"""Review session: a person judges sampled accepted outputs (paper §7, HLD §12).

The screen shows the input lines, the accepted output, and the reference fields
side by side. The reviewer answers c (correct), i (incorrect), s (skip, not
recorded), or q (quit). The time from display to answer is recorded in minutes.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from fenced_retry.adapters.cord import Record


def _show(rec: Record, output: str | None, print_fn: Callable[[str], None]) -> None:
    print_fn(f"=== {rec.record_id} ===")
    print_fn("INPUT LINES:")
    print_fn(rec.input_text)
    print_fn("OUTPUT (accepted):")
    print_fn(output if output is not None else "(no accepted output found)")
    print_fn("REFERENCE:")
    print_fn(json.dumps(rec.reference, ensure_ascii=False, indent=2, sort_keys=True))


def review_session(store: Any, run_id: str, records: dict[str, Record], *,
                   input_fn: Callable[[str], str] = input,
                   print_fn: Callable[[str], None] = print,
                   clock: Callable[[], float] = time.monotonic) -> int:
    """Review every pending sample for the run. Returns the number of verdicts recorded."""
    recorded = 0
    for rid in store.pending_sample_ids(run_id):
        rec = records.get(rid)
        if rec is None:
            print_fn(f"skip {rid}: not found in the data split")
            continue
        start = clock()
        _show(rec, store.last_accepted_output(run_id, rid), print_fn)
        while True:
            answer = input_fn("[c]orrect / [i]ncorrect / [s]kip / [q]uit: ").strip().lower()
            if answer in ("c", "i", "s", "q"):
                break
            print_fn("answer c, i, s or q")
        if answer == "q":
            break
        if answer == "s":
            continue
        minutes = (clock() - start) / 60.0
        store.add_review(run_id, rid, "incorrect" if answer == "i" else "correct", minutes)
        recorded += 1
    return recorded
