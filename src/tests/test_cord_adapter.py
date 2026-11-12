import pathlib

import pytest

from fenced_retry.adapters.cord import build_input_text, load_split, reference_fields
from fenced_retry.validator.checks import check_output

DATA = pathlib.Path(__file__).resolve().parents[2] / "data" / "cord"
needs_data = pytest.mark.skipif(not DATA.exists(), reason="CORD download not present")


def test_reference_fields_from_gt_parse():
    gt = {
        "menu": [{"nm": "Tea", "cnt": "x 1", "price": "5,000"}],
        "sub_total": {"subtotal_price": "5,000", "tax_price": "500"},
        "total": {"total_price": "5,500", "cashprice": "6,000"},
    }
    ref = reference_fields(gt)
    assert ref["total_price"] == "5,500"
    assert ref["subtotal_price"] == "5,000"
    assert ref["tax_price"] == "500"
    assert ref["cashprice"] == "6,000"
    assert ref["menu"] == [{"nm": "Tea", "cnt": "x 1", "price": "5,000"}]


def test_reference_fields_handle_list_and_missing():
    gt = {"menu": None, "sub_total": [{"subtotal_price": "9"}, {"tax_price": "1"}], "total": None}
    ref = reference_fields(gt)
    assert ref["subtotal_price"] == "9"
    assert ref["tax_price"] == "1"
    assert ref["total_price"] is None
    assert ref["menu"] == []


def test_build_input_text_orders_by_line_then_x():
    def seg(text, x, y):
        q = {"x1": x, "x2": x + 10, "x3": x + 10, "x4": x, "y1": y, "y2": y, "y3": y + 10, "y4": y + 10}
        return {"words": [{"quad": q, "text": text}], "category": "x"}

    lines = [seg("Total", 50, 100), seg("9", 200, 100), seg("Header", 10, 10)]
    assert build_input_text(lines) == "Header\nTotal 9"


@needs_data
def test_split_sizes_match_dataset_card():
    assert len(load_split(DATA / "train", "train")) == 800
    assert len(load_split(DATA / "validation", "validation")) == 100
    assert len(load_split(DATA / "test", "test")) == 100


@needs_data
def test_every_record_has_input_text():
    for rec in load_split(DATA / "validation", "validation"):
        assert rec.input_text.strip(), rec.record_id


@needs_data
def test_clean_records_pass_validators_on_reference():
    recs = load_split(DATA / "validation", "validation")
    clean = [r for r in recs if not check_output(r.reference, r.input_text)]
    assert len(clean) >= 90, f"only {len(clean)} of {len(recs)} clean on validation"
