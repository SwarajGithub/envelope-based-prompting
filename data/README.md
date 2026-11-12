# Data: Option A (receipt field extraction)

Dataset: CORD v1 (Park et al., NeurIPS 2019 Document Intelligence Workshop), published by NAVER CLOVA, licensed CC BY 4.0. Hosted at https://huggingface.co/datasets/naver-clova-ix/cord-v1.

## Why CORD and not SROIE

Both are receipt datasets. CORD is used here because its licence (CC BY 4.0) is clear. SROIE's licence terms were not verified, so it stays out of this folder until checked.

## Download (run on your machine)

The sandbox used to prepare this folder has no network access, so the download has to run locally:

```
cd data
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python download_cord.py
deactivate
```

This writes `data/cord/<split>/images/*.png` and `data/cord/<split>/gt/*.json`. The first run downloads a few hundred MB.

## Things to verify on the first run

- The script assumes the dataset has columns `image` and `ground_truth`. If `load_dataset` reports a different column name, update the script.
- Record counts per split are printed. Check them against the dataset card before building splits.
- Each `gt/*.json` is the annotation unchanged. Fields such as the total and date are what the validators (Area 3 in the plan) will check.

## Next steps once downloaded

- Hold out a calibration split and a test split before any tuning, as in §9.4 of the paper.
- Write the validator checks against the ground-truth schema, and confirm the checks pass on ground truth before using them on model outputs.
