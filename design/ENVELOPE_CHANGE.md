# Runbook: introducing a new envelope version

Use this whenever anything in an approved envelope changes: plans, the step graph, invariants, templates, fallback items, or any value in Θ (including the model, its digest, `num_ctx`, seeds, thresholds, and limits). Paper §4.4 and A7 make each such change an approved change. Nothing in this runbook is optional.

## Rules

1. An approved envelope file is never edited in place. A change produces a new version (`receipt-extract-v2.json`, and so on).
2. The previous version stays in `envelopes/` with its approval record. Results are linked to the hash of the version that produced them.
3. The approver is always a person (D-045). The code never approves.
4. The test split is used once per envelope version. A version built after test results exist is exploratory and must be reported as such.
5. Calibration (φ, φ_s, m, θ_drift) uses the train split only, never validation or test.

## Steps

1. **Record the reason.** Add an entry to `DECISIONS.md` (next free D-number) with what changes and why, and mark any value that is provisional.

2. **Copy the current approved version.**
   ```
   cd src
   cp envelopes/receipt-extract-v1.json envelopes/receipt-extract-v2.json
   ```
   Set `"version": "v2"` inside the new file. Do not touch the v1 file or its approval record.

3. **Make the change in v2 only.** If a fallback item is a prior envelope version, that prior file must stay in `envelopes/`.

4. **If the model or its digest changes:**
   - Run `curl -s http://localhost:11434/api/tags` and copy the new digest into `theta.model.digest`.
   - Run `ollama show <model>` and confirm the capabilities and parameters match what the envelope assumes.
   - Re-run the smoke test with the new model (step 7) before anything else.

5. **Update the notes block** in v2 so each provisional value says what it is waiting for.

6. **Check the structure and lint:**
   ```
   python -m fenced_retry.envelope verify envelopes/receipt-extract-v2.json
   ```
   Expect `no approval record` until step 9. Any lint problem must be fixed before approval.

7. **Run the tests and the smoke test:**
   ```
   pytest -v
   python -m fenced_retry.llm.smoke --expected-digest <digest in v2>
   ```
   Tests that name the envelope file must be updated if the file name changes.

8. **Shadow run (required before live use, paper §4.4).** Run v2 on a sample of validation records. Log outputs and do not use them as results. Compare the failure rate and the checks that fire against v1. This requires the shadow mode from M4 to be in place.

9. **Approve (you, not the code):**
   ```
   python -m fenced_retry.envelope approve envelopes/receipt-extract-v2.json --approver "Your Name" --note "<what changed and why>"
   python -m fenced_retry.envelope verify envelopes/receipt-extract-v2.json
   ```
   Verify must print `verified`. Keep the approval file with the envelope.

10. **Calibrate if thresholds changed.** Run calibration on the train split only (M7), then re-approve if any value changed after step 9. Each re-approval is a new approval record with a new hash.

11. **Run the comparison.** Run v1 and v2 on the validation split with three seeds each, from the same repeats. Record both in the run database, so each result links to its envelope hash.

12. **Test split (only if the change is final).** Run v2 once on the test split. If the test split was already used for v1, label the v2 run as exploratory in the results.

13. **Start the run with the new version.** The run configuration must name the envelope file. The controller checks the hash at batch start and per record (M4). A mismatch halts the run.

14. **Update the paper** (M9): add the version to the version history, state what changed and why, and report results per version.

## Checklist (copy into the change's DECISIONS entry)

- [ ] Reason and changed values recorded in DECISIONS.md
- [ ] New file copied, version field updated, v1 untouched
- [ ] Digest updated if the model changed, and `ollama show` checked
- [ ] Notes block updated for provisional values
- [ ] `verify` lint clean
- [ ] `pytest -v` passes; smoke test passes with the pinned digest
- [ ] Shadow run done and compared with v1
- [ ] Approved by a person; `verify` prints verified
- [ ] Calibration (if any) on train only; re-approved if values changed
- [ ] v1 and v2 compared on validation with three seeds
- [ ] Test split use recorded (once, or exploratory)
- [ ] Run configuration names the new envelope
- [ ] Paper version history updated
