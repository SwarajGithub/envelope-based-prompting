"""Prompt size estimate used by the pre-call check (HLD §6.4). No dependencies."""

from __future__ import annotations

import math


def estimate_prompt_tokens(messages: list[dict[str, str]]) -> int:
    """Conservative estimate: about three characters per token, plus a per-message allowance."""
    chars = sum(len(m.get("content", "")) for m in messages)
    return math.ceil(chars / 3) + 8 * len(messages)
