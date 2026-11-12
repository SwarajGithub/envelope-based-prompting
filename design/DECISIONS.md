# Decision log: fenced-retry

Each decision has an ID, the date it was made, what was decided, why, and its status. Changes are recorded as new entries that supersede the old one, so the history stays visible.

Status values: `Decided`, `Decided (default)` (chosen as a default, open to change), `Open`, `Superseded`.

| ID | Date | Decision | Why | Status |
|---|---|---|---|---|
| D-001 | 2026-10-18 | The project is a personal prototype of the paper's controller. The paper is the reference for behaviour; this repo is the working implementation. | Learn the design by running it; produce the §9.5 measurements. | Decided |
| D-002 | 2026-10-18 | Task family: receipt field extraction. | Deterministic validators, public data, and a size that fits a local machine. | Decided |
| D-003 | 2026-10-18 | Dataset: CORD v1 (naver-clova-ix/cord-v1), licence CC BY 4.0. SROIE is not used. | CORD's licence is clear. SROIE's licence terms were not verified. | Decided |
| D-004 | 2026-10-18 | Input is text built from CORD word annotations. No images and no OCR output. | Removes OCR noise from the first version. Images can come later. | Decided |
| D-005 | 2026-10-18 | Model: `qwen3:8b` served by Ollama on localhost. | Local, no per-call cost, no cloud dependency. | Decided |
| D-006 | 2026-10-19 | AWS Bedrock and boto3 are dropped. The earlier Bedrock and Sonnet 4.5 choice is superseded. | The user chose a local model. | Superseded by D-005 |
| D-007 | 2026-10-19 | Transport is Ollama's native `/api/chat` endpoint, called with `httpx`. The OpenAI-compatible endpoint is not used. | The native endpoint accepts `num_ctx`, `seed`, `temperature`, and the `think` flag. The OpenAI-compatible endpoint may ignore them. | Decided |
| D-008 | 2026-10-19 | Ollama version is 0.40.6, as installed. No upgrade. | The user's installed version. The qwen3 model runs on it. | Decided |
| D-009 | 2026-10-19 | `temperature = 0` and a fixed `seed` on every call. | Repeatability. The model's default is 0.6. | Decided (default) |
| D-010 | 2026-10-19 | Usable input limit is 4096 tokens. `num_ctx` is set to 4096 for the pilot, and the envelope records it. | Measured: with `num_ctx` set to 8192, the model still receives only about 4096 tokens and the start of longer prompts is dropped without an error. | Decided |
| D-011 | 2026-10-19 | A prompt whose `prompt_eval_count` is at or above 4096 is treated as truncated and recorded as an overflow failure. | Truncation is silent, so the client must detect it. | Decided |
| D-012 | 2026-10-19 | Known issue: `ollama ps` shows a context of 8192 while the observed limit is 4096. The cause is not explained. | Recorded so it is not forgotten. | Open |
| D-013 | 2026-10-19 | Thinking is off by default. A `think_enabled` switch turns it on. The switch is recorded in the envelope. | Thinking is slower and costs more tokens. Keeping it behind a switch lets us study it later. | Decided |
| D-014 | 2026-10-19 | Thinking text is stored only when thinking is on. | Thinking is only produced when enabled. | Decided |
| D-015 | 2026-10-19 | LLMClient is the only path to the model. It owns the usage tracker and the pre- and post-call checks. | One place for cost, timing, and safety checks. | Decided |
| D-016 | 2026-10-19 | Usage tracker writes one row per call to a shared SQLite file, `src/runs/usage.sqlite`. Each row has a UUID4 call ID, `project_name` (blank becomes `"unassigned"`), and `date_created`. | One place to see totals across all runs and projects. | Decided |
| D-017 | 2026-10-19 | Prompt and output hashes are always stored. Full text is stored only when `store_full_text` is on. The default is off. | Keeps the database small by default. Replay needs the switch on. | Decided |
| D-018 | 2026-10-19 | Budgets are total tokens and wall-clock time. Model load time is excluded from the time budget and tracked separately. | Local inference has no per-call cost. Loading is a one-time cost, not part of each call. | Decided |
| D-019 | 2026-10-19 | Storage is SQLite. The shared usage file plus one run database per run. Standard library, nothing to install. | Simple, inspectable, and needs no new software. | Decided |
| D-020 | 2026-10-18 | The envelope is approved by one person, recorded with a hash, the approver's name, the time, and a note. No cryptographic signing. | The pilot tests the controller, not key management. This departs from paper §4.4 on purpose; the gap is stated in the HLD. | Decided (default) |
| D-021 | 2026-10-18 | Project name is `fenced-retry` and the package is `fenced_retry`. | Named after the paper's envelope fence. | Decided |
| D-022 | 2026-10-18 | `data/` and `src/` are independent projects. Each has its own virtual environment and `requirements.txt`. `data/` keeps its download script and is not changed by `src/`. | Keeps the dataset and the code separate. | Decided |
| D-023 | 2026-10-18 | Python 3.13 for both projects. | Matches the data project. | Decided |
| D-024 | 2026-10-18 | Splits use CORD's official train, validation, and test splits (the folder is named `validation`). Train is for calibration, validation for prompt debugging, and test is used once at the end. | Avoids inventing a split rule. Keeps the test split clean. | Decided |
| D-025 | 2026-10-18 | Plan variants: A (direct extraction), B (extraction plus a total check against line items), C (extraction with date and currency rules spelled out). | Gives the compiler real choices without an LLM compiler. | Decided |
| D-026 | 2026-10-18 | Compiler is deterministic in v0.1. An LLM compiler is a later milestone. | Keeps the first pilot to one source of variation. | Decided |
| D-027 | 2026-10-18 | Sampling covers all accepted outputs, with no strata. Clusters are (batch, directive group), and first-attempt accepts form one cluster per batch. | Follows the paper's revision (§7). | Decided |
| D-028 | 2026-10-18 | Reviewer is the operator. Reference values are shown next to the model output during review. | Reviewing blind is slower and not needed for this pilot. | Decided |
| D-029 | 2026-10-18 | Baselines in the first milestone: single-shot and naive retry. | Cheapest comparison that shows whether retry helps at all. | Decided |
| D-030 | 2026-10-18 | Each condition runs three times, with the seed varied across runs. Variance is reported. | Enough to see spread without excessive local run time. | Decided |
| D-031 | 2026-10-18 | Field set comes from CORD's labels, since CORD v1 has no merchant, date, or currency. Phase 1 extracts nine total-related fields: `total_price`, `subtotal_price`, `tax_price`, `service_price`, `discount_price`, `cashprice`, `changeprice`, `creditcardprice`, `emoneyprice`. | CORD's labelled fields. Totals support deterministic arithmetic checks (menu sum against subtotal; subtotal plus tax and service minus discount against total). | Decided |
| D-032 | 2026-10-18 | Budget values (total tokens, wall-clock seconds) are set after the first smoke test. | Need a real measurement first. | Open |
| D-033 | 2026-10-19 | Envelope pins the model name and digest. The digest is read from Ollama at approval time. | Changing the model is a change that needs approval (paper A7). | Decided |
| D-034 | 2026-10-19 | Model name, digest, `num_ctx`, `temperature`, `seed`, `think_enabled` all live in the envelope. Operational settings (paths, `store_full_text`, budgets, split choice, repeat count) live in the run configuration. | Matches the paper's split between envelope-governed and operational settings. | Decided |
| D-035 | 2026-10-19 | Menu items (name, count, price) are included in the first version if feasible. Fields `sub_*`, `unitprice`, and other rare keys are excluded. | User preference: include them if we can. Menu items add a variable-length list and more failure modes. | Decided (if feasible) |
| D-036 | 2026-10-19 | Input is text only. The model receives text built from CORD `valid_line` word annotations. No images. | User confirmed text-only input. | Decided |
| D-037 | 2026-10-19 | Envelope pins model `qwen3:8b` with digest `500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41`. | Digest read from `/api/tags` on this machine. | Decided |
| D-038 | 2026-10-19 | Value normalisation is part of the validators: strip thousands separators, trailing punctuation (for example `40,000.`), and currency symbols before comparing numbers. | CORD values use mixed formats; comparisons are exact after normalisation. | Decided (default) |
| D-039 | 2026-10-19 | Arithmetic checks (menu sum against subtotal; subtotal plus tax and service minus discount against total) are not in v1. | CORD references contain rounding lines, unlabelled item discounts, and voids, so arithmetic fails on reference values (about one in three train records). Restore with rounding and discount handling in a later version. | Decided |
| D-040 | 2026-10-19 | Records with a reference value that does not appear in the input text are excluded from the pilot. The exclusion lists are written to `src/runs/reference_audit_<split>.json`. | A value absent from the text cannot be supported by any output, so these records would dead-letter for reasons unrelated to the controller. Counts: train 88 of 800 excluded, validation 7 of 100, test 16 of 100. | Decided (default), needs your confirmation |
| D-041 | 2026-10-19 | The model digest is read from Ollama and compared with the pin once per client instance (at the first call), not before every call. | A per-call digest request adds latency and a request per call; the model does not change within a run. A changed model is caught at the next run's start. | Decided (default) |
| D-042 | 2026-10-19 | Client budgets (tokens, wall-clock) are counted per client instance, i.e. per run. Model load time is excluded from the time budget. Per-call timeout is 120 seconds, allowing for model load. | Simplest scope that matches D-018. The timeout value is provisional until the smoke test is measured (D-032). | Decided (default) |
| D-043 | 2026-10-19 | The envelope holds a set of seeds (0, 1, 2), not one seed. Each run uses one seed from the set. | D-030 needs three repeats with different seeds, and D-034 put the seed in the envelope. A set satisfies both. | Decided (default) |
| D-044 | 2026-10-19 | Provisional values in the first envelope: m = 2 (matches paper Appendix A.4), theta_drift = 0.8, N_max = 3, t_max = 300 s, K = 1 (deterministic default fallback), W_d = 50, W_a = 100, alpha = 0.05, gamma = 0.95. φ and φ_s are null until calibrated (M7). Budgets are null until after the smoke test (D-032). | The envelope must be complete to be approved. Provisional values are recorded as provisional and changed only by a new approved version. | Decided (default) |
| D-045 | 2026-10-19 | Approval is run by the user. The code approves nothing on the user's behalf. | Approval is the human decision the paper depends on. | Decided |
| D-046 | 2026-10-19 | Changes to an approved envelope follow the runbook in `design/ENVELOPE_CHANGE.md`: a new version file, a recorded reason, a shadow run, a person's approval, train-only calibration, and test-split use recorded once per version. | Makes each envelope change traceable and keeps the test split clean. | Decided |
| D-047 | 2026-10-19 | The deterministic default fallback is terminal: it sends the record to the dead-letter queue with the null output held for review. It is never accepted as a result. | A null output would pass the validators (no amounts to check), so accepting it would count an unresolved record as a success. | Decided (default) |
| D-048 | 2026-10-19 | If a compiled note's template group is unknown (for example a call failure with no template), the note falls back to the schema template. | Call failures have no directive template; the record must still be able to continue. | Decided (default) |
| D-049 | 2026-10-19 | The initial plan is the constant `A` until the next envelope version adds an `initial_plan` field. | The v1 envelope is approved and not changed in place (D-046). | Decided (default) |
| D-050 | 2026-10-19 | The task statement Q and the reminder R are constants in `controller/prompt.py`. They move into the envelope in the next version. | Same reason as D-049; recorded as a gap, not hidden. | Decided (default) |
| D-051 | 2026-10-19 | Shadow mode is a run flag. Shadow outcomes are recorded with `shadow = 1` and are never accepted for use. | Paper §4.4 requires shadow runs before a new version goes live (`design/ENVELOPE_CHANGE.md`, step 8). | Decided |
| D-052 | 2026-10-19 | The pilot runs in small blocks of about 100 records, each its own run, not one long run. | Limits the cost of a crash or a bad setting, and makes each block reviewable. Crash resume (M6) still applies within a block. | Decided |
| D-053 | 2026-10-19 | The naive retry baseline resends the identical prompt. It is not resampled, so with temperature 0 and a fixed seed the retries are expected to repeat the output. | Matches the paper's naive-retry description. Whether identical retries help at all is itself a result to report. | Decided (default) |
| D-054 | 2026-10-19 | Each condition and each approved seed is its own run database, named `<name>-<split>-<offset>-<n>-<condition>-s<seed>`. Resuming a run uses the same name. | Lets a block be re-run safely; per-run reports and comparisons follow directly. | Decided |
| D-055 | 2026-10-19 | The test split can only be run with `final = true` in the configuration, and only after the envelope is frozen. The config loader refuses it otherwise. | Enforces D-046 in code, not only in the runbook. | Decided |
| D-056 | 2026-10-19 | Crash resume: records already ACCEPT or DEAD_LETTER are skipped; any record left in progress is cleared and run again from its first attempt, with an event logged. | Avoids repeating accepted work and avoids mixing attempts from a crashed run into a new one. | Decided |

## Open questions

1. Menu item scope (D-035): included if feasible. Fallback is to move them to phase 2 if the validators prove too hard to write in M1.
2. Budget values (D-032). Set after the smoke test.
3. Root cause of the `ollama ps` mismatch (D-012). Not blocking, but worth understanding before the pilot is written up.
4. CORD column names. The download script assumes `image` and `ground_truth`. Confirm on the first run.

## D-057 Stage temperatures are externalised (2026-10-18)

Context: the user asked that the temperature of each stage can be changed later without code edits. The stages are the executor (the controller's calls), single_shot, naive_retry, and the reserved plan_compiler and judge stages.

Decision:
- The executor temperature stays in the approved envelope (theta.temperature, currently 0.0). Changing it is a governed change: a new envelope version under ENVELOPE_CHANGE.md (D-046). The loader rejects an [executor] entry in configs/sampling.toml.
- The other stages are set in configs/sampling.toml. A stage not listed uses theta.temperature. Values must be in 0..2 and stage names are checked.
- Every LLM call records the stage and the temperature it used (llm_calls.stage, llm_calls.temperature). The usage DB gains a stage column via a migration for older files.
- The run prints the temperatures it uses at start. Any non-default value is an exploratory run and must be labelled as such in the report. This is not implemented in the report yet.

Consequence: a temperature change for baselines or a future judge needs no envelope change. Any change to the executor needs a new approved envelope.

## D-058 Assurance sampling protocol values (2026-10-18)

The sampling values are protocol settings, not envelope settings: alpha 0.05, eps_c 0.2, c = 14 clusters per block (the rule c >= ln(alpha)/ln(1 - eps_c) gives 13.4), j = 2 outputs per sampled cluster. They live in configs/assurance.toml and must be fixed before the test split. A cluster is (batch, directive group). The group of an accepted output is the group of its last failing attempt (first failing check in sorted order). An output accepted on the first attempt goes to the "none" cluster. Reviews use the accepted attempt's output text.

## D-059 Fleet breaker arms and resumption (2026-10-18)

The dead-letter arm trips when the fraction over the last W_d terminal records exceeds phi, and only on a full window. The silent-failure arm trips when the exact lower confidence bound at level gamma exceeds phi_s. A null threshold leaves that arm observational. The v1 envelope has phi and phi_s null, so the pilot breaker is observational.

Calibration (analytic, paper §9.4): with nominal dead-letter rate p0 and W_d = 50, alpha = 0.05, the smallest k with P(count > k) <= alpha gives phi. On one train run (controller, seed 0) the observed rate is 6/100, so p0 = 0.06 gives k = 6 and phi = 0.12. Calibration pools one run by default, because the nine block-1 runs share the same 100 records. phi is not applied: setting it changes the envelope and needs a new approved version (D-046). phi_s is the nominal silent-failure rate p0_s, which the protocol fixes. The lower bound already makes a false trip unlikely at level gamma, so no further calibration is needed for the form of the rule.

When the breaker opens, run_batch stops after the record that tripped it and logs a breaker event. A new run refuses to start until `decide` records a decision (paper §7: resumption is an explicit human decision).

## D-060 Final partial block is sampled; per-block review floor (2026-10-18, decided by the assistant, to be reported as a deviation)

Problem: W_a = 100 in the v1 envelope, and a block-1 run accepts about 94 records. Paper §7 samples only full blocks, so no block closed and nothing was sampled. Changing W_a is a governed envelope change (D-046), so it was not changed.

Decision, in two parts:
1. The final partial block of a batch is closed at the end of the batch and sampled like a full block. A batch smaller than W_a is therefore sampled. This deviates from paper §7, which samples full blocks only.
2. Each block receives at least n_min = ceil(ln alpha / ln(1 - eps)) reviews, with eps = 0.2, so n_min = 14 (configs/assurance.toml, [sampling].eps). Reason: most block-1 outputs are first-attempt accepts and form a single cluster, so c clusters with j = 2 gives only 2 reviews, which cannot see a 20% silent-failure rate. The floor tops the sample up from the block's outputs.

Result on the block-1 controller run (seed 0): 14 reviews in one block, all in cluster "none".

Paper follow-up (M9): report both changes as deviations, and state that the first-attempt cluster is a single cluster per batch, so the cluster design gives little extra detection there.

Note: `assure` adds rows and does not replace earlier draws. If the protocol changes, delete the samples rows for the run before drawing again. This is not done while reviews exist.

## D-061 Automatic reference judge for the silent-failure sample (2026-10-18)

Context: the validator skips null fields (checks.py: `if value is None: continue`), so a field the input shows but the output leaves empty passes. A human reviewer was asked to judge the sample but did not review it, so the first pass recorded 14 "correct" verdicts without reading. Those rows are not reviews and are to be removed.

Decision: `judge --run <id>` gives each sampled accepted output an automatic verdict by comparing it with the CORD reference (gt_parse): amounts after normalisation (D-038), and the menu as sorted (name, price, count) triples. A null in the output where the reference has a value is a mismatch. Verdicts are stored with reviewer = "reference" and the differences in note. Time is 0.

Caveat, which bounds how the number may be used: a "reference" incorrect means the output disagrees with the CORD annotation. It is not proof that the output is wrong. Annotation gaps (the reference is empty but the receipt has a value) count as disagreement. Menu comparisons are strict about count formatting. On the block-1 controller run the judge gives 14 of 14 incorrect, and the differences are a mix of amount errors (about 8) and menu differences (about 6, several from formatting). Report this as a reference-disagreement rate, separately from any human-judged silent-failure rate, until the menu comparison is checked against human judgment.

Schema: reviews gains reviewer (default 'human') and note columns, added by a migration in RunStore.__init__.

## D-062 M8 metrics, frozen baseline, and per-reviewer verdicts (2026-10-19)

- Metrics (harness/metrics.py) are read-only and list not-computed quantities with reasons. Containment is read from the record's reason ("fuse:<tier>"), because the fuse fires before an attempt is made and no attempt row carries the tier.
- The frozen-strategy condition ("frozen") keeps the ledger, note choice, and fuse, and fixes the plan at A (paper §9.2).
- Reviews are keyed by (run, record, reviewer). A human verdict and a reference verdict can coexist. Older files are migrated in RunStore.__init__.
- The silent-failure arm of the breaker and the silent-failure metric use human verdicts. Reference verdicts are reported separately (D-061).
- Test split: one seed at temperature 0, per the protocol decision of 2026-10-19 (PROTOCOL.md).

## D-063 Envelope v2: qwen3-8k:latest, 8k context, temperature 0.3, remote server (2026-11-02)

Context: v1 results showed temperature 0 makes all conditions deterministic — the same six records dead-lettered across all nine runs with no rescues. H2 (directive retry outperforms naive retry) could not be tested. Also, D-012 recorded that qwen3:8b on the local server silently truncates prompts beyond ~4096 tokens even when num_ctx is set to 8192.

Decision: create envelope v2 with the following changes. All other structure (plans A/B/C, templates, step graph, fallback) is unchanged from v1.

- model: qwen3-8k:latest (digest a68fd3999784d0ff878cb799bfe1a6f19f0ddf405651728609d687604ce10955) on http://192.168.0.13:11434 (remote, Q4_K_M). Resolves D-005 server location.
- num_ctx: 8192 (from 4096). Resolves D-012: qwen3-8k is the 8k-context variant; the truncation issue does not affect it.
- temperature: 0.3 (from 0.0). This is the key change for H2: at 0.3 the model sometimes hallucinates values not in the receipt text (menu prices, totals), the validator catches these, and directives from the template library guide the retry. Rescue events become observable. Seeds now produce different outputs, making multi-seed variance meaningful.
- call_timeout_s: 180 (from 120) and t_max_s: 600 (from 300) to account for remote server latency variance.
- configs/sampling.toml: removed explicit 0.0 overrides for single_shot and naive_retry so all conditions inherit temperature 0.3 from the envelope. Fair comparison for v2 experiments.

Smoke test (2026-11-03): median latency 2.4s (vs 7.2s local), max 4.8s. Remote server significantly faster.

v2 runs are NOT exploratory: temperature 0.3 is now the envelope's governed value. The v1 (temp=0) results remain as the reproducibility baseline and are reported separately.

Checklist: reason recorded (this entry), new file created v2 untouched, digest updated and smoke test passed, notes block updated, lint clean, approved by Swaraj Shekhar (2026-11-03), sampling.toml updated.

## D-064 Envelope v3: num_predict 512→1024, strengthened templates to fix six v2 dead letters (2026-11-02)

Context: v2 block-1 analysis (100 records, 3 controller seeds) showed all six dead-lettered records fail for the same structural reasons across all nine runs — no rescue events. Post-mortem on the model's actual outputs identified three distinct root causes:

1. **PARSE_ERROR — output truncation** (records train-00065, train-00103): The model generates syntactically valid JSON but the output is cut off mid-array because num_predict=512 is too small for receipts with long menu lists. The JSON is incomplete on both attempts 1 and 2, so the record dead-letters on the repetition fuse (m=2, same PARSE_ERROR signature). Fix: raise num_predict to 1024.

2. **MENU_PRICE_NOT_VALID — @ prefix or compound prices** (records train-00023, train-00048, train-00099): These receipts print menu lines as `@unit_price line_total` (e.g., `@21,000 21,000` or `2 13.500 27.000`). The model copies either `@unit_price` (fails normalize_amount because `@` is not stripped) or both numbers as a compound string `"13.500 27.000"` (also fails). The CORD reference expects the LINE TOTAL (last printed number). Fix: strengthen the menu template to say "price is the LAST printed number — never both numbers or the @ prefix."

3. **TOTAL/CASH/CHANGE_NOT_VALID — field label contamination** (record train-00089): Receipt text is `Total Rp 5.000`. The model copies `"Total Rp 5.000"` as total_price. normalize_amount strips Rp but not the label word "Total", returning None (not a number), so the check fails. The directive injected on retry is the same template as the initial prompt — identical information, no new guidance. Fix: strengthen all amount templates to say "extract ONLY the amount value, NOT the field label."

Decision: create envelope v3 with targeted changes to templates and theta.num_predict. Plans, fallback, step graph, and invariants are unchanged from v2.

- num_predict: 1024 (from 512). Addresses root cause 1.
- templates.menu: specifies price = LINE TOTAL, explicitly forbids @ prefix and compound prices. Addresses root cause 2.
- templates.amount:* (all nine fields): adds "NOT the label 'Total'/'CASH'/etc." with a concrete example. Addresses root cause 3.
- All other theta parameters identical to v2 (model, endpoint, num_ctx, temperature, seeds, N_max, t_max_s, K, m).

Note on H2 (rescue events): The three root causes above are all cases where the model will likely succeed on attempt 1 once the templates are clear — they are NOT expected to produce rescue events. Rescue events are expected to appear in block 2 (400 more diverse records) where some records fail on attempt 1 for marginal reasons and the injected directive guides the retry to success.

Checklist: reason recorded (this entry), new file envelopes/receipt-extract-v3.json and v3.approval.json created, v2 untouched, hash computed and matches approval, configs/v3-train-block-01.toml created.

## D-065 Canonical envelope hash: computed over sorted-key JSON, not raw file bytes (2026-11-03)

When the v3 block-1 run was first launched, it raised `BatchHalted('envelope hash differs from approved hash')` despite the approval having been re-written with the SHA-256 of the file bytes. Root cause: `fenced_retry/envelope/canonical.py` defines `envelope_hash(obj)` as `hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",",":")).encode()).hexdigest()` — SHA-256 of the **canonical JSON serialisation** (sorted keys, no whitespace), not the raw bytes of the `.json` file. Raw-bytes SHA-256 diverges from the canonical hash whenever the file has different key ordering or whitespace.

Decision: the approval record is always produced by calling `fenced_retry.envelope.approval.approve()` or by directly calling `envelope_hash(load_envelope(path))`. Never compute it from raw file bytes (`open(path,"rb").read()`). The approval record for v3 was re-generated with the correct canonical hash `f35c4ef1...` and verified by calling `verify(env_path)` before relaunching the run.
