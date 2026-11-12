"""Run configuration (TOML, not governed by the envelope; HLD §15)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from fenced_retry.controller.batch import CONDITIONS

SPLITS = ("train", "validation", "test")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class PilotConfig:
    name: str
    envelope: Path
    data: Path
    split: str
    offset: int
    limit: int
    conditions: tuple[str, ...]
    seeds: tuple[int, ...]
    project: str = "pilot"
    shadow: bool = False
    store_full_text: bool = True
    final: bool = False          # required to use the test split (D-046)
    runs_dir: Path = Path("runs")
    extra: dict = field(default_factory=dict)


def load_config(path: Path, *, envelope_seeds: list[int]) -> PilotConfig:
    raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    run = raw.get("run", {})
    try:
        cfg = PilotConfig(
            name=run["name"],
            envelope=Path(run["envelope"]),
            data=Path(run["data"]),
            split=run["split"],
            offset=int(run.get("offset", 0)),
            limit=int(run["limit"]),
            conditions=tuple(run.get("conditions", ["controller"])),
            seeds=tuple(int(x) for x in run.get("seeds", envelope_seeds)),
            project=run.get("project", "pilot"),
            shadow=bool(run.get("shadow", False)),
            store_full_text=bool(run.get("store_full_text", True)),
            final=bool(run.get("final", False)),
            runs_dir=Path(run.get("runs_dir", "runs")),
        )
    except KeyError as exc:
        raise ConfigError(f"missing key in [run]: {exc}") from exc
    validate(cfg, envelope_seeds)
    return cfg


def validate(cfg: PilotConfig, envelope_seeds: list[int]) -> None:
    if cfg.split not in SPLITS:
        raise ConfigError(f"split must be one of {SPLITS}")
    if cfg.split == "test" and not cfg.final:
        raise ConfigError("the test split needs final = true, and is used once per envelope version (D-046)")
    unknown = [c for c in cfg.conditions if c not in CONDITIONS]
    if unknown:
        raise ConfigError(f"unknown conditions: {unknown}")
    not_approved = [s for s in cfg.seeds if s not in envelope_seeds]
    if not_approved:
        raise ConfigError(f"seeds {not_approved} are not in the envelope's approved seed set")
    if cfg.limit <= 0 or cfg.offset < 0:
        raise ConfigError("limit must be positive and offset non-negative")
