"""Reference judge (D-061): an automatic verdict on each sampled accepted output.

The output is compared field by field with the CORD reference (gt_parse). Amounts are
compared after normalisation (D-038). A null field is compared too, so a value that
is in the reference but missing from the output is a mismatch. The validator skips
null fields (checks.py), so this check catches a class the validator cannot see.

The verdict is recorded with reviewer = "reference". It is an automatic label, not a
human review, and it is reported separately from human verdicts.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fenced_retry.adapters.cord import Record
from fenced_retry.controller.loop import parse_output
from fenced_retry.validator.numbers import normalize_amount

AMOUNT_KEYS = (
    "subtotal_price", "tax_price", "service_price", "discount_price",
    "total_price", "cashprice", "changeprice", "creditcardprice", "emoneyprice",
)
REVIEWER = "reference"


def _amount(v: Any) -> Any:
    if v is None or str(v).strip() == "":
        return None
    d = normalize_amount(v)
    return d if d is not None else ("raw", str(v).strip())


def _name(v: Any) -> str | None:
    return None if v is None else " ".join(str(v).upper().split())


def _menu_key(item: dict[str, Any]) -> tuple:
    cnt = item.get("cnt")
    return (_name(item.get("nm")), _amount(item.get("price")), None if cnt is None else str(cnt).strip())


def mismatches(output: dict[str, Any] | None, reference: dict[str, Any]) -> list[str]:
    """Return a list of differences. An empty list means the output matches the reference."""
    if output is None:
        return ["output is not one JSON object"]
    out: list[str] = []
    for k in AMOUNT_KEYS:
        a, r = _amount(output.get(k)), _amount(reference.get(k))
        if a != r:
            out.append(f"{k}: output {output.get(k)!r}, reference {reference.get(k)!r}")
    out_menu = sorted((_menu_key(i) for i in (output.get("menu") or []) if isinstance(i, dict)), key=repr)
    ref_menu = sorted((_menu_key(i) for i in (reference.get("menu") or []) if isinstance(i, dict)), key=repr)
    if out_menu != ref_menu:
        missing = [m for m in ref_menu if m not in out_menu]
        extra = [m for m in out_menu if m not in ref_menu]
        if missing:
            out.append(f"menu missing: {missing}")
        if extra:
            out.append(f"menu extra: {extra}")
    return out


def judge_samples(store: Any, run_id: str, records: dict[str, Record], *,
                  print_fn: Callable[[str], None] = print) -> dict[str, int]:
    """Give every sampled record without a verdict an automatic verdict. Returns counts."""
    counts = {"correct": 0, "incorrect": 0, "no_reference": 0}
    for rid in store.pending_sample_ids(run_id, reviewer=REVIEWER):
        rec = records.get(rid)
        if rec is None:
            counts["no_reference"] += 1
            print_fn(f"{rid}: no reference found in the data split")
            continue
        diffs = mismatches(parse_output(store.last_accepted_output(run_id, rid)), rec.reference)
        verdict = "incorrect" if diffs else "correct"
        counts[verdict] += 1
        store.add_review(run_id, rid, verdict, 0.0, reviewer=REVIEWER, note="; ".join(diffs))
        print_fn(f"{rid}: {verdict}" + (f" ({'; '.join(diffs)})" if diffs else ""))
    return counts
