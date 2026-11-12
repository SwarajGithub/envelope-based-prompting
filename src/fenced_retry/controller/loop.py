"""Per-record controller loop (paper §4, §5.4, §5.5; HLD §9).

Order for each attempt i:
  1. choose the plan and note for attempt i (initial plan, or the compiler's choice);
  2. evaluate the fuse on that plan (limit, repetition, envelope tiers);
  3. if open, stop the main loop and run the fallback menu;
  4. otherwise assemble the prompt, call the model, validate, and record.

Fallback runs after the main loop and never re-enters it (paper §5.5).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from fenced_retry.controller.compiler import choose_next
from fenced_retry.controller.fuse import fuse_check, signature
from fenced_retry.controller.ledger import Ledger
from fenced_retry.controller.prompt import assemble
from fenced_retry.controller.types import Attempt, Caller, Outcome
from fenced_retry.llm.tokens import estimate_prompt_tokens
from fenced_retry.validator.checks import GROUP_OF, check_output

INITIAL_NOTE_GROUP = "schema"


def parse_output(text: str | None) -> dict[str, Any] | None:
    """Parse the model's JSON object. Returns None if the text is not one JSON object."""
    if text is None:
        return None
    s = text.strip()
    if s.startswith("```"):
        s = s.strip("`").strip()
        if s.lower().startswith("json"):
            s = s[4:].strip()
    try:
        data = json.loads(s)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _validate(text: str | None, input_text: str) -> tuple[frozenset[str], dict[str, Any] | None]:
    parsed = parse_output(text)
    if parsed is None:
        return frozenset({"PARSE_ERROR"}), None
    return frozenset(check_output(parsed, input_text)), parsed


def run_record(
    *,
    record_id: str,
    input_text: str,
    env: dict[str, Any],
    caller: Caller,
    run_id: str = "run",
    store: Any | None = None,
    project: str = "pilot",
    shadow: bool = False,
    initial_plan: str = "A",
    clock: Callable[[], float] = time.monotonic,
    compile_fn: Callable[..., tuple[str, str]] | None = None,
) -> Outcome:
    theta = env["theta"]
    N_max = theta["N_max"]
    t_max = theta["t_max_s"]
    m = theta["m"]
    num_ctx = theta["num_ctx"]
    num_predict = theta["num_predict"]
    templates = env["templates"]
    plan_order = list(env["plans"].keys())
    compile_next = compile_fn or choose_next

    ledger = Ledger()
    attempts: list[Attempt] = []
    signature_counts: dict[str, int] = {}
    plan_counts: dict[str, int] = {}
    last_signature: str | None = None
    plan_id, note_group = initial_plan, INITIAL_NOTE_GROUP
    start = clock()
    index = 1

    def persist(state: str, reason: str | None = None) -> None:
        if store is not None:
            store.record_state(run_id, record_id, state, plan_id, index, reason)

    persist("NEW")

    while True:
        envelope_ok = plan_id in env["plans"] and note_group in templates
        messages = None
        prompt_ok = False
        if envelope_ok:
            messages = assemble(
                env=env, input_text=input_text, attempt=index, plan_id=plan_id,
                ledger_lines=ledger.render(templates), note_text=templates[note_group],
            )
            prompt_ok = estimate_prompt_tokens(messages) + num_predict <= num_ctx
        tier = fuse_check(
            index=index,
            elapsed_s=clock() - start,
            last_signature=last_signature,
            signature_counts=signature_counts,
            plan_repeat=plan_counts.get(plan_id, 0),
            N_max=N_max,
            t_max_s=t_max,
            m=m,
            envelope_ok=envelope_ok,
            prompt_ok=prompt_ok,
        )
        if tier is not None:
            persist("FALLBACK", f"fuse:{tier}")
            break

        plan_counts[plan_id] = plan_counts.get(plan_id, 0) + 1
        persist("CALL")
        call = caller.chat(messages, project_name=project, stage="executor")
        if call.status == "ok":
            failing, parsed = _validate(call.text, input_text)
        else:
            failing, parsed = frozenset({f"CALL_{call.status.upper()}"}), None
        persist("VALIDATE")

        sig = signature(failing) if failing else None
        attempt = Attempt(
            index=index, plan_id=plan_id, call_id=getattr(call, "call_id", None),
            status=call.status, failing=failing, signature=sig,
            output_text=call.text,
            latency_ms=getattr(call, "latency_ms", None),
            input_tokens=getattr(call, "input_tokens", None),
            output_tokens=getattr(call, "output_tokens", None),
        )
        attempts.append(attempt)
        if store is not None:
            store.add_attempt(run_id, record_id, plan_id, attempt)

        if not failing:
            persist("ACCEPT", "accepted")
            if store is not None:
                store.set_ledger(run_id, record_id, ledger.entries())
            return Outcome(record_id, "ACCEPT", "accepted", output=parsed, attempts=attempts,
                           ledger=ledger.entries(), shadow=shadow)

        signature_counts[sig] = signature_counts.get(sig, 0) + 1
        last_signature = sig
        groups = {GROUP_OF.get(c, c) for c in failing}
        for group in sorted(groups):
            if group in templates:
                ledger.update(group, group)
        plan_id, note_group = compile_next(
            current_plan=plan_id,
            failing_groups=groups,
            plan_order=plan_order,
            repeat_counts=plan_counts,
            m=m,
        )
        if note_group not in templates and plan_id in env["plans"]:
            # Call failures have no template; the note falls back to the schema template.
            note_group = INITIAL_NOTE_GROUP
        index += 1

    # Fallback menu: each item at most once; never re-enters the main loop (§5.5).
    if store is not None:
        store.set_ledger(run_id, record_id, ledger.entries())
    for item in env["fallback"]:
        if item["type"] == "deterministic_default":
            reason = f"fuse:{tier}; fallback {item['id']}: deterministic default held for review"
            persist("DEAD_LETTER", reason)
            return Outcome(record_id, "DEAD_LETTER", reason, attempts=attempts,
                           ledger=ledger.entries(), shadow=shadow)
        # Other fallback types (prior envelope version) are not implemented in v1.
        continue
    reason = f"fuse:{tier}; fallback menu exhausted"
    persist("DEAD_LETTER", reason)
    return Outcome(record_id, "DEAD_LETTER", reason, attempts=attempts,
                   ledger=ledger.entries(), shadow=shadow)
