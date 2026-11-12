import sqlite3
import uuid

from fenced_retry.llm.usage import DEFAULT_PROJECT, UsageTracker, new_call_id, normalise_project, sha256_text


def _row(**overrides):
    row = {
        "call_id": new_call_id(),
        "model": "qwen3:8b",
        "endpoint": "http://localhost:11434",
        "num_ctx": 4096,
        "num_predict": 512,
        "temperature": 0.0,
        "seed": 0,
        "think": 0,
        "prompt_hash": "p",
        "status": "ok",
        "store_full_text": 0,
        "input_tokens": 10,
        "output_tokens": 3,
        "latency_ms": 100.0,
    }
    row.update(overrides)
    return row


def test_call_id_is_uuid4():
    assert uuid.UUID(new_call_id()).version == 4


def test_blank_or_missing_project_becomes_default():
    assert normalise_project(None) == DEFAULT_PROJECT == "unassigned"
    assert normalise_project("") == "unassigned"
    assert normalise_project("   ") == "unassigned"
    assert normalise_project(" pilot ") == "pilot"


def test_sha256_is_stable_and_none_safe():
    assert sha256_text("abc") == sha256_text("abc")
    assert len(sha256_text("abc")) == 64
    assert sha256_text(None) is None


def test_record_writes_row_with_date_and_default_project(tmp_path):
    db = tmp_path / "usage.sqlite"
    tracker = UsageTracker(db)
    tracker.record(_row(project_name=None))
    tracker.close()
    conn = sqlite3.connect(db)
    project, date_created, output_text = conn.execute(
        "SELECT project_name, date_created, output_text FROM llm_calls"
    ).fetchone()
    conn.close()
    assert project == "unassigned"
    assert date_created and "T" in date_created
    assert output_text is None


def test_totals_sum_rows(tmp_path):
    tracker = UsageTracker(tmp_path / "usage.sqlite")
    tracker.record(_row(input_tokens=10, output_tokens=2, latency_ms=50.0))
    tracker.record(_row(input_tokens=5, output_tokens=1, latency_ms=25.0))
    totals = tracker.totals()
    tracker.close()
    assert totals["calls"] == 2
    assert totals["input_tokens"] == 15
    assert totals["output_tokens"] == 3
    assert totals["latency_ms"] == 75.0


def test_shared_file_keeps_rows_across_trackers(tmp_path):
    db = tmp_path / "usage.sqlite"
    a = UsageTracker(db)
    a.record(_row(project_name="one"))
    a.close()
    b = UsageTracker(db)
    b.record(_row(project_name="two"))
    assert b.totals()["calls"] == 2
    b.close()
