from decimal import Decimal

import pytest

from fenced_retry.validator.numbers import amounts_in_text, normalize_amount


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("1,591,600", Decimal("1591600")),
        ("40,000.", Decimal("40000")),
        ("226,500.00", Decimal("226500.00")),
        ("35.000,00", Decimal("35000.00")),
        ("154 000", Decimal("154000")),
        ("177.870", Decimal("177870")),
        ("-Rp 34,363", Decimal("34363")),
        ("Rp. 50,000", Decimal("50000")),
        (":9,000", Decimal("9000")),
        ("Discount(0%)", None),
        ("", None),
        (None, None),
    ],
)
def test_normalize_amount(raw, expected):
    assert normalize_amount(raw) == expected


def test_amounts_in_text_finds_spaced_and_plain_amounts():
    found = amounts_in_text("SUBTOTAL 154 000\nTOTAL 177.870 Cash 200,000")
    assert {Decimal("154000"), Decimal("177870"), Decimal("200000")} <= found
