"""Usage tracker: one row per LLM call in a shared SQLite file (D-016, D-017).

Rows hold metadata and hashes for every call. Full prompt, output, and thinking
text are stored only when the call's ``store_full_text`` flag is on (D-017).
"""

from __future__ import annotations

import hashlib
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_PROJECT = "unassigned"

SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_calls (
    call_id         TEXT PRIMARY KEY,
    project_name   TEXT NOT NULL,
    date_created   TEXT NOT NULL,
    model          TEXT NOT NULL,
    model_digest   TEXT,
    endpoint       TEXT NOT NULL,
    num_ctx        INTEGER NOT NULL,
    num_predict    INTEGER NOT NULL,
    temperature    REAL NOT NULL,
    seed           INTEGER NOT NULL,
    think          INTEGER NOT NULL,
    stage          TEXT,
    input_tokens   INTEGER,
    output_tokens  INTEGER,
    thinking_tokens INTEGER,
    load_ms        REAL,
    latency_ms     REAL,
    status         TEXT NOT NULL,
    error_text     TEXT,
    prompt_hash    TEXT NOT NULL,
    output_hash    TEXT,
    thinking_hash  TEXT,
    store_full_text INTEGER NOT NULL,
    input_text     TEXT,
    output_text    TEXT,
    thinking_text  TEXT
)
"""


def sha256_text(text: str | None) -> str | None:
    if text is None:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def new_call_id() -> str:
    return str(uuid.uuid4())


def normalise_project(name: str | None) -> str:
    """Blank or missing project names become DEFAULT_PROJECT."""
    if name is None or not str(name).strip():
        return DEFAULT_PROJECT
    return str(name).strip()


@dataclass
class UsageTracker:
    db_path: Path
    _conn: sqlite3.Connection | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.db_path = Path(self.db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.execute(SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        """Add columns that newer versions expect to files created by older versions."""
        cols = {row[1] for row in self._conn.execute("PRAGMA table_info(llm_calls)")}
        if "stage" not in cols:
            self._conn.execute("ALTER TABLE llm_calls ADD COLUMN stage TEXT")

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def record(self, row: dict[str, Any]) -> None:
        """Insert one call row. ``row`` uses the column names in SCHEMA."""
        if self._conn is None:
            raise RuntimeError("usage tracker is closed")
        row = dict(row)
        row["project_name"] = normalise_project(row.get("project_name"))
        row.setdefault("date_created", datetime.now(timezone.utc).isoformat(timespec="microseconds"))
        cols = list(row.keys())
        placeholders = ", ".join("?" for _ in cols)
        sql = f"INSERT INTO llm_calls ({', '.join(cols)}) VALUES ({placeholders})"
        self._conn.execute(sql, [row[c] for c in cols])
        self._conn.commit()

    def totals(self) -> dict[str, float]:
        """Totals across all rows in the file (useful for reports)."""
        if self._conn is None:
            raise RuntimeError("usage tracker is closed")
        cur = self._conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0), "
            "COALESCE(SUM(latency_ms),0) FROM llm_calls"
        )
        calls, tin, tout, ms = cur.fetchone()
        return {"calls": calls, "input_tokens": tin, "output_tokens": tout, "latency_ms": ms}
