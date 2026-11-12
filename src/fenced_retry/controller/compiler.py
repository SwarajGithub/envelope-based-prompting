"""Deterministic plan compiler (HLD §11, D-026).

Maps the failing groups of the last attempt to the next plan in P_approved, and
chooses the note template. It reads only check groups and plan IDs, never the
input text or the output, and it cannot change the envelope.

Table (v1):
  amount:* failures  -> plan B (adds the consistency step)
  menu failures      -> plan C (payment and item rules spelled out)
  schema failures    -> plan A
If the table's plan has already reached the repetition limit, the first approved
plan below the limit is used. If none is below it, the current plan is kept, and
the repetition tier stops the record.
"""

from __future__ import annotations

from collections.abc import Iterable

GROUP_TO_PLAN = {"menu": "C", "schema": "A"}
AMOUNT_PREFIX = "amount:"


def _table_plan(groups: Iterable[str]) -> str | None:
    groups = list(groups)
    if any(g.startswith(AMOUNT_PREFIX) for g in groups):
        return "B"
    for g in sorted(groups):
        if g in GROUP_TO_PLAN:
            return GROUP_TO_PLAN[g]
    return None


def choose_next(
    *,
    current_plan: str,
    failing_groups: Iterable[str],
    plan_order: list[str],
    repeat_counts: dict[str, int],
    m: int,
) -> tuple[str, str]:
    """Return (next_plan_id, note_group)."""
    groups = sorted(set(failing_groups))
    note_group = groups[0] if groups else "schema"
    wanted = _table_plan(groups) or current_plan
    if repeat_counts.get(wanted, 0) < m:
        return wanted, note_group
    for plan_id in plan_order:
        if repeat_counts.get(plan_id, 0) < m:
            return plan_id, note_group
    return current_plan, note_group
