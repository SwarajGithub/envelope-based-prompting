"""Data types shared by the controller (paper Appendix B and C)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class Caller(Protocol):
    """What the controller needs from the LLM client (LLMClient satisfies this)."""

    def chat(self, messages: list[dict[str, str]], project_name: str | None = None) -> Any: ...


@dataclass(frozen=True)
class Attempt:
    index: int
    plan_id: str
    call_id: str | None
    status: str                 # ok | timeout | error | truncated | too_large
    failing: frozenset[str]     # F_i: failing check IDs (empty when accepted)
    signature: str | None       # sigma = hash of sorted F_i
    output_text: str | None
    tier: str | None = None     # fuse tier that stopped the next attempt, if any
    latency_ms: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass
class Outcome:
    record_id: str
    state: str                  # ACCEPT | DEAD_LETTER
    reason: str
    output: dict[str, Any] | None = None
    attempts: list[Attempt] = field(default_factory=list)
    ledger: dict[str, str] = field(default_factory=dict)
    shadow: bool = False        # shadow mode: logged, not used (D-046, paper §4.4)

    @property
    def accepted_for_use(self) -> bool:
        return self.state == "ACCEPT" and not self.shadow
