"""Baseline conditions for the pilot (paper §9.2, HLD §13).

single_shot:  one call on the initial plan with the schema note; no retry.
naive_retry:  the same prompt is sent again after each failure, up to N_max attempts.
              No ledger, no plan change, no fuse. At temperature 0 the retries
              are expected to repeat the same output; the runs will show whether that holds.

Both use the same validator and the same run store as the controller, so the
results are directly comparable.
"""

from __future__ import annotations

from typing import Any

from fenced_retry.controller.fuse import signature
from fenced_retry.controller.loop import INITIAL_NOTE_GROUP, _validate
from fenced_retry.controller.prompt import assemble
from fenced_retry.controller.types import Attempt, Caller, Outcome

BASELINE_PLAN = "A"


def _attempt(index: int, call: Any, failing: frozenset[str]) -> Attempt:
    return Attempt(
        index=index,
        plan_id=BASELINE_PLAN,
        call_id=getattr(call, "call_id", None),
        status=call.status,
        failing=failing,
        signature=signature(failing) if failing else None,
        output_text=call.text,
        latency_ms=getattr(call, "latency_ms", None),
        input_tokens=getattr(call, "input_tokens", None),
        output_tokens=getattr(call, "output_tokens", None),
    )


def _check(call: Any, input_text: str) -> frozenset[str]:
    if call.status != "ok":
        return frozenset({f"CALL_{call.status.upper()}"})
    failing, _ = _validate(call.text, input_text)
    return failing


def _messages(env: dict[str, Any], input_text: str, attempt: int) -> list[dict[str, str]]:
    return assemble(
        env=env, input_text=input_text, attempt=attempt, plan_id=BASELINE_PLAN,
        ledger_lines=[], note_text=env["templates"][INITIAL_NOTE_GROUP],
    )


def _finish(store: Any, run_id: str, record_id: str, outcome: Outcome) -> None:
    if store is not None:
        store.record_state(run_id, record_id, outcome.state, BASELINE_PLAN,
                           len(outcome.attempts) or None, outcome.reason)
        store.add_outcome(run_id, outcome)


def run_single_shot(*, record_id: str, input_text: str, env: dict[str, Any], caller: Caller,
                    run_id: str = "run", store: Any | None = None, project: str = "pilot",
                    shadow: bool = False) -> Outcome:
    messages = _messages(env, input_text, 1)
    if store is not None:
        store.record_state(run_id, record_id, "CALL", BASELINE_PLAN, 1, None)
    call = caller.chat(messages, project_name=project, stage="single_shot")
    failing = _check(call, input_text)
    attempt = _attempt(1, call, failing)
    if store is not None:
        store.add_attempt(run_id, record_id, BASELINE_PLAN, attempt)
    if failing:
        outcome = Outcome(record_id, "DEAD_LETTER", f"single_shot: failed {sorted(failing)}",
                          attempts=[attempt], shadow=shadow)
    else:
        parsed = _validate(call.text, input_text)[1]
        outcome = Outcome(record_id, "ACCEPT", "accepted", output=parsed, attempts=[attempt], shadow=shadow)
    _finish(store, run_id, record_id, outcome)
    return outcome


def run_naive_retry(*, record_id: str, input_text: str, env: dict[str, Any], caller: Caller,
                    run_id: str = "run", store: Any | None = None, project: str = "pilot",
                    shadow: bool = False) -> Outcome:
    limit = env["theta"]["N_max"]
    messages = _messages(env, input_text, 1)  # same prompt every time
    attempts: list[Attempt] = []
    for index in range(1, limit + 1):
        if store is not None:
            store.record_state(run_id, record_id, "CALL", BASELINE_PLAN, index, None)
        call = caller.chat(messages, project_name=project, stage="naive_retry")
        failing = _check(call, input_text)
        attempt = _attempt(index, call, failing)
        attempts.append(attempt)
        if store is not None:
            store.add_attempt(run_id, record_id, BASELINE_PLAN, attempt)
        if not failing:
            parsed = _validate(call.text, input_text)[1]
            outcome = Outcome(record_id, "ACCEPT", "accepted", output=parsed, attempts=attempts, shadow=shadow)
            _finish(store, run_id, record_id, outcome)
            return outcome
    outcome = Outcome(record_id, "DEAD_LETTER", f"naive_retry: {limit} attempts failed",
                      attempts=attempts, shadow=shadow)
    _finish(store, run_id, record_id, outcome)
    return outcome


def _frozen_compile(*, current_plan: str, failing_groups: Any, plan_order: Any,
                    repeat_counts: Any, m: int) -> tuple[str, str]:
    """Frozen strategy (M8, paper §9.2): the plan is fixed to S_1. The ledger, the note
    choice, and the fuse are unchanged, so only the plan choice is removed."""
    groups = sorted(set(failing_groups))
    return "A", (groups[0] if groups else INITIAL_NOTE_GROUP)


def run_frozen(*, record_id: str, input_text: str, env: dict[str, Any], caller: Caller,
               run_id: str = "run", store: Any | None = None, project: str = "pilot",
               shadow: bool = False, clock: Any = None) -> Outcome:
    from fenced_retry.controller.loop import run_record
    import time as _time

    return run_record(record_id=record_id, input_text=input_text, env=env, caller=caller,
                      run_id=run_id, store=store, project=project, shadow=shadow,
                      initial_plan="A", clock=clock or _time.monotonic,
                      compile_fn=_frozen_compile)
