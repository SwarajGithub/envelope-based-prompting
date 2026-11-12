# Evaluation protocol (draft, not frozen)

Status: draft for review. Nothing here is frozen until the decisions in the last section are made. Freezing happens before the test split is touched (paper §9.4).

## Task and splits
- Task: receipt field extraction on CORD v1 (nine total-related fields and the menu; D-031, D-035).
- Train split: 800 records. Used for calibration (θ_drift, m, φ, φ_s). Block 1 is records 0 to 99 (clean subset, D-040). A second train block would use records 100 onward.
- Validation split: 100 records. Used for development and debugging only, never for calibration or the reported numbers.
- Test split: 100 records. Run once, with final = true (D-055), after freezing.

## Conditions
- single_shot: one call, initial plan A, no retry (baseline).
- naive_retry: the same prompt up to N_max attempts, no ledger, no plan change (baseline).
- frozen: the controller with the plan fixed to S_1 (plan A). Ledger, note choice, and fuse unchanged (M8, paper §9.2).
- controller: the full design.

## Metrics (paper §9.3, §9.5; code in src/fenced_retry/harness/metrics.py)
- Success within limits: accept rate, rescues after the first attempt.
- Invariant adherence by position: pass rate and mean input tokens by attempt index.
- Containment: fuse stops by tier.
- Repetition count: histogram of the largest repeated signature per record (calibration of m).
- Oversight load h = u + s(1 − u), with u the dead-letter fraction and s the assurance sample rate over accepted outputs.
- Cost: calls per record, input and output tokens, mean latency.
- Silent failure: human-reviewed and reference-judged counts, reported separately.
- Review effort Rev_E: mean minutes per human review.
- Not computed, with reasons recorded in the metrics output: fuse precision and recall (needs a no-fuse counterfactual), drift–failure correlation (J_struct not stored), dead-letter delay (no step timestamps), ρ (needs clustered reviews).

## Repeats
- Temperature 0 makes seeds almost identical (D-030; block 1 showed one formatting difference across seeds). Use one seed for the test split.
- Temperature experiments are at the end, after the other parts, and are labelled exploratory (D-057).

## Thresholds
- θ_drift, m: calibrated on the train split and frozen before the test split (§9.4). Currently provisional: m = 2, θ_drift = 0.8.
- φ, φ_s: analytic rule (§9.4). φ is computed (0.12 from the block-1 rate); φ_s needs reviewed samples.
- Assurance: α = 0.05, ε = 0.2, c = 14, j = 2, floor 14 reviews per block (D-058, D-060).

## Decisions made (2026-10-19)
1. Second train block: records 100 to 499, controller only, one seed (configs/train-block-02.toml).
2. Test split: the full CORD test split, 100 records.
3. Repeats on the test split: one seed, temperature 0.
4. Silent-failure measure: reference judge for every sample, plus human review of the sample (14 per block).
5. H2a and H2b baselines: left out of v1.

## Open points found while drafting
- θ_drift has no runtime role (paper L2). It is checked when a plan is approved, so the run data cannot calibrate it. The plan-drift value J_struct is not stored per attempt. The paper should say θ_drift is set by the authors at approval, not calibrated from runs, unless a runtime drift measure is added.
- Block 2 gives calibration data for m only.
- Silent-failure measure: human verdicts are the silent-failure count. Reference verdicts are reported as a reference-disagreement rate (D-061). Both appear in metrics.
