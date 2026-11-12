"""Failure signature and the per-record fuse (paper §5.4, HLD §9).

CB(i) is open (1) when any tier trips:
  limit:       attempt index above N_max, or elapsed time above t_max
  repetition:  failure count for the last signature is at least m,
               or the candidate plan's repeat count is at least m
  envelope:    the plan or note fails the envelope check, or the prompt is too large
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable


def signature(failing: Iterable[str]) -> str:
    """sigma = hash of the sorted failing check IDs. Depends only on IDs (Appendix B)."""
    joined = "|".join(sorted(set(failing)))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def fuse_check(
    *,
    index: int,
    elapsed_s: float,
    last_signature: str | None,
    signature_counts: dict[str, int],
    plan_repeat: int,
    N_max: int,
    t_max_s: float,
    m: int,
    envelope_ok: bool,
    prompt_ok: bool,
) -> str | None:
    """Return the tier that opens the fuse, or None when the attempt may run."""
    if index > N_max or elapsed_s > t_max_s:
        return "limit"
    if last_signature is not None and signature_counts.get(last_signature, 0) >= m:
        return "repetition"
    if plan_repeat >= m:
        return "repetition"
    if not envelope_ok or not prompt_ok:
        return "envelope"
    return None
