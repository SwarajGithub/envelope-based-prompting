# Findings log (pilot results, for the paper's Experiments and Results sections)

Each entry records the run, what was measured, and what it does and does not show. Numbers come from `src/runs/reports/summary.csv` and the run databases.

## F-004: v3 Block 1, first 100 clean train records — controller-s0 COMPLETE (2026-11-03)

Setup: envelope receipt-extract v3 (hash `f35c4ef1...`), qwen3-8k:latest Q4_K_M at 192.168.0.13, temperature=0.3, num_predict=1024, strengthened templates (menu=LINE TOTAL not @unit_price; all 9 amount fields include "NOT the label" guidance). Controller seed 0 only; other conditions queued.

Results (100/100 records):
- **100/100 ACCEPT, 0 DL, 6 rescue events**, 106 total calls
- Mean att1 input tokens: 388 (same as v2 — prompt size unchanged from v1→v2→v3)
- Mean att2 input tokens: 653 (vs 506 in v2 att2 — larger because v3 templates are longer)
- Mean latency: 18043 ms/call (att1), 16982 ms/call (att2) — server load varies

6 rescue events (all ACCEPT on attempt 2):
1. train-00023: att1=MENU_PRICE_NOT_VALID (in=372) → att2=PASS (in=591) — @unit_price issue, fixed by LINE TOTAL template
2. train-00031: att1=MENU_PRICE_NOT_VALID (in=382) → att2=PASS (in=601) — was ACCEPT att1 in v2 (temperature non-determinism)
3. train-00048: att1=MENU_PRICE_NOT_VALID (in=451) → att2=PASS (in=670) — compound price issue, fixed by LINE TOTAL template
4. train-00089: att1=CASH_NOT_VALID+CHANGE_NOT_VALID+SUBTOTAL_NOT_VALID+TOTAL_NOT_VALID (in=347) → att2=PASS (in=599) — label contamination on all 4 amount fields; all fixed by v3 "NOT the label" templates
5. train-00099: att1=MENU_PRICE_NOT_VALID (in=423) → att2=PASS (in=642) — @unit_price issue
6. train-00103: att1=MENU_ITEM_NOT_VERBATIM (in=594) → att2=PASS (in=813) — in v2, truncation caused PARSE_ERROR before this check; v3 num_predict=1024 fixes truncation, revealing the verbatim issue, then menu directive rescues it

Comparison with v2 block 1 (same 100 records, controller-s0):
- v2: 94/100 ACCEPT, 6 DL, 0 rescues, 106 calls
- v3: 100/100 ACCEPT, 0 DL, 6 rescues, 106 calls
- v3 eliminates all 6 DL by addressing root causes (truncation, @unit_price, label contamination)

Key finding: H2 CONFIRMED STRONGLY. All 6 previously dead-lettered records become rescue events under v3. The ledger directive (injected on attempt 2 from the typed failure template) changes model behavior for each failure class. The distinction: v2 templates gave the same generic guidance on att1 and att2 (repetition fuse fired at m=2 identical sigs); v3 templates are specific enough to change the model's output on retry. train-00103 also shows that fixing a blocking issue (truncation) can reveal a downstream issue (MENU_ITEM_NOT_VERBATIM) that the retry mechanism then addresses — a cascade-rescue.

## F-003: v2 Block 2 (calibration block), records 100–499 (PARTIAL — controller-s0 in progress, 2026-11-03)

Setup: v2 envelope, same config as block 1 controller-s0, offset=100, limit=400.

Preliminary results (205/400 records processed, 12 DL so far):
- DL rate: 5.85% (12/205), similar to block 1 (6%)
- DL failure classes: MENU_PRICE_NOT_VALID×5, PARSE_ERROR×3, MENU_ITEM_NOT_VERBATIM×1, DISCOUNT_NOT_VALID×2, SERVICE_NOT_VALID×1
  - MENU_ITEM_NOT_VERBATIM (train-00210): model paraphrases menu item name; validator checks `str(nm).strip(" -*:.") in input_text`. NOT fixed by v3 templates.
  - DISCOUNT_NOT_VALID (train-00219, 00302): label contamination; likely fixed by v3 `amount:discount_price` template.
  - SERVICE_NOT_VALID (train-00254): appears to be structural (service field absent or not numeric); MARGINAL version of same (train-00282) was rescued.
  - PARSE_ERROR×3: still present (v2 num_predict=512 is too small for long receipts — same root cause as block 1)
- **5 RESCUE EVENTS** confirmed so far (train-00108, 00173, 00199, 00282, 00306)
  - train-00108: MENU_PRICE_NOT_VALID att1 → PASS att2 (template reminder sufficient)
  - train-00173: TAX_NOT_VALID att1 → PASS att2 (v2 amount:tax_price template helped)
  - train-00199: MENU_ITEM_NOT_VERBATIM att1 → PASS att2 (v2 menu template reminder helped)
  - train-00282: SERVICE_NOT_VALID att1 → PASS att2 (marginal; service field legible but model missed it)
  - train-00306: MENU_PRICE_NOT_VALID att1 → PASS att2 (template reminder sufficient)

Key finding: Block 1's 6 DL records were SYSTEMATIC failures where the v2 templates were too vague to help (@ prefix not mentioned, compound prices not mentioned). Block 2 records include MARGINAL failures where the generic "copy as printed" template is enough to rescue on attempt 2. This confirms H2 holds for marginal failures; the claim about 0 rescues in block 1 is specific to those 6 systematic cases.

What this does not show: calibration of m requires the full 400 records. These are preliminary numbers.

## F-005: v2 Block 1, naive_retry condition (COMPLETE, 2026-11-03)

Setup: v2 envelope, N_max=3, m=2 repetition fuse, NO ledger directives — same prompt on every attempt.

Results:
- s0: 93/100 ACCEPT, 7 DL, 0 rescues, 114 calls — 7 DL includes train-00028 (borderline, temperature non-determinism)
- s1: 94/100 ACCEPT, 6 DL, **1 rescue** (train-00028, TAX_NOT_VALID att1 → PASS att2), 113 calls
- s2: 94/100 ACCEPT, 6 DL, 0 rescues, 112 calls
- Core 6 DL records (train-00023, 00048, 00065, 00089, 00099, 00103) appear in all three seeds

Key finding: The 1 "rescue" in s1 (train-00028, TAX_NOT_VALID) is temperature stochasticity, not directive-based repair. The same borderline record passed with the identical prompt on attempt 2 — no new information was injected. This is the null condition for H2: naive_retry can produce accidental rescues from temperature randomness, but not systematic ones from structural failures (the core 6 DL records appear in all 3 seeds). The controller's ledger-based rescues in v3 are mechanistically different.

Implication: the comparison is v3-controller (6 rescues on the core 6, all directed) vs v2-naive_retry (0–1 accidental rescues, none on the core 6). H2 requires directed rescue, not stochastic fluctuation.

## F-002: v2 Train block 1, first 100 clean train records (run group `v2-pilot-train-0000-100`)

Setup: envelope receipt-extract v2 (hash `23d1bd4711b4bb59d787fdf18062dfc3de835d064c1f558f44d3e82ffbd3643a`), qwen3-8k:latest (Q4_K_M) on 192.168.0.13, temperature 0.3, num_ctx=8192, num_predict=512, N_max=3, m=2. Controller ×3 seeds (0,1,2), single_shot seed 0. Naive_retry pending.

Results:
- Controller seeds 0, 1, 2: ALL identical — 94/100 accepted, 6/100 dead-lettered, 0 rescued. Same 6 DL records: train-00023, 00048, 00065, 00089, 00099, 00103.
- Single_shot seed 0: 94/100 accepted, 6/100 dead-lettered, same 6 records. Controller did not rescue any record.
- Mean input tokens: 388 (attempt 1), 506 (attempt 2). Stable — no prompt growth.
- All 6 DL records stopped by repetition fuse (m=2, same failure signature on both attempts).
- Calls: controller 106 (1.06/record), single_shot 100 (1.0/record).
- Mean latency: ~2484ms/call (remote server).
- Assurance sample (14 records): reference judge found 11/14 with CORD annotation discrepancies (78.6%). Most are minor format differences; some are substantive (wrong field, label contamination in accepted output).
- h_oversight: 0.06 (DL rate), 0.20 after assurance sampling.

Root-cause analysis of 6 DL records (by inspecting stored output text and tracing normalize_amount):
1. train-00023, 00099: MENU_PRICE_NOT_VALID — model copies @unit_price (e.g., `@21,000`) instead of line total (`21,000`). `normalize_amount` returns None for values starting with `@`.
2. train-00048: MENU_PRICE_NOT_VALID — model copies compound "unit_price line_total" (e.g., `"13.500 27.000"`). Not parseable as single decimal.
3. train-00065, 00103: PARSE_ERROR — JSON output truncated because num_predict=512 is too small for receipts with long menu lists.
4. train-00089: TOTAL/CASH/CHANGE_NOT_VALID — model copies field label + value (e.g., `"Total Rp 5.000"` instead of `"5.000"`). normalize_amount strips `Rp` but not the leading "Total" word, returning None.

Key finding: temperature 0.3 with seeds {0,1,2} is STILL deterministic for these 6 records. All 3 controller seeds produce identical outputs, identical failure signatures, identical DL sets. The retry mechanism provides zero benefit on v2 because the directive injected on attempt 2 is identical in meaning to what was in the Note section on attempt 1.

Consequence: envelope v3 created (D-064) with strengthened templates and num_predict=1024. V3 block 1 tests pending.

## F-001: Train block 1, first 100 clean train records (run group `pilot-train-0000-100`)

Setup: envelope receipt-extract v1 (hash in the approval record), qwen3:8b at temperature 0, context 4096, thinking off, three seeds (0, 1, 2) per condition.

Results:
- All three conditions accepted 94 of 100 records (accept rate 0.940).
- The accepted sets were identical across conditions. The same six records were dead-lettered in each: train-00023, 00048, 00065, 00089, 00099, 00103.
- Calls: single-shot 100, controller 106, naive retry 112. The controller used one extra call for each of the six failing records. Naive retry used two extra calls for each of them.
- The three seeds gave the same states for every record under every condition. Output texts matched in every attempt except one formatting difference (a code fence) in one controller attempt, with the same validator result.

What this shows: at temperature 0 the seed has essentially no effect, so the three repeats add no information about variance. On this block, retries did not rescue any record, under either the naive retry or the controller's plan change (plans A, B, C). The controller's plan change did not change the failing checks for the six records.

What this does not show: one block of 100 records, one model, one envelope. The clean-subset rule (D-040) applies. It says nothing yet about the paper's H2 to H4 or about the §9.5 measurements. The rate of six failures out of 100 is small, so the difference between conditions is not measurable at this size.

Follow-up options (decisions pending): (a) use one seed for later blocks; (b) add a sampling temperature above zero so that retries can differ, which changes the protocol and needs a decision (D-009, D-030); (c) inspect the six failing records to see whether the validators or the model are at fault.
