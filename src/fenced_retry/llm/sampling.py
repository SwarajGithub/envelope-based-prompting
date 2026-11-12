"""Stage temperatures, read from configs/sampling.toml (D-057).

Temperature is set per stage of the pipeline:
  executor       the controller's model calls. Governed by the envelope (theta.temperature),
                 so a change is an approved envelope change (paper A7). Not set here.
  single_shot    the single-shot baseline.
  naive_retry    the naive retry baseline.
  plan_compiler  reserved for an LLM plan compiler (not in v1).
  judge          reserved for the offline LLM judge (paper A12, not in v1).

Every call records the temperature it actually used, so changing a value here does
not hide what was run.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

ALLOWED_STAGES = ("single_shot", "naive_retry", "plan_compiler", "judge")
GOVERNED_STAGES = ("executor",)
MAX_TEMPERATURE = 2.0


class SamplingError(ValueError):
    pass


def load_sampling(path: Path) -> dict[str, float]:
    """Return {stage: temperature} for the stages set in the file."""
    raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    out: dict[str, float] = {}
    for stage, values in raw.items():
        if stage in GOVERNED_STAGES:
            raise SamplingError(f"[{stage}] is governed by the envelope (theta.temperature); remove it here")
        if stage not in ALLOWED_STAGES:
            raise SamplingError(f"unknown stage [{stage}]; allowed: {ALLOWED_STAGES}")
        if "temperature" not in values:
            raise SamplingError(f"[{stage}] needs a temperature")
        t = float(values["temperature"])
        if not 0.0 <= t <= MAX_TEMPERATURE:
            raise SamplingError(f"[{stage}] temperature {t} outside 0..{MAX_TEMPERATURE}")
        out[stage] = t
    return out
