"""Envelope loading and structural validation (paper Appendix A.1, HLD §8).

An envelope has these top-level keys:
  envelope_id, version   identity
  invariants             I: list of invariant statements
  step_graph             G0: V0 (step IDs), E0 (precedence pairs), M (mandatory)
  plans                  P_approved: plan_id -> {steps, extensions, description}
  fallback               fallback menu, K = len(fallback)
  templates              T: directive group -> template text
  theta                  Θ: model pin, limits, and calibration parameters
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REQUIRED_TOP = (
    "envelope_id", "version", "invariants", "step_graph",
    "plans", "fallback", "templates", "theta",
)
REQUIRED_THETA = (
    "model", "num_ctx", "num_predict", "temperature", "seeds", "think_enabled",
    "call_timeout_s", "N_max", "t_max_s", "K", "m", "theta_drift", "alpha", "gamma",
    "W_d", "W_a", "max_total_tokens", "max_wall_seconds", "phi", "phi_s",
)


class EnvelopeError(ValueError):
    """The envelope file is malformed."""


def load_envelope(path: Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_structure(data)
    return data


def validate_structure(env: dict[str, Any]) -> None:
    missing = [k for k in REQUIRED_TOP if k not in env]
    if missing:
        raise EnvelopeError(f"missing top-level keys: {missing}")
    theta = env["theta"]
    missing_theta = [k for k in REQUIRED_THETA if k not in theta]
    if missing_theta:
        raise EnvelopeError(f"missing theta keys: {missing_theta}")
    if "name" not in theta["model"] or "digest" not in theta["model"]:
        raise EnvelopeError("theta.model needs name and digest")
    graph = env["step_graph"]
    for k in ("V0", "E0", "M"):
        if k not in graph:
            raise EnvelopeError(f"step_graph missing {k}")
    if not env["plans"]:
        raise EnvelopeError("P_approved is empty")
    if len(env["fallback"]) != theta["K"]:
        raise EnvelopeError("theta.K must equal the number of fallback items")
