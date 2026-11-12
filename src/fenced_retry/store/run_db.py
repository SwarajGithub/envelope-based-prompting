"""Run database: one SQLite file per run under runs/ (HLD §14, D-019).

State is written after each transition, so a crash leaves a readable record of
what happened. Only standard-library sqlite3 is used.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fenced_retry.controller.types import Attempt, Outcome

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    envelope_hash TEXT NOT NULL,
    started_utc TEXT NOT NULL,
    shadow INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS records (
    run_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    state TEXT NOT NULL,
    plan_id TEXT,
    attempt_index INTEGER,
    reason TEXT,
    updated_utc TEXT NOT NULL,
    PRIMARY KEY (run_id, record_id)
);
CREATE TABLE IF NOT EXISTS attempts (
    run_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    attempt_index INTEGER NOT NULL,
    plan_id TEXT NOT NULL,
    call_id TEXT,
    status TEXT NOT NULL,
    failing_json TEXT NOT NULL,
    signature TEXT,
    output_text TEXT,
    tier TEXT,
    latency_ms REAL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    PRIMARY KEY (run_id, record_id, attempt_index)
);
CREATE TABLE IF NOT EXISTS ledger_entries (
    run_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    group_name TEXT NOT NULL,
    template_id TEXT NOT NULL,
    PRIMARY KEY (run_id, record_id, group_name)
);
CREATE TABLE IF NOT EXISTS dead_letters (
    run_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    PRIMARY KEY (run_id, record_id)
);
CREATE TABLE IF NOT EXISTS events (
    run_id TEXT NOT NULL,
    record_id TEXT,
    event TEXT NOT NULL,
    date_created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS samples (
    run_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    block_index INTEGER NOT NULL,
    cluster TEXT NOT NULL,
    date_created TEXT NOT NULL,
    PRIMARY KEY (run_id, record_id)
);
CREATE TABLE IF NOT EXISTS reviews (
    run_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    verdict TEXT NOT NULL,
    minutes REAL NOT NULL,
    reviewer TEXT NOT NULL DEFAULT 'human',
    note TEXT NOT NULL DEFAULT '',
    date_created TEXT NOT NULL,
    PRIMARY KEY (run_id, record_id, reviewer)
);
CREATE TABLE IF NOT EXISTS breaker_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    detail TEXT,
    date_created TEXT NOT NULL
);
"""
TERMINAL_STATES = ("ACCEPT", "DEAD_LETTER")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class RunStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.executescript(SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        """Add the reviewer and note columns to run files created before D-061."""
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(reviews)")}
        if "reviewer" not in cols:
            self._conn.execute("ALTER TABLE reviews ADD COLUMN reviewer TEXT NOT NULL DEFAULT 'human'")
        if "note" not in cols:
            self._conn.execute("ALTER TABLE reviews ADD COLUMN note TEXT NOT NULL DEFAULT ''")
        pk = [r[1] for r in self._conn.execute("PRAGMA table_info(reviews)") if r[5]]
        if pk == ["run_id", "record_id"]:  # older key: one verdict per record. Rebuild with reviewer in the key.
            self._conn.executescript("""
                CREATE TABLE reviews_new (
                    run_id TEXT NOT NULL, record_id TEXT NOT NULL, verdict TEXT NOT NULL,
                    minutes REAL NOT NULL, reviewer TEXT NOT NULL DEFAULT 'human',
                    note TEXT NOT NULL DEFAULT '', date_created TEXT NOT NULL,
                    PRIMARY KEY (run_id, record_id, reviewer));
                INSERT INTO reviews_new SELECT run_id, record_id, verdict, minutes, reviewer, note, date_created
                    FROM reviews;
                DROP TABLE reviews;
                ALTER TABLE reviews_new RENAME TO reviews;
            """)

    def close(self) -> None:
        self._conn.close()

    def start_run(self, run_id: str, envelope_hash: str, shadow: bool) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO runs (run_id, envelope_hash, started_utc, shadow) VALUES (?, ?, ?, ?)",
            (run_id, envelope_hash, _now(), int(shadow)),
        )
        self._conn.commit()

    def record_state(self, run_id: str, record_id: str, state: str,
                     plan_id: str | None, attempt_index: int | None, reason: str | None) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO records (run_id, record_id, state, plan_id, attempt_index, reason, updated_utc) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (run_id, record_id, state, plan_id, attempt_index, reason, _now()),
        )
        self._conn.commit()

    def add_attempt(self, run_id: str, record_id: str, plan_id: str, attempt: Attempt) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO attempts (run_id, record_id, attempt_index, plan_id, call_id, status, "
            "failing_json, signature, output_text, tier, latency_ms, input_tokens, output_tokens) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, record_id, attempt.index, plan_id, attempt.call_id, attempt.status,
             json.dumps(sorted(attempt.failing)), attempt.signature, attempt.output_text, attempt.tier,
             attempt.latency_ms, attempt.input_tokens, attempt.output_tokens),
        )
        self._conn.commit()

    def set_ledger(self, run_id: str, record_id: str, entries: dict[str, str]) -> None:
        self._conn.execute("DELETE FROM ledger_entries WHERE run_id = ? AND record_id = ?", (run_id, record_id))
        for group, template_id in entries.items():
            self._conn.execute(
                "INSERT INTO ledger_entries (run_id, record_id, group_name, template_id) VALUES (?, ?, ?, ?)",
                (run_id, record_id, group, template_id),
            )
        self._conn.commit()

    def add_outcome(self, run_id: str, outcome: Outcome) -> None:
        if outcome.state == "DEAD_LETTER":
            self._conn.execute(
                "INSERT OR REPLACE INTO dead_letters (run_id, record_id, reason) VALUES (?, ?, ?)",
                (run_id, outcome.record_id, outcome.reason),
            )
        self._conn.commit()

    # assurance (M7)
    def accepted_record_ids(self, run_id: str) -> list[str]:
        cur = self._conn.execute(
            "SELECT record_id FROM records WHERE run_id = ? AND state = 'ACCEPT' ORDER BY updated_utc, record_id",
            (run_id,),
        )
        return [r[0] for r in cur.fetchall()]

    def terminal_states_in_order(self, run_id: str) -> list[tuple[str, str]]:
        cur = self._conn.execute(
            "SELECT record_id, state FROM records WHERE run_id = ? AND state IN ('ACCEPT', 'DEAD_LETTER') "
            "ORDER BY updated_utc, record_id",
            (run_id,),
        )
        return [(r[0], r[1]) for r in cur.fetchall()]

    def _failing_lists(self, run_id: str, record_id: str) -> list[list[str]]:
        cur = self._conn.execute(
            "SELECT failing_json FROM attempts WHERE run_id = ? AND record_id = ? ORDER BY attempt_index",
            (run_id, record_id),
        )
        return [json.loads(r[0]) for r in cur.fetchall()]

    def cluster_for(self, run_id: str, record_id: str) -> str:
        from fenced_retry.assurance.sampler import cluster_of

        return cluster_of(self._failing_lists(run_id, record_id))

    def last_accepted_output(self, run_id: str, record_id: str) -> str | None:
        cur = self._conn.execute(
            "SELECT output_text FROM attempts WHERE run_id = ? AND record_id = ? AND failing_json = '[]' "
            "ORDER BY attempt_index DESC LIMIT 1",
            (run_id, record_id),
        )
        row = cur.fetchone()
        return row[0] if row else None

    def add_sample(self, run_id: str, record_id: str, block_index: int, cluster: str) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO samples (run_id, record_id, block_index, cluster, date_created) "
            "VALUES (?, ?, ?, ?, ?)",
            (run_id, record_id, block_index, cluster, _now()),
        )
        self._conn.commit()

    def pending_sample_ids(self, run_id: str, reviewer: str = "human") -> list[str]:
        """Sampled records with no verdict from this reviewer ("human" or "reference")."""
        cur = self._conn.execute(
            "SELECT s.record_id FROM samples s WHERE s.run_id = ? AND NOT EXISTS "
            "(SELECT 1 FROM reviews r WHERE r.run_id = s.run_id AND r.record_id = s.record_id "
            "AND r.reviewer = ?) ORDER BY s.block_index, s.record_id",
            (run_id, reviewer),
        )
        return [r[0] for r in cur.fetchall()]

    def add_review(self, run_id: str, record_id: str, verdict: str, minutes: float,
                   reviewer: str = "human", note: str = "") -> None:
        if verdict not in ("correct", "incorrect"):
            raise ValueError(verdict)
        self._conn.execute(
            "INSERT OR REPLACE INTO reviews (run_id, record_id, verdict, minutes, reviewer, note, date_created) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (run_id, record_id, verdict, float(minutes), reviewer, note, _now()),
        )
        self._conn.commit()

    def review_counts(self, run_id: str, reviewer: str = "human") -> tuple[int, int]:
        """(incorrect, total) over the verdicts of one reviewer. The silent-failure arm uses human verdicts."""
        k, n = self._conn.execute(
            "SELECT COALESCE(SUM(verdict = 'incorrect'), 0), COUNT(*) FROM reviews "
            "WHERE run_id = ? AND reviewer = ?",
            (run_id, reviewer),
        ).fetchone()
        return int(k), int(n)

    def reviews(self, run_id: str) -> list[dict[str, Any]]:
        cur = self._conn.execute(
            "SELECT record_id, verdict, minutes, reviewer, note, date_created FROM reviews WHERE run_id = ? "
            "ORDER BY date_created",
            (run_id,),
        )
        keys = ("record_id", "verdict", "minutes", "reviewer", "note", "date_created")
        return [dict(zip(keys, r)) for r in cur.fetchall()]

    def add_breaker_event(self, run_id: str, kind: str, detail: str = "") -> None:
        if kind not in ("open", "decision", "check"):
            raise ValueError(kind)
        self._conn.execute(
            "INSERT INTO breaker_events (run_id, kind, detail, date_created) VALUES (?, ?, ?, ?)",
            (run_id, kind, detail, _now()),
        )
        self._conn.commit()

    def last_breaker_open(self, run_id: str) -> bool:
        """True when the most recent open/decision event is an open (no decision since)."""
        row = self._conn.execute(
            "SELECT kind FROM breaker_events WHERE run_id = ? AND kind IN ('open', 'decision') "
            "ORDER BY seq DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        return bool(row) and row[0] == "open"

    # read helpers (used by tests and reports)
    def record_row(self, run_id: str, record_id: str) -> dict[str, Any] | None:
        cur = self._conn.execute(
            "SELECT state, plan_id, attempt_index, reason FROM records WHERE run_id = ? AND record_id = ?",
            (run_id, record_id),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return dict(zip(("state", "plan_id", "attempt_index", "reason"), row))

    def record_states(self, run_id: str) -> dict[str, str]:
        cur = self._conn.execute("SELECT record_id, state FROM records WHERE run_id = ?", (run_id,))
        return {rid: state for rid, state in cur.fetchall()}

    def reset_record(self, run_id: str, record_id: str) -> None:
        """Remove a partly processed record so it can be run again from its first attempt."""
        for table in ("attempts", "ledger_entries", "records", "dead_letters"):
            self._conn.execute(f"DELETE FROM {table} WHERE run_id = ? AND record_id = ?", (run_id, record_id))
        self._conn.commit()

    def log_event(self, run_id: str, record_id: str | None, event: str) -> None:
        self._conn.execute(
            "INSERT INTO events (run_id, record_id, event, date_created) VALUES (?, ?, ?, ?)",
            (run_id, record_id, event, _now()),
        )
        self._conn.commit()

    def count(self, table: str, run_id: str) -> int:
        if table not in {"attempts", "ledger_entries", "dead_letters", "records", "events"}:
            raise ValueError(table)
        return self._conn.execute(f"SELECT COUNT(*) FROM {table} WHERE run_id = ?", (run_id,)).fetchone()[0]
