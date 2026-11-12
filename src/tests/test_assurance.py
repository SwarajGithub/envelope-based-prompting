"""M7 tests: statistics, sampler, breaker, review session, and the breaker in run_batch.

Simulated batches with known rates are used where the expected result is known.
"""

import random
import pathlib

import pytest

from fenced_retry.assurance.breaker import evaluate, make_fleet_check
from fenced_retry.assurance.review import review_session
from fenced_retry.assurance.sampler import cluster_of, full_blocks, plan_samples, sample_block
from fenced_retry.assurance.stats import (
    binom_sf,
    clopper_pearson_lower,
    phi_from_null,
    required_clusters,
)
from fenced_retry.adapters.cord import Record
from fenced_retry.controller.batch import BatchHalted, run_batch
from fenced_retry.controller.types import Attempt
from fenced_retry.store.run_db import RunStore

ROOT = pathlib.Path(__file__).resolve().parents[1]
SHIPPED = ROOT / "envelopes" / "receipt-extract-v1.json"
INPUT = "Tea 5,000\nSubtotal 5,000\nTotal 5,500\nCash 6,000\nChange 500"


# ---------- statistics ----------

def test_binom_sf_matches_direct_sum():
    assert binom_sf(0, 1, 0.3) == pytest.approx(0.3)  # P(X > 0) for one trial
    assert binom_sf(10, 10, 0.5) == 0.0


def test_phi_is_smallest_threshold_within_alpha():
    k, phi = phi_from_null(50, 0.06, 0.05)
    assert binom_sf(k, 50, 0.06) <= 0.05
    assert binom_sf(k - 1, 50, 0.06) > 0.05
    assert phi == pytest.approx(k / 50)


def test_phi_rejects_bad_inputs():
    with pytest.raises(ValueError):
        phi_from_null(50, 1.5, 0.05)


def test_clopper_pearson_known_values():
    assert clopper_pearson_lower(10, 10, 0.95) == pytest.approx(0.05 ** 0.1, abs=1e-6)
    assert clopper_pearson_lower(0, 10, 0.95) == 0.0
    assert clopper_pearson_lower(30, 100, 0.95) == pytest.approx(0.2249, abs=1e-3)


def test_required_clusters_rule():
    assert required_clusters(0.05, 0.2) == 14  # ceil(ln 0.05 / ln 0.8)


# ---------- sampler ----------

def test_full_blocks_drop_partial_tail_unless_asked():
    assert len(full_blocks([f"r{i}" for i in range(25)], 10)) == 2
    tail = full_blocks([f"r{i}" for i in range(25)], 10, include_tail=True)
    assert [len(b) for b in tail] == [10, 10, 5]


def test_cluster_of_uses_last_failing_attempt_and_none_when_clean():
    assert cluster_of([]) == "none"
    assert cluster_of([["TOTAL_NOT_VALID"], ["MENU_ITEM_NOT_VERBATIM"]]) == "menu"
    assert cluster_of([["SCHEMA_MISSING_FIELD"], []]) == "schema"


def test_sample_block_takes_c_clusters_and_j_per_cluster():
    items = [(f"r{i}", f"g{i % 5}") for i in range(40)]  # 5 clusters of 8
    picked = sample_block(items, c=3, j=2, rng=random.Random(1))
    assert len(picked) == 6
    assert len({cl for _, cl in picked}) == 3


def test_sample_block_tops_up_to_floor_when_one_cluster():
    items = [(f"r{i:02d}", "none") for i in range(40)]  # one cluster, as for first-attempt accepts
    picked = sample_block(items, c=14, j=2, rng=random.Random(3), n_min=14)
    assert len(picked) == 14 and len({rid for rid, _ in picked}) == 14


def test_sample_block_takes_everything_when_few_clusters():
    items = [("a", "x"), ("b", "y")]
    assert sorted(sample_block(items, c=14, j=2, rng=random.Random(0))) == sorted(items)


def _store(tmp_path):
    return RunStore(tmp_path / "run.sqlite")


def _accept(store, run, rid, failing_lists=()):
    for i, failing in enumerate(failing_lists, start=1):
        store.add_attempt(run, rid, "A", Attempt(index=i, plan_id="A", call_id=f"c{i}", status="ok",
                                                 failing=frozenset(failing), signature=None,
                                                 output_text="{}"))
    idx = len(failing_lists) + 1
    store.add_attempt(run, rid, "A", Attempt(index=idx, plan_id="A", call_id="cz", status="ok",
                                             failing=frozenset(), signature=None,
                                             output_text=f'{{"id": "{rid}"}}'))
    store.record_state(run, rid, "ACCEPT", "A", idx, "accepted")


def test_plan_samples_only_closed_blocks_and_is_idempotent(tmp_path):
    store = _store(tmp_path)
    store.start_run("r", "h", False)
    for i in range(25):
        _accept(store, "r", f"r{i:03d}", [["TOTAL_NOT_VALID"]] if i % 2 else [])
    first = plan_samples(store, "r", w_a=10, c=2, j=1, include_tail=False)
    assert {s.block_index for s in first} == {0, 1}  # tail of 5 not sampled without include_tail
    tail = plan_samples(store, "r", w_a=10, c=2, j=1)  # include_tail default: D-060
    assert {s.block_index for s in tail} == {0, 1, 2}
    again = plan_samples(store, "r", w_a=10, c=2, j=1)
    assert {(s.record_id, s.cluster) for s in tail} == {(s.record_id, s.cluster) for s in again}
    assert len(store.pending_sample_ids("r")) == len(tail)
    store.close()


# ---------- breaker ----------

def _dead(store, run, rid):
    store.add_attempt(run, rid, "A", Attempt(index=1, plan_id="A", call_id="c", status="ok",
                                             failing=frozenset({"TOTAL_NOT_VALID"}), signature="s",
                                             output_text="{}"))
    store.record_state(run, rid, "DEAD_LETTER", "A", 1, "fail")


def test_dead_letter_arm_trips_only_on_full_window(tmp_path):
    store = _store(tmp_path)
    store.start_run("r", "h", False)
    for i in range(4):
        _dead(store, "r", f"d{i}")
    st = evaluate(store, "r", w_d=5, phi=0.5, phi_s=None, gamma=0.95)
    assert not st.dl_full and not st.open  # window not yet full
    _dead(store, "r", "d4")
    st = evaluate(store, "r", w_d=5, phi=0.5, phi_s=None, gamma=0.95)
    assert st.dl_full and st.dl_fraction == 1.0 and st.open
    store.close()


def test_dead_letter_arm_below_threshold_stays_closed(tmp_path):
    store = _store(tmp_path)
    store.start_run("r", "h", False)
    for i in range(50):
        _accept(store, "r", f"a{i:03d}")
    for i in range(3):
        _dead(store, "r", f"d{i}")
    st = evaluate(store, "r", w_d=50, phi=0.12, phi_s=None, gamma=0.95)
    assert st.dl_full and st.dl_fraction == pytest.approx(0.06)
    assert not st.open
    store.close()


def test_silent_arm_trips_on_lcb_not_point_estimate(tmp_path):
    store = _store(tmp_path)
    store.start_run("r", "h", False)
    # 5 incorrect of 10 reviewed: point estimate 0.5, LCB at 0.95 well below 0.5
    for i in range(10):
        _accept(store, "r", f"s{i}")
        store.add_sample("r", f"s{i}", 0, "none")
        store.add_review("r", f"s{i}", "incorrect" if i < 5 else "correct", 1.0)
    st = evaluate(store, "r", w_d=5, phi=None, phi_s=0.3, gamma=0.95)
    assert st.silent_lcb is not None and st.silent_lcb < 0.3  # LCB, not the point estimate 0.5
    assert not st.open
    st2 = evaluate(store, "r", w_d=5, phi=None, phi_s=0.1, gamma=0.95)
    assert st2.open
    store.close()


def test_observational_breaker_never_opens(tmp_path):
    store = _store(tmp_path)
    store.start_run("r", "h", False)
    for i in range(10):
        _dead(store, "r", f"d{i}")
    st = evaluate(store, "r", w_d=5, phi=None, phi_s=None, gamma=0.95)
    assert st.dl_fraction == 1.0 and not st.open
    store.close()


def test_simulated_batch_false_pause_rate_matches_alpha():
    """Null model: 6% dead-letter rate. Calibrated phi should give a false pause in at most ~alpha."""
    k, phi = phi_from_null(50, 0.06, 0.05)
    rng = random.Random(7)
    trials, trips = 4000, 0
    for _ in range(trials):
        dead = sum(rng.random() < 0.06 for _ in range(50))
        trips += dead / 50 > phi
    assert trips / trials <= 0.05 + 0.02  # Monte Carlo slack


# ---------- review session ----------

def test_review_records_verdicts_and_time_and_skips(tmp_path):
    store = _store(tmp_path)
    store.start_run("r", "h", False)
    for rid in ("a", "b", "c"):
        _accept(store, "r", rid)
        store.add_sample("r", rid, 0, "none")
    recs = {rid: Record(rid, "train", INPUT, {"total_price": "5,500"}) for rid in ("a", "b", "c")}
    answers = iter(["c", "i", "s"])
    ticks = iter([0.0, 90.0, 100.0, 220.0, 300.0, 330.0])
    shown = []
    n = review_session(store, "r", recs, input_fn=lambda _p: next(answers),
                       print_fn=shown.append, clock=lambda: next(ticks))
    assert n == 2
    rows = {r["record_id"]: r for r in store.reviews("r")}
    assert rows["a"]["verdict"] == "correct" and rows["a"]["minutes"] == pytest.approx(1.5)
    assert rows["b"]["verdict"] == "incorrect" and rows["b"]["minutes"] == pytest.approx(2.0)
    assert "c" not in rows  # skipped stays pending
    assert store.pending_sample_ids("r") == ["c"]
    assert any("REFERENCE" in line for line in shown)
    store.close()


def test_review_reprompts_on_bad_answer_and_quits(tmp_path):
    store = _store(tmp_path)
    store.start_run("r", "h", False)
    _accept(store, "r", "a")
    store.add_sample("r", "a", 0, "none")
    recs = {"a": Record("a", "train", INPUT, {})}
    answers = iter(["yes", "q"])
    n = review_session(store, "r", recs, input_fn=lambda _p: next(answers),
                       print_fn=lambda _m: None, clock=lambda: 0.0)
    assert n == 0 and store.pending_sample_ids("r") == ["a"]
    store.close()


# ---------- breaker inside run_batch ----------

class Garbage:
    """Model stub that always returns invalid output, so every record dead-letters."""

    def chat(self, messages, project_name=None, stage="executor", **kwargs):
        class R:
            status = "ok"
            text = "not json"
            call_id = "c"
            latency_ms = 1.0
            input_tokens = 1
            output_tokens = 1
        return R()

    def close(self):
        pass


def test_run_batch_pauses_then_needs_decision(tmp_path):
    store = RunStore(tmp_path / "pause.sqlite")
    records = [(f"g{i}", INPUT) for i in range(6)]
    check = make_fleet_check(w_d=3, phi=0.5, phi_s=None, gamma=0.95)
    out = run_batch(env_path=SHIPPED, records=records, caller=Garbage(), store=store,
                    run_id="p", condition="single_shot", breaker=check)
    assert len(out) == 3  # window of 3 full of dead letters pauses after record 3
    assert store.last_breaker_open("p")
    with pytest.raises(BatchHalted, match="decide"):
        run_batch(env_path=SHIPPED, records=records, caller=Garbage(), store=store,
                  run_id="p", condition="single_shot", breaker=check)
    store.add_breaker_event("p", "decision", "reviewed, resume")
    out2 = run_batch(env_path=SHIPPED, records=records, caller=Garbage(), store=store,
                     run_id="p", condition="single_shot", breaker=check)
    assert len(out2) == 1  # one new dead letter re-opens the window at once
    assert store.last_breaker_open("p")
    store.close()


def test_run_batch_without_breaker_is_unchanged(tmp_path):
    store = RunStore(tmp_path / "nobreak.sqlite")
    records = [(f"n{i}", INPUT) for i in range(4)]
    out = run_batch(env_path=SHIPPED, records=records, caller=Garbage(), store=store,
                    run_id="n", condition="single_shot")
    assert len(out) == 4
    store.close()


# ---------- reference judge (D-061) ----------

from fenced_retry.assurance.judge import judge_samples, mismatches


def test_judge_flags_null_output_where_reference_has_value():
    ref = {"tax_price": "52,815", "total_price": "580,965", "menu": []}
    out = {"tax_price": None, "total_price": "580,965", "menu": []}
    diffs = mismatches(out, ref)
    assert len(diffs) == 1 and diffs[0].startswith("tax_price")


def test_judge_accepts_equivalent_number_formats():
    ref = {"total_price": "40.000,00", "menu": [{"nm": "Tea", "cnt": "1", "price": "40,000"}]}
    out = {"total_price": "40000", "menu": [{"nm": "TEA", "cnt": "1", "price": "40000"}]}
    assert mismatches(out, ref) == []


def test_judge_reports_menu_differences():
    ref = {"menu": [{"nm": "Tea", "cnt": "1", "price": "5,000"}]}
    out = {"menu": [{"nm": "Tea", "cnt": "2", "price": "5,000"}]}
    diffs = mismatches(out, ref)
    assert any("menu missing" in d for d in diffs) and any("menu extra" in d for d in diffs)


def test_judge_samples_writes_reference_reviews(tmp_path):
    store = _store(tmp_path)
    store.start_run("r", "h", False)
    _accept(store, "r", "a")
    store.add_sample("r", "a", 0, "none")
    store.add_review  # present
    out_text = '{"total_price": "5,500", "subtotal_price": null, "menu": []}'
    store.add_attempt("r", "a", "A", Attempt(index=9, plan_id="A", call_id="z", status="ok",
                                             failing=frozenset(), signature=None, output_text=out_text))
    recs = {"a": Record("a", "train", INPUT, {"total_price": "5,500", "subtotal_price": "5,000", "menu": []})}
    counts = judge_samples(store, "r", recs, print_fn=lambda _m: None)
    assert counts == {"correct": 0, "incorrect": 1, "no_reference": 0}
    row = store.reviews("r")[0]
    assert row["reviewer"] == "reference" and row["verdict"] == "incorrect"
    assert "subtotal_price" in row["note"]
    assert judge_samples(store, "r", recs, print_fn=lambda _m: None) == {"correct": 0, "incorrect": 0, "no_reference": 0}
    store.close()
