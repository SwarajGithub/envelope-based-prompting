"""Audit CORD reference values against the input text (M1).

For each record, every reference field that the validators check is compared with
the input text. A reference value that does not appear in the text cannot be
supported by any model output, so such records are listed for exclusion from the
pilot (D-040). Writes one JSON file per split under runs/.

Usage (from src/, with the venv active):
    python scripts/audit_references.py --data ../data/cord
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fenced_retry.adapters.cord import load_split  # noqa: E402
from fenced_retry.validator.checks import check_output  # noqa: E402


def audit(data_dir: pathlib.Path, out_dir: pathlib.Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for split_name in ("train", "validation", "test"):
        split_dir = data_dir / split_name
        if not split_dir.exists():
            print(f"skip {split_name}: {split_dir} not found")
            continue
        records = load_split(split_dir, split_name)
        excluded = []
        causes: collections.Counter[str] = collections.Counter()
        for rec in records:
            failing = sorted(check_output(rec.reference, rec.input_text))
            if failing:
                excluded.append({"record_id": rec.record_id, "checks": failing})
                causes.update(failing)
        clean = len(records) - len(excluded)
        report = {
            "split": split_name,
            "records": len(records),
            "clean": clean,
            "excluded": len(excluded),
            "failing_checks": dict(causes),
            "excluded_records": excluded,
        }
        path = out_dir / f"reference_audit_{split_name}.json"
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"{split_name}: {clean}/{len(records)} clean, {len(excluded)} excluded -> {path}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=pathlib.Path, default=ROOT.parent / "data" / "cord")
    parser.add_argument("--out", type=pathlib.Path, default=ROOT / "runs")
    args = parser.parse_args()
    audit(args.data, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
