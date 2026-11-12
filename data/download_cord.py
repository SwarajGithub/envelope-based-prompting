#!/usr/bin/env python3
"""Download CORD v1 (naver-clova-ix/cord-v1, licensed CC BY 4.0) into ./cord/.

Layout written:
  cord/<split>/images/<id>.png   receipt image
  cord/<split>/gt/<id>.json      ground-truth annotation (JSON text, unchanged)

Run on your own machine:
  pip install datasets pillow
  python download_cord.py
"""
import pathlib
import sys

from datasets import load_dataset

OUT = pathlib.Path(__file__).resolve().parent / "cord"


def main() -> int:
    ds = load_dataset("naver-clova-ix/cord-v1")
    for split, rows in ds.items():
        img_dir = OUT / split / "images"
        gt_dir = OUT / split / "gt"
        img_dir.mkdir(parents=True, exist_ok=True)
        gt_dir.mkdir(parents=True, exist_ok=True)
        for i, row in enumerate(rows):
            rid = f"{split}-{i:05d}"
            row["image"].convert("RGB").save(img_dir / f"{rid}.png")
            gt = row["ground_truth"]
            text = gt if isinstance(gt, str) else __import__("json").dumps(gt, ensure_ascii=False)
            (gt_dir / f"{rid}.json").write_text(text, encoding="utf-8")
        print(f"{split}: {len(rows)} records -> {OUT / split}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
