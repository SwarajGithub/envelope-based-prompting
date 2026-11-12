"""Read CORD v1 records and build the input text and reference fields.

Input text is built from CORD's word-level annotations (``valid_line``),
ordered into lines by vertical position. OCR is not used (D-004, D-036).

Reference fields come from ``gt_parse`` (D-031, D-035). CORD v1 has no
merchant, date, or currency labels, so those are not extracted.
"""

from __future__ import annotations

import json
import pathlib
import statistics
from dataclasses import dataclass, field
from typing import Any

# Flat output fields and the CORD source for each (D-031).
AMOUNT_FIELDS = (
    "total_price",
    "subtotal_price",
    "tax_price",
    "service_price",
    "discount_price",
    "cashprice",
    "changeprice",
    "creditcardprice",
    "emoneyprice",
)

MENU_KEYS = ("nm", "cnt", "price")


@dataclass
class Record:
    record_id: str
    split: str
    input_text: str
    reference: dict[str, Any] = field(default_factory=dict)


def _line_items(valid_line: list[dict]) -> list[tuple[float, float, str]]:
    """Return (y_center, x_left, text) for each annotated text segment."""
    items = []
    for seg in valid_line or []:
        words = seg.get("words") or []
        if not words:
            continue
        texts = [w.get("text", "") for w in words]
        ys = []
        xs = []
        for w in words:
            q = w.get("quad") or {}
            y_vals = [q.get(k) for k in ("y1", "y2", "y3", "y4") if q.get(k) is not None]
            x_vals = [q.get(k) for k in ("x1", "x2", "x3", "x4") if q.get(k) is not None]
            if y_vals:
                ys.append((min(y_vals), max(y_vals)))
            if x_vals:
                xs.append(min(x_vals))
        if not ys:
            continue
        y_center = sum((a + b) / 2 for a, b in ys) / len(ys)
        x_left = min(xs) if xs else 0
        items.append((y_center, x_left, " ".join(t for t in texts if t)))
    return items


def build_input_text(valid_line: list[dict]) -> str:
    """Join annotated segments into lines, top to bottom, left to right."""
    items = _line_items(valid_line)
    if not items:
        return ""
    heights = []
    for seg in valid_line or []:
        for w in seg.get("words") or []:
            q = w.get("quad") or {}
            ys = [q.get(k) for k in ("y1", "y2", "y3", "y4") if q.get(k) is not None]
            if ys:
                heights.append(max(ys) - min(ys))
    tol = 0.5 * statistics.median(heights) if heights else 10.0

    items.sort(key=lambda t: (t[0], t[1]))
    lines: list[list[tuple[float, float, str]]] = []
    for item in items:
        if lines and abs(item[0] - lines[-1][-1][0]) <= tol:
            lines[-1].append(item)
        else:
            lines.append([item])
    rendered = []
    for line in lines:
        line.sort(key=lambda t: t[1])
        rendered.append(" ".join(t[2] for t in line))
    return "\n".join(rendered)


def _as_dicts(value: Any) -> list[dict]:
    """CORD sometimes stores one object as a dict and sometimes as a list."""
    if value is None:
        return []
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [v for v in value if isinstance(v, dict)]
    return []


def _merged(value: Any) -> dict[str, Any]:
    """Merge a dict or a list of dicts; earlier entries win on conflicts."""
    out: dict[str, Any] = {}
    for d in _as_dicts(value):
        for k, v in d.items():
            if out.get(k) in (None, "") and v not in (None, ""):
                out[k] = v
    return out


def reference_fields(gt_parse: dict[str, Any]) -> dict[str, Any]:
    """Map CORD ground truth to the flat output schema."""
    sub = _merged(gt_parse.get("sub_total"))
    tot = _merged(gt_parse.get("total"))
    ref: dict[str, Any] = {}
    for k in ("subtotal_price", "tax_price", "service_price", "discount_price"):
        ref[k] = sub.get(k)
    for k in ("total_price", "cashprice", "changeprice", "creditcardprice", "emoneyprice"):
        ref[k] = tot.get(k)
    menu = []
    for item in _as_dicts(gt_parse.get("menu")):
        menu.append({k: item.get(k) for k in MENU_KEYS})
    ref["menu"] = menu
    return ref


def load_split(split_dir: pathlib.Path, split: str) -> list[Record]:
    """Load every record in a split (for example ``data/cord/train``)."""
    records = []
    for gt_path in sorted((split_dir / "gt").glob("*.json")):
        data = json.loads(gt_path.read_text(encoding="utf-8"))
        records.append(
            Record(
                record_id=gt_path.stem,
                split=split,
                input_text=build_input_text(data.get("valid_line") or []),
                reference=reference_fields(data.get("gt_parse") or {}),
            )
        )
    return records
