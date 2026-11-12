"""Fleet breaker (paper §5.5, HLD §12).

Two arms, each optional:
  dead-letter arm  trips when the dead-letter fraction over the last W_d terminal
                   records exceeds phi. Only evaluated on a full window.
  silent arm       trips when the lower confidence bound at level gamma on the
                   reviewed silent-failure rate exceeds phi_s.

A threshold of None leaves that arm observational: values are reported, nothing pauses.
The v1 envelope has phi and phi_s = null, so the pilot breaker is observational.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from fenced_retry.assurance.stats import clopper_pearson_lower
from fenced_retry.store.run_db import TERMINAL_STATES


@dataclass
class BreakerState:
    dl_window: int
    dl_full: bool
    dl_dead: int
    dl_fraction: float | None
    silent_n: int
    silent_k: int
    silent_lcb: float | None
    dl_armed: bool
    silent_armed: bool
    reasons: list[str] = field(default_factory=list)

    @property
    def open(self) -> bool:
        return bool(self.reasons)


def evaluate(store: Any, run_id: str, *, w_d: int, phi: float | None,
             phi_s: float | None, gamma: float) -> BreakerState:
    terminal = [s for _, s in store.terminal_states_in_order(run_id) if s in TERMINAL_STATES]
    window = terminal[-w_d:]
    dead = sum(1 for s in window if s == "DEAD_LETTER")
    full = len(window) == w_d
    fraction = (dead / len(window)) if window else None

    k, n = store.review_counts(run_id)
    lcb = clopper_pearson_lower(k, n, gamma) if n else None

    state = BreakerState(
        dl_window=len(window), dl_full=full, dl_dead=dead, dl_fraction=fraction,
        silent_n=n, silent_k=k, silent_lcb=lcb,
        dl_armed=phi is not None, silent_armed=phi_s is not None,
    )
    if state.dl_armed and full and fraction is not None and fraction > phi:
        state.reasons.append(f"dead-letter fraction {fraction:.3f} > phi {phi} over W_d={w_d}")
    if state.silent_armed and lcb is not None and lcb > phi_s:
        state.reasons.append(f"silent-failure LCB {lcb:.3f} > phi_s {phi_s} (n={n}, gamma={gamma})")
    return state


def make_fleet_check(*, w_d: int, phi: float | None, phi_s: float | None,
                     gamma: float) -> Callable[[Any, str], bool]:
    """Callable used by run_batch: True means the batch must pause."""
    def check(store: Any, run_id: str) -> bool:
        return evaluate(store, run_id, w_d=w_d, phi=phi, phi_s=phi_s, gamma=gamma).open
    return check
