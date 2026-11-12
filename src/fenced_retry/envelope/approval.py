"""Approval record and verification (D-020, D-037).

Approval is a single person's recorded decision: the envelope hash, the approver,
the time, and a note are written to envelopes/<version>.approval.json. This is a
hash, not a signature; changes are detectable, not unforgeable (stated gap, HLD §8).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fenced_retry.envelope.canonical import envelope_hash
from fenced_retry.envelope.lint import lint_envelope
from fenced_retry.envelope.model import load_envelope


class ApprovalError(RuntimeError):
    pass


def approval_path(env_path: Path) -> Path:
    return env_path.with_suffix("").with_suffix(".approval.json")


def approve(env_path: Path, approver: str, note: str) -> dict[str, Any]:
    if not approver.strip():
        raise ApprovalError("approver name is required")
    env = load_envelope(env_path)
    problems = lint_envelope(env)
    if problems:
        raise ApprovalError("lint failed: " + "; ".join(problems))
    record = {
        "envelope_id": env["envelope_id"],
        "version": env["version"],
        "envelope_file": env_path.name,
        "envelope_hash": envelope_hash(env),
        "approver": approver.strip(),
        "approved_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "note": note,
    }
    out = approval_path(env_path)
    out.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return record


def verify(env_path: Path) -> list[str]:
    """Return problems found. Empty list means the envelope matches its approval."""
    problems: list[str] = []
    env = load_envelope(env_path)
    problems.extend(lint_envelope(env))
    record_path = approval_path(env_path)
    if not record_path.exists():
        problems.append(f"no approval record at {record_path.name}")
        return problems
    record = json.loads(record_path.read_text(encoding="utf-8"))
    if record.get("envelope_hash") != envelope_hash(env):
        problems.append("envelope hash differs from approved hash (envelope changed after approval)")
    return problems
