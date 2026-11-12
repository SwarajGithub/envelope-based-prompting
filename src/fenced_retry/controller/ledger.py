"""Failure ledger: at most one active directive per directive group (paper A2).

A new failure in a group replaces that group's directive. Directives are never
removed when a check later passes (HLD §4.3). Only template IDs and declared
parameters are stored, never free text.
"""

from __future__ import annotations


class Ledger:
    def __init__(self) -> None:
        self._entries: dict[str, str] = {}  # group -> template ID

    def update(self, group: str, template_id: str) -> None:
        self._entries[group] = template_id

    def entries(self) -> dict[str, str]:
        return dict(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def render(self, templates: dict[str, str]) -> list[str]:
        """Directive lines in group order, from the envelope's template library."""
        return [f"- {templates[self._entries[g]]}" for g in sorted(self._entries)]
