"""Approval lint (paper §5.3, HLD §8). Runs before an envelope can be approved.

For each plan in P_approved:
  - hard constraint: every mandatory step is in the plan;
  - hard constraint: for each precedence pair (u, v) in E0 with both in the plan, u comes first;
  - extensions (steps outside V0) are declared, and only declared extensions appear;
  - J_struct = |V_i ∩ V0| / |V_i ∪ V0| is at least theta_drift.
"""

from __future__ import annotations

from typing import Any


def j_struct(plan_steps: set[str], v0: set[str]) -> float:
    union = plan_steps | v0
    return len(plan_steps & v0) / len(union) if union else 1.0


def lint_envelope(env: dict[str, Any]) -> list[str]:
    """Return a list of violations. Empty list means the envelope may be approved."""
    problems: list[str] = []
    graph = env["step_graph"]
    v0 = set(graph["V0"])
    e0 = [tuple(e) for e in graph["E0"]]
    mandatory = set(graph["M"])
    threshold = env["theta"]["theta_drift"]

    if not mandatory <= v0:
        problems.append(f"mandatory steps not in V0: {sorted(mandatory - v0)}")

    for plan_id, plan in env["plans"].items():
        steps = plan["steps"]
        step_set = set(steps)
        if len(step_set) != len(steps):
            problems.append(f"{plan_id}: duplicate steps")

        missing = mandatory - step_set
        if missing:
            problems.append(f"{plan_id}: missing mandatory steps {sorted(missing)}")

        position = {s: i for i, s in enumerate(steps)}
        for u, v in e0:
            if u in position and v in position and position[u] > position[v]:
                problems.append(f"{plan_id}: precedence {u} -> {v} violated")

        extensions = step_set - v0
        declared = set(plan.get("extensions", []))
        if extensions != declared:
            problems.append(
                f"{plan_id}: undeclared extensions {sorted(extensions - declared)}, "
                f"declared but unused {sorted(declared - extensions)}"
            )

        score = j_struct(step_set, v0)
        if score < threshold:
            problems.append(f"{plan_id}: J_struct {score:.3f} below theta_drift {threshold}")

    return problems
