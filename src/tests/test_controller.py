import copy
import json
import pathlib
import shutil
from dataclasses import dataclass

import pytest

from fenced_retry.controller.batch import BatchHalted, run_batch
from fenced_retry.controller.compiler import choose_next
from fenced_retry.controller.fuse import fuse_check, signature
from fenced_retry.controller.ledger import Ledger
from fenced_retry.controller.loop import parse_output, run_record
from fenced_retry.envelope.approval import approve
from fenced_retry.envelope.model import load_envelope
from fenced_retry.store.run_db import RunStore

ROOT = pathlib.Path(__file__).resolve().parents[1]
SHIPPED = ROOT / "envelopes" / "receipt-extract-v1.json"
INPUT = "Tea 5,000\nSubtotal 5,000\nTotal 5,500\nCash 6,000\nChange 500"
GOOD = {
    "total_price": "5,500", "subtotal_price": "5,000", "tax_price": None, "service_price": None,
    "discount_price": None, "cashprice": "6,000", "changeprice": "500",
    "creditcardprice": None, "emoneyprice": None,
    "menu": [{"nm": "Tea", "cnt": "1", "price": "5,000"}],
}


@dataclass
class Res:
    status: str = "ok"
    text: str | None = None
    call_id: str = "c"


class Fake:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def chat(self, messages, project_name=None, stage="executor", **kwargs):
        self.calls.append(messages)
        if self.results:
            return self.results.pop(0)
        return Res(status="error")


def _env(**theta):
    env = load_envelope(SHIPPED)
    env = copy.deepcopy(env)
    env["theta"].update(theta)
    return env


def _ok():
    return Res(text=json.dumps(GOOD))


def _empty():
    return Res(text="{}")


# ----- helpers ---------------------------------------------------------

def test_parse_output_handles_fences_and_rejects_non_objects():
    assert parse_output('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_output("[1,2]") is None
    assert parse_output("not json") is None
    assert parse_output(None) is None


def test_signature_depends_only_on_sorted_ids():
    assert signature({"B", "A"}) == signature(["A", "B"])
    assert signature({"A"}) != signature({"B"})


def test_fuse_tiers_each_trip_alone():
    base = dict(index=1, elapsed_s=0.0, last_signature=None, signature_counts={},
                plan_repeat=0, N_max=3, t_max_s=300.0, m=2, envelope_ok=True, prompt_ok=True)
    assert fuse_check(**base) is None
    assert fuse_check(**{**base, "index": 4}) == "limit"
    assert fuse_check(**{**base, "elapsed_s": 301.0}) == "limit"
    assert fuse_check(**{**base, "last_signature": "s", "signature_counts": {"s": 2}}) == "repetition"
    assert fuse_check(**{**base, "plan_repeat": 2}) == "repetition"
    assert fuse_check(**{**base, "envelope_ok": False}) == "envelope"
    assert fuse_check(**{**base, "prompt_ok": False}) == "envelope"


def test_compiler_follows_table_and_respects_repeat_limit():
    assert choose_next(current_plan="A", failing_groups={"amount:total_price"},
                       plan_order=["A", "B", "C"], repeat_counts={}, m=2)[0] == "B"
    assert choose_next(current_plan="A", failing_groups={"menu"},
                       plan_order=["A", "B", "C"], repeat_counts={}, m=2)[0] == "C"
    assert choose_next(current_plan="A", failing_groups={"schema"},
                       plan_order=["A", "B", "C"], repeat_counts={"A": 1}, m=2)[0] == "A"
    # B already used m times: first approved plan below the limit is used
    assert choose_next(current_plan="A", failing_groups={"amount:total_price"},
                       plan_order=["A", "B", "C"], repeat_counts={"B": 2}, m=2)[0] == "A"


def test_ledger_one_directive_per_group_and_replacement():
    ledger = Ledger()
    ledger.update("schema", "schema")
    ledger.update("schema", "schema")
    ledger.update("menu", "menu")
    templates = {"schema": "S text", "menu": "M text"}
    assert len(ledger) == 2
    assert ledger.render(templates) == ["- M text", "- S text"]


# ----- main loop --------------------------------------------------------

def test_accepts_valid_output_on_first_attempt():
    caller = Fake([_ok()])
    out = run_record(record_id="r1", input_text=INPUT, env=_env(), caller=caller)
    assert out.state == "ACCEPT"
    assert out.accepted_for_use
    assert len(caller.calls) == 1


def test_repetition_trace_appendix_a4_with_m_equal_two():
    # Same schema failure twice on plan A: the signature count reaches m, so the
    # repetition tier opens before attempt 3. Fallback then dead-letters the record.
    caller = Fake([_empty(), _empty(), _empty()])
    out = run_record(record_id="r2", input_text=INPUT, env=_env(m=2), caller=caller)
    assert out.state == "DEAD_LETTER"
    assert "fuse:repetition" in out.reason
    assert len(caller.calls) == 2
    assert [a.plan_id for a in out.attempts] == ["A", "A"]


def test_plan_repeat_tier_with_m_one_stops_before_second_attempt():
    caller = Fake([_empty(), _empty()])
    out = run_record(record_id="r3", input_text=INPUT, env=_env(m=1), caller=caller)
    assert out.state == "DEAD_LETTER"
    assert "fuse:repetition" in out.reason
    assert len(caller.calls) == 1


def test_appendix_a2_illegal_plan_opens_envelope_tier_without_call():
    def bad_compiler(**kwargs):
        return "retry_parse", "schema"  # not in P_approved

    caller = Fake([_empty()])
    out = run_record(record_id="r4", input_text=INPUT, env=_env(m=5), caller=caller,
                     compile_fn=bad_compiler)
    assert out.state == "DEAD_LETTER"
    assert "fuse:envelope" in out.reason
    assert len(caller.calls) == 1


def test_limit_tier_stops_after_n_max_attempts():
    caller = Fake([_empty(), _empty(), _empty(), _empty()])
    out = run_record(record_id="r5", input_text=INPUT, env=_env(N_max=2, m=99), caller=caller)
    assert out.state == "DEAD_LETTER"
    assert "fuse:limit" in out.reason
    assert len(caller.calls) == 2


def test_time_limit_stops_the_record():
    ticks = iter([0.0, 0.0, 1000.0, 1000.0])
    caller = Fake([_empty(), _empty(), _empty()])
    out = run_record(record_id="r6", input_text=INPUT, env=_env(m=99), caller=caller,
                     clock=lambda: next(ticks))
    assert "fuse:limit" in out.reason
    assert len(caller.calls) == 1


def test_call_bound_holds():
    # At most N_max executor calls; the deterministic compiler makes none; fallback makes none.
    caller = Fake([_empty()] * 10)
    env = _env(N_max=3, m=99)
    out = run_record(record_id="r7", input_text=INPUT, env=env, caller=caller)
    assert len(caller.calls) <= 2 * env["theta"]["N_max"] + env["theta"]["K"]


def test_timeout_is_a_failed_attempt_then_recovers():
    caller = Fake([Res(status="timeout"), _ok()])
    out = run_record(record_id="r8", input_text=INPUT, env=_env(), caller=caller)
    assert out.state == "ACCEPT"
    assert out.attempts[0].status == "timeout"
    assert "CALL_TIMEOUT" in out.attempts[0].failing
    assert out.ledger == {}  # no directive template for call failures


def test_shadow_outcome_is_not_for_use():
    caller = Fake([_ok()])
    out = run_record(record_id="r9", input_text=INPUT, env=_env(), caller=caller, shadow=True)
    assert out.state == "ACCEPT"
    assert not out.accepted_for_use


def test_fallback_never_reenters_main_loop():
    caller = Fake([_empty(), _empty(), _empty(), _empty()])
    run_record(record_id="r10", input_text=INPUT, env=_env(N_max=3, m=99), caller=caller)
    assert len(caller.calls) == 3  # limit tier at attempt 4; no further calls after fallback


# ----- store and batch --------------------------------------------------

def test_run_store_persists_states_attempts_and_dead_letters(tmp_path):
    store = RunStore(tmp_path / "run.sqlite")
    caller = Fake([_ok(), _empty(), _empty()])
    env_path = tmp_path / "envelope.json"
    env_path.write_text(SHIPPED.read_text(encoding="utf-8"), encoding="utf-8")
    approve(env_path, approver="Tester", note="test")
    outcomes = run_batch(
        env_path=env_path,
        records=[("ok-1", INPUT), ("bad-1", INPUT)],
        caller=caller,
        store=store,
        run_id="t1",
    )
    assert [o.state for o in outcomes] == ["ACCEPT", "DEAD_LETTER"]
    assert store.record_row("t1", "ok-1")["state"] == "ACCEPT"
    assert store.record_row("t1", "bad-1")["state"] == "DEAD_LETTER"
    assert store.count("dead_letters", "t1") == 1
    assert store.count("attempts", "t1") >= 3
    store.close()


def test_batch_halts_when_envelope_changes_after_approval(tmp_path):
    store = RunStore(tmp_path / "run.sqlite")
    env_path = tmp_path / "envelope.json"
    env_path.write_text(SHIPPED.read_text(encoding="utf-8"), encoding="utf-8")
    approve(env_path, approver="Tester", note="test")
    data = json.loads(env_path.read_text(encoding="utf-8"))
    data["theta"]["N_max"] = 9
    env_path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(BatchHalted):  # noqa
        run_batch(env_path=env_path, records=[("r", INPUT)], caller=Fake([_ok()]),
                  store=store, run_id="t2")
    store.close()
