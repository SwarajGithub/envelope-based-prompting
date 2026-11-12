"""Amount normalisation (D-038).

CORD writes amounts with thousands separators as either ``,`` or ``.``, sometimes
with a trailing punctuation mark or a sign. Amounts are compared as Decimal after
normalisation, so ``40,000.``, ``40.000`` and ``40000`` are the same amount.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

_GROUPED = re.compile(r"^-?\d{1,3}([.,]\d{3})+$")
_DECIMAL = re.compile(r"^-?\d+[.,]\d{1,2}$")
_SPACED = re.compile(r"^-?\d{1,3}(?:\s\d{3})+$")
_TOKEN = re.compile(r"-?\d{1,3}(?: \d{3})+|-?\d[\d.,]*\d|-?\d")


def normalize_amount(raw: object) -> Decimal | None:
    """Return the amount as Decimal, or None if the value is not numeric."""
    if raw is None:
        return None
    if isinstance(raw, (int, float, Decimal)):
        try:
            return Decimal(str(raw))
        except InvalidOperation:
            return None
    s = str(raw).strip()
    s = re.sub(r"(?i)\b(rp|idr)\.?", "", s)
    s = s.lstrip(" :.-")
    s = s.rstrip(" .,;:")
    if _SPACED.match(s):
        s = re.sub(r"\s", "", s)
    s = s.replace(" ", "")
    if not s:
        return None
    if "," in s and "." in s:
        # Both marks present: the last one is the decimal separator.
        decimal_mark = "," if s.rfind(",") > s.rfind(".") else "."
        group_mark = "." if decimal_mark == "," else ","
        s = s.replace(group_mark, "")
        if decimal_mark == ",":
            s = s.replace(",", ".")
    elif _GROUPED.match(s):
        s = s.replace(".", "").replace(",", "")
    elif _DECIMAL.match(s):
        s = s.replace(",", ".")
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def amounts_in_text(text: str) -> set[Decimal]:
    """All numeric amounts that appear in the input text, normalised."""
    found: set[Decimal] = set()
    for token in _TOKEN.findall(text):
        value = normalize_amount(token)
        if value is not None:
            found.add(value)
    return found
