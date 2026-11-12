# Envelope-Bounded Retry with Typed Failure Ledgers for Unattended LLM Pipelines

**Paper:** [Paper](docs/Envelope-Bounded%20Retry%20with%20Typed%20Failure%20Ledgers.pdf)

This repository contains the source code, envelope definitions, run configs, and design documents for the paper. The system restricts runtime LLM behavior to pre-approved plans with bounded prompt length and guaranteed termination. When a record fails, the controller injects a typed, template-based directive on the next attempt — a "failure ledger" — so the model gets targeted repair instructions rather than the same prompt again. A per-record fuse and a fleet-level circuit breaker bound how much can be spent on any single record or batch.

All experiments use CORD v1 receipt extraction via Ollama-served `qwen3-8k:latest` (Q4_K_M).

---

## Repository layout

```
envelope-based-prompting/
  docs/
    paper.html                    the paper
    figure1_execution_loop.svg     Figure 1
    figure2_drift_example.svg      Figure 2
  src/
    fenced_retry/                  Python package (controller, ledger, validator, harness)
    envelopes/                     envelope JSON files + approval records (v1, v2, v3)
    configs/                       TOML run configs for each experimental block
    runs/                          SQLite run databases (gitignored — generated locally)
    scripts/
      audit_references.py          identify un-extractable records (clean-split tool)
    tests/                         unit tests
    requirements.txt
    pytest.ini
  data/
    README.md                      dataset notes and licence
    download_cord.py               downloads CORD v1 from Hugging Face
    requirements.txt               deps for the download script
    cord/                          CORD v1 dataset — NOT in repo, download below
  design/
    HLD.md                         architecture overview
    DECISIONS.md                   decision log (D-nnn references in the paper)
    FINDINGS.md                    per-run findings annotated for the paper
    PROTOCOL.md                    experimental protocol
    RUNBOOK.md                     step-by-step run guide
    ENVELOPE_CHANGE.md             envelope versioning procedure
```

---

## Prerequisites

- Python 3.13+
- [Ollama](https://ollama.com/) running and accessible from your machine
- Model pulled: `qwen3-8k:latest` (Q4_K_M)
  - Full model digest: `a68fd3999784d0ff878cb799bfe1a6f19f0ddf405651728609d687604ce10955`
  - The paper ran against a remote GPU at a private address; to reproduce locally, update the `base_url` field in the envelope files to your Ollama endpoint

---

## 1. Download the dataset

The CORD v1 dataset is **not included** in this repository. Download it yourself from Hugging Face (CC BY 4.0 licence):

```bash
cd data
python3.13 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python download_cord.py
deactivate
```

This writes `data/cord/<split>/images/*.png` and `data/cord/<split>/gt/*.json`. Download is a few hundred MB. Expected split sizes: 800 train / 100 validation / 100 test.

---

## 2. Python environment

```bash
cd src
python3.13 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

All commands below assume you are in `src/` with the venv active.

---

## 3. Identify clean records

The validator requires that every reference field value appears verbatim in the input text. Run the reference audit to identify which records pass this check per split:

```bash
python scripts/audit_references.py --data ../data/cord
```

Output is written to `runs/reference_audit_{train,validation,test}.json`. The paper uses the 800 clean train records and 84 clean test records identified by this audit.

---

## 4. Run the experiments

Run configs in this order. Each config corresponds to one experimental block in the paper.

### v2 block 1 — first 100 clean train records (Table 1)

```bash
python -m fenced_retry.cli run --config configs/v2-train-block-01.toml
```

### v2 block 2 — calibration block, 400 records (§9.5.2)

```bash
python -m fenced_retry.cli run --config configs/v2-train-block-02.toml
```

### Calibrate fleet-breaker threshold (§9.5.2)

```bash
python -m fenced_retry.cli calibrate --prefix v2-pilot-train-0100-400
```

### v3 block 1 — all three conditions, 100 clean train records (Table 3)

```bash
python -m fenced_retry.cli run --config configs/v3-train-block-01.toml
```

### v3 test split — held-out 84 clean test records (Table 5)

> **Run this only once**, after completing all train-split analysis. The config is marked `final = true` to preserve the held-out guarantee.

```bash
python -m fenced_retry.cli run --config configs/v3-test.toml
```

---

## 5. Reports and assurance

Generate a summary CSV after any block:

```bash
python -m fenced_retry.cli report
# writes runs/reports/summary.csv
```

Plan an assurance sample for a completed controller run:

```bash
python -m fenced_retry.cli assure --run v3-pilot-train-0000-100-controller-s0
```

Run the automated reference judge on the sample:

```bash
python -m fenced_retry.cli judge \
  --run v3-pilot-train-0000-100-controller-s0 \
  --data ../data/cord/train
```

Run the interactive human review (requires terminal input):

```bash
python -m fenced_retry.cli review \
  --run v3-pilot-train-0000-100-controller-s0 \
  --data ../data/cord/train
```

---

## 6. Envelope hash verification

The canonical envelope hash is `SHA-256(json.dumps(envelope, sort_keys=True, separators=(",",":" )))` — **not** raw file bytes.

| Version | File | Canonical SHA-256 |
|---------|------|-------------------|
| v1 | `envelopes/receipt-extract-v1.json` | *(see approval record)* |
| v2 | `envelopes/receipt-extract-v2.json` | `23d1bd4711b4bb59d787fdf18062dfc3de835d064c1f558f44d3e82ffbd3643a` |
| v3 | `envelopes/receipt-extract-v3.json` | `f35c4ef11fc3eac5a1251edd533540076e085a80c0a1419cd7469cf81a8356db` |

To verify v3:

```bash
python - <<'EOF'
from fenced_retry.envelope.model import load_envelope
from fenced_retry.envelope.canonical import envelope_hash
print(envelope_hash(load_envelope("envelopes/receipt-extract-v3.json")))
EOF
```

---

## 7. Run databases

Each run produces a SQLite file in `runs/` (e.g. `v3-pilot-train-0000-100-controller-s0.sqlite`). Tables: `runs`, `attempts`, `dead_letters`, `ledger_entries`, `samples`, `reviews`, `events`, `breaker_events`.

A run can be resumed if interrupted — re-run the same command and it skips completed records.

```bash
sqlite3 runs/v3-pilot-train-0000-100-controller-s0.sqlite \
  "SELECT record_id, attempt_index, status FROM attempts WHERE attempt_index > 1;"
```

---

## 8. Tests

```bash
pytest
```

---

## Citation

```
Shekhar, S. (2026). Envelope-Bounded Retry with Typed Failure Ledgers for
Unattended LLM Pipelines. [Preprint]
```
