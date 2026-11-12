"""Batch runner: conditions, resume, and envelope checks (HLD §8, §13, §16).

Resume rule (M6): records already ACCEPT or DEAD_LETTER in the run database are
skipped. A record left in progress is removed from the database and run again
from its first attempt; the restart is logged as an event.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from fenced_retry.controller.loop import run_record
from fenced_retry.controller.types import Caller, Outcome
from fenced_retry.envelope.approval import verify
from fenced_retry.envelope.canonical import envelope_hash
from fenced_retry.envelope.model import load_envelope
from fenced_retry.store.run_db import TERMINAL_STATES

CONDITIONS = ("controller", "single_shot", "naive_retry", "frozen")


class BatchHalted(RuntimeError):
    """The envelope no longer matches its approval. The run stops (HLD §16)."""


def _check_envelope(env_path: Path) -> dict[str, Any]:
    problems = verify(env_path)
    if problems:
        raise BatchHalted("; ".join(problems))
    return load_envelope(env_path)


def _dispatch(condition: str) -> Callable[..., Outcome]:
    if condition == "controller":
        return run_record
    from fenced_retry.harness.conditions import run_frozen, run_naive_retry, run_single_shot
    if condition == "single_shot":
        return run_single_shot
    if condition == "frozen":
        return run_frozen
    if condition == "naive_retry":
        return run_naive_retry
    raise ValueError(f"unknown condition {condition!r}")


def run_batch(
    *,
    env_path: Path,
    records: Iterable[tuple[str, str]],
    caller: Caller,
    store: Any,
    run_id: str,
    condition: str = "controller",
    shadow: bool = False,
    project: str = "pilot",
    clock: Callable[[], float] = time.monotonic,
    breaker: Callable[[Any, str], bool] | None = None,
) -> list[Outcome]:
    """Run records under one envelope. breaker(store, run_id) -> True pauses the batch (M7).

    While the breaker is open, a new run call halts until a decision is recorded
    with `decide` (paper §7: resumption is an explicit human decision).
    """
    fn = _dispatch(condition)
    env = _check_envelope(env_path)
    store.start_run(run_id, envelope_hash(env), shadow)
    if breaker is not None and store.last_breaker_open(run_id):
        raise BatchHalted("breaker is open for this run; record a decision with `decide` before resuming")
    done = store.record_states(run_id)
    outcomes: list[Outcome] = []
    for record_id, input_text in records:
        state = done.get(record_id)
        if state in TERMINAL_STATES:
            store.log_event(run_id, record_id, f"skipped: already {state}")
            continue
        if state is not None:
            store.log_event(run_id, record_id, f"restarted from first attempt (was {state})")
            store.reset_record(run_id, record_id)
        env = _check_envelope(env_path)  # per record, as HLD §8 requires
        kwargs: dict[str, Any] = dict(record_id=record_id, input_text=input_text, env=env,
                                      caller=caller, run_id=run_id, store=store,
                                      project=project, shadow=shadow)
        if condition in ("controller", "frozen"):
            kwargs["clock"] = clock
        outcome = fn(**kwargs)
        store.add_outcome(run_id, outcome)
        outcomes.append(outcome)
        if breaker is not None and breaker(store, run_id):
            store.add_breaker_event(run_id, "open", f"after record {record_id}")
            store.log_event(run_id, record_id, "breaker opened: batch paused")
            break
    return outcomes
