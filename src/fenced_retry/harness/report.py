"""Run reports: CSV of records and attempts, Markdown summary (HLD §13)."""

from __future__ import annotations

import csv
import json
import sqlite3
import statistics
from pathlib import Path
from typing import Any


def summarize_run(db_path: Path) -> dict[str, Any]:
    conn = sqlite3.connect(db_path)
    try:
        run_id, envelope_hash, shadow = conn.execute("SELECT run_id, envelope_hash, shadow FROM runs").fetchone()
        states = dict(conn.execute("SELECT state, COUNT(*) FROM records GROUP BY state").fetchall())
        attempts = conn.execute(
            "SELECT record_id, attempt_index, status, failing_json, latency_ms, input_tokens, output_tokens FROM attempts"
        ).fetchall()
        per_record = conn.execute(
            "SELECT record_id, COUNT(*) FROM attempts GROUP BY record_id"
        ).fetchall()
        first_try = conn.execute(
            "SELECT COUNT(*) FROM attempts WHERE attempt_index = 1 AND failing_json = '[]'"
        ).fetchone()[0]
    finally:
        conn.close()
    total = sum(states.values())
    accepted = states.get("ACCEPT", 0)
    latencies = [a[4] for a in attempts if a[4] is not None]
    failing: dict[str, int] = {}
    for a in attempts:
        for check in json.loads(a[3]):
            failing[check] = failing.get(check, 0) + 1
    return {
        "run_id": run_id,
        "envelope_hash": envelope_hash,
        "shadow": bool(shadow),
        "records": total,
        "accepted": accepted,
        "dead_lettered": states.get("DEAD_LETTER", 0),
        "accept_rate": (accepted / total) if total else None,
        "first_attempt_accepts": first_try,
        "calls": len(attempts),
        "mean_calls_per_record": (sum(c for _, c in per_record) / len(per_record)) if per_record else None,
        "median_latency_ms": statistics.median(latencies) if latencies else None,
        "input_tokens": sum(a[5] or 0 for a in attempts),
        "output_tokens": sum(a[6] or 0 for a in attempts),
        "failing_checks": failing,
    }


def write_run_report(db_path: Path, out_dir: Path) -> dict[str, Any]:
    summary = summarize_run(db_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = db_path.stem
    conn = sqlite3.connect(db_path)
    try:
        records = conn.execute(
            "SELECT record_id, state, plan_id, attempt_index, reason FROM records ORDER BY record_id"
        ).fetchall()
        attempts = conn.execute(
            "SELECT record_id, attempt_index, plan_id, status, failing_json, signature, tier, latency_ms, "
            "input_tokens, output_tokens FROM attempts ORDER BY record_id, attempt_index"
        ).fetchall()
    finally:
        conn.close()
    with (out_dir / f"{stem}_records.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["record_id", "state", "plan_id", "attempt_index", "reason"])
        w.writerows(records)
    with (out_dir / f"{stem}_attempts.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["record_id", "attempt_index", "plan_id", "status", "failing", "signature", "tier",
                    "latency_ms", "input_tokens", "output_tokens"])
        w.writerows(attempts)
    lines = [
        f"# Run {summary['run_id']}",
        "",
        f"- Envelope hash: `{summary['envelope_hash']}`",
        f"- Shadow: {summary['shadow']}",
        f"- Records: {summary['records']}, accepted: {summary['accepted']}, dead-lettered: {summary['dead_lettered']}",
        f"- Accept rate: {summary['accept_rate']:.3f}" if summary["accept_rate"] is not None else "- Accept rate: n/a",
        f"- First-attempt accepts: {summary['first_attempt_accepts']}",
        f"- Calls: {summary['calls']}, mean calls per record: {summary['mean_calls_per_record']}",
        f"- Median latency (ms): {summary['median_latency_ms']}",
        f"- Tokens: {summary['input_tokens']} in, {summary['output_tokens']} out",
        "",
        "Failing checks (count across attempts):",
        "",
    ]
    for check, count in sorted(summary["failing_checks"].items(), key=lambda kv: -kv[1]):
        lines.append(f"- {check}: {count}")
    (out_dir / f"{stem}_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def summarize_all(runs_dir: Path, out_dir: Path) -> list[dict[str, Any]]:
    rows = []
    for db in sorted(runs_dir.glob("*.sqlite")):
        if db.name in {"usage.sqlite"}:
            continue
        try:
            rows.append(summarize_run(db))
        except (sqlite3.Error, ValueError, TypeError):
            continue
    out_dir.mkdir(parents=True, exist_ok=True)
    fields = ["run_id", "shadow", "records", "accepted", "dead_lettered", "accept_rate",
              "first_attempt_accepts", "calls", "mean_calls_per_record", "median_latency_ms",
              "input_tokens", "output_tokens"]
    with (out_dir / "summary.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return rows
