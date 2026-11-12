"""M8 tests: metrics on a simulated run with known values, and the frozen-strategy baseline."""

import json
import pathlib

import pytest

from fenced_retry.controller.batch import run_batch
from fenced_retry.controller.types import Attempt
from fenced_retry.harness.metrics import compute
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


def _att(i, failing=(), tier=None, sig=None, tokens=10, text="{}"):
    return Attempt(index=i, plan_id="A", call_id=f"c{i}", status="ok", failing=frozenset(failing),
                   signature=sig, output_text=text, tier=tier, latency_ms=100.0,
                   input_tokens=tokens, output_tokens=2)


def test_metrics_on_known_run(tmp_path):
    store = RunStore(tmp_path / "m.sqlite")
    run = "m"
    store.start_run(run, "h", False)
    # four accepted (three first try, one on attempt 2), one dead letter
    for rid in ("a", "b", "c"):
        store.add_attempt(run, rid, "A", _att(1))
        store.record_state(run, rid, "ACCEPT", "A", 1, "accepted")
    store.add_attempt(run, "d", "A", _att(1, ["TOTAL_NOT_VALID"], sig="s1"))
    store.add_attempt(run, "d", "A", _att(2))
    store.record_state(run, "d", "ACCEPT", "A", 2, "accepted")
    store.add_attempt(run, "e", "A", _att(1, ["SCHEMA_MISSING_FIELD"], sig="s2"))
    store.record_state(run, "e", "DEAD_LETTER", "A", 1, "fuse:repetition; fallback menu exhausted")
    # two samples, one human review (minutes 2), one reference review
    store.add_sample(run, "a", 0, "none")
    store.add_sample(run, "d", 0, "amount:total_price")
    store.add_review(run, "a", "incorrect", 2.0, reviewer="human")
    store.add_review(run, "d", "correct", 0.0, reviewer="reference")
    store.add_breaker_event(run, "open", "test")
    store.close()

    out = compute(tmp_path / "m.sqlite")
    assert out["records"] == 5 and out["accepted"] == 4 and out["dead_letters"] == 1
    assert out["accept_rate"] == pytest.approx(0.8)
    assert out["rescued_after_first_attempt"] == 1
    assert out["u_unresolved"] == pytest.approx(0.2)
    assert out["s_sample_rate"] == pytest.approx(0.5)          # 2 samples / 4 accepted
    assert out["h_oversight"] == pytest.approx(0.2 + 0.5 * 0.8)  # u + s(1 - u)
    assert out["containment_stops_by_tier"] == {"repetition": 1}
    assert out["max_signature_repeat_histogram"] == {1: 2}
    assert out["position"][1]["pass_rate"] == pytest.approx(3 / 5)
    assert out["position"][2]["attempts"] == 1
    assert out["silent_failure"]["human_reviewed"] == 1
    assert out["silent_failure"]["human_incorrect"] == 1
    assert out["silent_failure"]["rev_e_minutes_mean"] == pytest.approx(2.0)
    assert out["silent_failure"]["reference_judged"] == 1
    assert out["silent_failure"]["reference_incorrect"] == 0
    assert out["breaker_pauses"] == 1
    assert "fuse_precision_recall" in out["not_computed"]


class Scripted:
    """Returns the scripted texts in order; the last one repeats."""

    def __init__(self, texts):
        self.texts = list(texts)
        self.n = 0

    def chat(self, messages, project_name=None, stage="executor", **kwargs):
        text = self.texts[min(self.n, len(self.texts) - 1)]
        self.n += 1

        class R:
            status = "ok"
            call_id = f"c{self.n}"
            latency_ms = 1.0
            input_tokens = 5
            output_tokens = 2
        R.text = text
        return R()

    def close(self):
        pass


def test_frozen_keeps_plan_a_and_still_accepts_on_retry(tmp_path):
    store = RunStore(tmp_path / "f.sqlite")
    out = run_batch(env_path=SHIPPED, records=[("r1", INPUT)],
                    caller=Scripted(["not json", json.dumps(GOOD)]), store=store,
                    run_id="f", condition="frozen")
    assert out[0].state == "ACCEPT"
    plans = {r[0] for r in store._conn.execute("SELECT plan_id FROM attempts WHERE run_id='f'")}
    assert plans == {"A"}
    store.close()


def test_frozen_stops_on_repetition_with_fallback_exhausted(tmp_path):
    store = RunStore(tmp_path / "g.sqlite")
    out = run_batch(env_path=SHIPPED, records=[("r1", INPUT)],
                    caller=Scripted(['{"total_price": "9"}']), store=store,
                    run_id="g", condition="frozen")
    assert out[0].state == "DEAD_LETTER"
    store.close()


def test_old_reviews_table_is_migrated_and_keeps_rows(tmp_path):
    import sqlite3

    path = tmp_path / "old.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE reviews (run_id TEXT NOT NULL, record_id TEXT NOT NULL, verdict TEXT NOT NULL,
                              minutes REAL NOT NULL, date_created TEXT NOT NULL,
                              PRIMARY KEY (run_id, record_id));
        INSERT INTO reviews VALUES ('r', 'a', 'correct', 0.5, '2026-10-18');
    """)
    conn.commit()
    conn.close()
    store = RunStore(path)
    rows = store.reviews("r")
    assert rows == [{"record_id": "a", "verdict": "correct", "minutes": 0.5,
                     "reviewer": "human", "note": "", "date_created": "2026-10-18"}]
    # the same record can now hold a human and a reference verdict
    store.add_review("r", "a", "incorrect", 0.0, reviewer="reference", note="x")
    assert len(store.reviews("r")) == 2
    assert store.review_counts("r") == (0, 1)  # human only
    store.close()
