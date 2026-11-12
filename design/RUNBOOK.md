# Run sheet: what to run, and when

Work from `src/` on your Mac, with the venv active (`source .venv/bin/activate`). Ollama must be running (`ollama serve`, or the Ollama app).

Before any run:
```
ollama ps            # qwen3:8b should be listed; if not, the first call loads it (about 25 s)
pytest -q            # should show 72 passed (or more)
python -m fenced_retry.envelope verify envelopes/receipt-extract-v1.json   # should print "verified"
```
If any of these fail, stop and send me the output.

## Block 1: validation split, clean records (available now)

This is the first pilot block. It covers the 93 clean validation records (D-040), with the current controller and envelope v1. Expected time: about 15 to 25 minutes.

```
python -m fenced_retry.cli smoke --n 93 --project pilot
```

When it finishes, send me the run ID printed on the first line (for example `smoke-2026...`). I can read the run database directly from here, so you don't need to paste the output. The shared usage file also gets one row per call.

Do not run any other command while this one is running.

## Block 2: train split, calibration (M6, available now)

This runs the three conditions (controller, single-shot, naive retry) on the first 100 clean train records, each with the three approved seeds. That is nine runs, each saved to its own database.

Before starting, check Ollama is up (`ollama ps`).

```
python -m fenced_retry.cli run --config configs/train-block-01.toml
python -m fenced_retry.cli report
```

Expect several hours in total (about one hour per seed). Keep the machine awake. If it stops, run the same two commands again: finished runs are skipped and the interrupted record restarts.

When it finishes, send me the lines printed by `run` and the `report` output, or just tell me it finished. Reports are written to `src/runs/reports/`.

## Later blocks (after M6 is built)

M6 adds these before the next blocks can run: crash resume, the run configuration file, the single-shot and naive retry baselines, and the output reports. I'll post the exact commands here when M6 is ready. The order will be:

1. Train split, calibration, in blocks of about 100 records (D-052), three seeds each.
2. Validation split, the baselines and the controller, three seeds each, for debugging.
3. Test split, once, only after the envelope is frozen (D-046).

## If something goes wrong

- The run stops with `BatchHalted` or a digest mismatch: stop. Send me the message. Do not edit the envelope.
- Ollama connection refused: start Ollama, then run the same command again. The run stops at the first failed call and records what it finished.
- Mac goes to sleep mid-run: the run stops. Keep the machine awake (or plug in) during a block. I'll add crash resume in M6 so an interrupted block can continue.
