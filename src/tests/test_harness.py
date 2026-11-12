import copy
import json
import pathlib
import sqlite3

import pytest

from fenced_retry.controller.batch import BatchHalted, run_batch
from fenced_retry.envelope.approval import approve
from fenced_retry.harness.conditions import run_naive_retry, run_single_shot
from fenced_retry.harness.config import ConfigError, PilotConfig, validate
from fenced_retry.harness.report import summarize_run, write_run_report
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


class Res:
    def __init__(self, status="ok", text=None, call_id="c"):
        self.status, self.text, self.call_id = status, text, call_id
        self.latency_ms = 10.0
        self.input_tokens = 5
        self.output_tokens = 2


class Fake:
    def __init__(self, results, crash_on=None):
        self.results = list(results)
        self.calls = []
        self.crash_on = crash_on

    def chat(self, messages, project_name=None, stage="executor", **kwargs):
        self.calls.append(messages)
        if self.crash_on is not None and len(self.calls) == self.crash_on:
            raise RuntimeError("simulated crash")
        return self.results.pop(0) if self.results else Res(status="error")


def _ok():
    return Res(text=json.dumps(GOOD))


def _bad():
    return Res(text="{}")


def _env(tmp_path):
    env_path = tmp_path / "envelope.json"
    env_path.write_text(SHIPPED.read_text(encoding="utf-8"), encoding="utf-8")
    approve(env_path, approver="Tester", note="test")
    return env_path


def _env_dict():
    return json.loads(SHIPPED.read_text(encoding="utf-8"))


def test_single_shot_accepts_with_one_call(tmp_path):
    caller = Fake([_ok()])
    out = run_single_shot(record_id="s1", input_text=INPUT, env=_env_dict(), caller=caller)
    assert out.state == "ACCEPT"
    assert len(caller.calls) == 1


def test_single_shot_dead_letters_after_one_failed_call(tmp_path):
    caller = Fake([_bad(), _ok()])
    out = run_single_shot(record_id="s2", input_text=INPUT, env=_env_dict(), caller=caller)
    assert out.state == "DEAD_LETTER"
    assert out.reason.startswith("single_shot")
    assert len(caller.calls) == 1


def test_naive_retry_resends_same_prompt_up_to_limit():
    env = _env_dict()
    caller = Fake([_bad(), _bad(), _bad()])
    out = run_naive_retry(record_id="n1", input_text=INPUT, env=env, caller=caller)
    assert out.state == "DEAD_LETTER"
    assert len(caller.calls) == env["theta"]["N_max"]
    assert all(c == caller.calls[0] for c in caller.calls)


def test_naive_retry_accepts_on_a_later_attempt():
    caller = Fake([_bad(), _ok()])
    out = run_naive_retry(record_id="n2", input_text=INPUT, env=_env_dict(), caller=caller)
    assert out.state == "ACCEPT"
    assert len(caller.calls) == 2


def test_crash_resume_skips_accepted_and_restarts_in_progress(tmp_path):
    env_path = _env(tmp_path)
    store = RunStore(tmp_path / "run.sqlite")
    records = [("r1", INPUT), ("r2", INPUT), ("r3", INPUT)]

    crashing = Fake([_ok(), _ok(), _ok()], crash_on=2)  # second call crashes
    with pytest.raises(RuntimeError):
        run_batch(env_path=env_path, records=records, caller=crashing, store=store, run_id="resume")

    resumed = Fake([_ok(), _ok(), _ok()])
    run_batch(env_path=env_path, records=records, caller=resumed, store=store, run_id="resume")

    assert len(resumed.calls) == 2  # r2 restarted, r3 processed; r1 not called again
    states = store.record_states("resume")
    assert states == {"r1": "ACCEPT", "r2": "ACCEPT", "r3": "ACCEPT"}
    events = [e for (e,) in store._conn.execute("SELECT event FROM events WHERE run_id = 'resume'")]
    assert any(e.startswith("skipped: already ACCEPT") for e in events)
    assert any(e.startswith("restarted from first attempt") for e in events)
    store.close()


def test_baseline_condition_runs_through_batch(tmp_path):
    env_path = _env(tmp_path)
    store = RunStore(tmp_path / "base.sqlite")
    outcomes = run_batch(env_path=env_path, records=[("b1", INPUT)], caller=Fake([_ok()]),
                         store=store, run_id="base", condition="single_shot")
    assert outcomes[0].state == "ACCEPT"
    store.close()


def test_report_summarises_run(tmp_path):
    env_path = _env(tmp_path)
    db = tmp_path / "rep.sqlite"
    store = RunStore(db)
    run_batch(env_path=env_path, records=[("a", INPUT), ("b", INPUT)],
              caller=Fake([_ok(), _ok()]), store=store, run_id="rep")
    store.close()
    summary = summarize_run(db)
    assert summary["records"] == 2
    assert summary["accepted"] == 2
    assert summary["accept_rate"] == 1.0
    out = write_run_report(db, tmp_path / "reports")
    assert (tmp_path / "reports" / "rep_summary.md").exists()
    assert (tmp_path / "reports" / "rep_records.csv").exists()
    assert out["calls"] == 2


def test_config_rejects_test_split_without_final():
    cfg = PilotConfig(name="p", envelope=SHIPPED, data=ROOT, split="test", offset=0, limit=10,
                      conditions=("controller",), seeds=(0,), final=False)
    with pytest.raises(ConfigError):
        validate(cfg, envelope_seeds=[0, 1, 2])


def test_config_rejects_unknown_condition_and_unapproved_seed():
    base = dict(name="p", envelope=SHIPPED, data=ROOT, split="train", offset=0, limit=10)
    with pytest.raises(ConfigError):
        validate(PilotConfig(conditions=("magic",), seeds=(0,), **base), envelope_seeds=[0, 1, 2])
    with pytest.raises(ConfigError):
        validate(PilotConfig(conditions=("controller",), seeds=(9,), **base), envelope_seeds=[0, 1, 2])


def test_batch_halts_on_changed_envelope(tmp_path):
    env_path = _env(tmp_path)
    data = json.loads(env_path.read_text(encoding="utf-8"))
    data["theta"]["N_max"] = 9
    env_path.write_text(json.dumps(data), encoding="utf-8")
    store = RunStore(tmp_path / "h.sqlite")
    with pytest.raises(BatchHalted):
        run_batch(env_path=env_path, records=[("x", INPUT)], caller=Fake([_ok()]), store=store, run_id="h")
    store.close()
