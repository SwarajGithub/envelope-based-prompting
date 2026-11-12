"""Deterministic checks for receipt extraction (design/HLD.md §10).

Each check ID belongs to exactly one group (paper A2). All checks run on every
output, so the failing set is complete. No model calls are made here.

Arithmetic checks (menu sum against subtotal, subtotal plus tax minus discount
against total) are not in v1. CORD references contain rounding lines, unlabelled
item discounts and voids, so arithmetic fails on reference values (D-039).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fenced_retry.adapters.cord import AMOUNT_FIELDS, MENU_KEYS
from fenced_retry.validator.numbers import amounts_in_text, normalize_amount

AMOUNT_CHECK = {
    "total_price": "TOTAL_NOT_VALID",
    "subtotal_price": "SUBTOTAL_NOT_VALID",
    "tax_price": "TAX_NOT_VALID",
    "service_price": "SERVICE_NOT_VALID",
    "discount_price": "DISCOUNT_NOT_VALID",
    "cashprice": "CASH_NOT_VALID",
    "changeprice": "CHANGE_NOT_VALID",
    "creditcardprice": "CARD_NOT_VALID",
    "emoneyprice": "EMONEY_NOT_VALID",
}

# Check ID -> directive group (A2).
GROUP_OF = {
    **{cid: f"amount:{f}" for f, cid in AMOUNT_CHECK.items()},
    "MENU_ITEM_NOT_VERBATIM": "menu",
    "MENU_PRICE_NOT_VALID": "menu",
    "SCHEMA_MISSING_FIELD": "schema",
    "PARSE_ERROR": "schema",
}

EXPECTED_KEYS = (*AMOUNT_FIELDS, "menu")


def check_output(output: dict[str, Any], input_text: str) -> set[str]:
    """Return the set of failing check IDs for one output."""
    failing: set[str] = set()

    for key in EXPECTED_KEYS:
        if key not in output:
            failing.add("SCHEMA_MISSING_FIELD")

    amounts = amounts_in_text(input_text)

    for field_name, check_id in AMOUNT_CHECK.items():
        value = output.get(field_name)
        if value is None:
            continue
        amount = normalize_amount(value)
        if amount is None or amount not in amounts:
            failing.add(check_id)

    menu = output.get("menu") or []
    for item in menu:
        name = item.get("nm")
        if name is not None and str(name).strip(" -*:.") not in input_text:
            failing.add("MENU_ITEM_NOT_VERBATIM")
        price = item.get("price")
        if price is not None:
            p = normalize_amount(price)
            if p is None or p not in amounts:
                failing.add("MENU_PRICE_NOT_VALID")

    return failing
