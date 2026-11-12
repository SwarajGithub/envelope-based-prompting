"""M8 metrics for one run database (paper §9.3, §9.5). Read-only.

Each quantity is computed from the run database only. Quantities the database cannot
support are returned as None with a reason in "not_computed", never as an estimate.
"""

from __future__ import annotations

import re
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any


def _rows(conn: sqlite3.Connection, sql: str, args: tuple = ()) -> list[tuple]:
    return conn.execute(sql, args).fetchall()


def compute(db_path: Path) -> dict[str, Any]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        run_id = _rows(conn, "SELECT run_id FROM runs")[0][0]
        states = dict(_rows(conn, "SELECT state, COUNT(*) FROM records WHERE run_id=? GROUP BY state", (run_id,)))
        records = sum(states.values())
        accepted = states.get("ACCEPT", 0)
        dead = states.get("DEAD_LETTER", 0)
        attempts = _rows(conn, """SELECT record_id, attempt_index, plan_id, status, failing_json, signature,
                                         tier, latency_ms, input_tokens, output_tokens
                                  FROM attempts WHERE run_id=?""", (run_id,))
        samples = _rows(conn, "SELECT COUNT(*) FROM samples WHERE run_id=?", (run_id,))[0][0]
        reviews = _rows(conn, "SELECT reviewer, verdict, minutes FROM reviews WHERE run_id=?", (run_id,))
        pauses = _rows(conn, "SELECT COUNT(*) FROM breaker_events WHERE run_id=? AND kind='open'", (run_id,))[0][0]

        # Success within limits and rescues
        first_ok = {r[0] for r in attempts if r[1] == 1 and r[4] == "[]"}
        accepted_ids = {r[0] for r in _rows(conn, "SELECT record_id FROM records WHERE run_id=? AND state='ACCEPT'",
                                            (run_id,))}
        rescued = len(accepted_ids - first_ok)

        # Invariant adherence and prompt size by position
        by_pos: dict[int, list[tuple]] = {}
        for r in attempts:
            by_pos.setdefault(r[1], []).append(r)
        position = {}
        for idx in sorted(by_pos):
            rows = by_pos[idx]
            position[idx] = {
                "attempts": len(rows),
                "pass_rate": sum(1 for r in rows if r[4] == "[]") / len(rows),
                "mean_input_tokens": _mean([r[8] for r in rows]),
            }

        # Containment: fuse stops by tier. The fuse fires before an attempt is made, so the
        # tier is recorded in the record's reason ("fuse:<tier>; ...") and not on an attempt row.
        reasons = _rows(conn, "SELECT reason FROM records WHERE run_id=? AND reason LIKE 'fuse:%'", (run_id,))
        tiers = Counter(re.match(r"fuse:(\w+)", r[0]).group(1) for r in reasons)

        # Repetition: the largest count of one signature within a record (m calibration)
        per_record_sig: dict[str, Counter] = {}
        for r in attempts:
            if r[5]:
                per_record_sig.setdefault(r[0], Counter())[r[5]] += 1
        max_repeat = Counter(max(c.values()) for c in per_record_sig.values())

        # Cost
        calls = len(attempts)
        cost = {
            "calls": calls,
            "calls_per_record": calls / records if records else None,
            "input_tokens": sum(r[8] or 0 for r in attempts),
            "output_tokens": sum(r[9] or 0 for r in attempts),
            "mean_latency_ms": _mean([r[7] for r in attempts]),
        }

        # Oversight load h = u + s(1 - u)  (paper §10.2)
        u = dead / records if records else None
        s_rate = samples / accepted if accepted else None
        h = (u + s_rate * (1 - u)) if (u is not None and s_rate is not None) else None

        human = [m for who, v, m in reviews if who == "human"]
        ref = [v for who, v, _ in reviews if who == "reference"]
        silent = {
            "human_reviewed": len(human),
            "human_incorrect": sum(1 for who, v, _ in reviews if who == "human" and v == "incorrect"),
            "reference_judged": len(ref),
            "reference_incorrect": ref.count("incorrect"),
            "rev_e_minutes_mean": _mean(human),
        }

        return {
            "run_id": run_id,
            "records": records,
            "accepted": accepted,
            "dead_letters": dead,
            "accept_rate": accepted / records if records else None,
            "rescued_after_first_attempt": rescued,
            "h_oversight": h,
            "u_unresolved": u,
            "s_sample_rate": s_rate,
            "assurance_samples": samples,
            "breaker_pauses": pauses,
            "containment_stops_by_tier": dict(tiers),
            "max_signature_repeat_histogram": dict(max_repeat),
            "position": position,
            "cost": cost,
            "silent_failure": silent,
            "not_computed": {
                "fuse_precision_recall": "needs the counterfactual run without the fuse (not in this run)",
                "drift_failure_correlation": "J_struct is not stored per attempt (needs a schema change)",
                "dead_letter_delay": "per-record timestamps are not recorded at each step",
                "rho": "needs clustered reviews; not estimable from one block",
            },
        }
    finally:
        conn.close()


def _mean(values: list) -> float | None:
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None
