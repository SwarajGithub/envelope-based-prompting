# HLD: fenced-retry (Envelope-Bounded Retry Pilot, Receipt Field Extraction)

Status: Draft v0.3 for review. Supersedes v0.1 (Bedrock, two-key signing) and v0.2 (context size 8192). Decisions are logged in `DECISIONS.md`; the work plan is in `TODO.md`.
Owner: Swaraj Shekhar (personal prototype).
Paper: `docs/draft6.html`. Terms follow the paper's Appendix B.

## 1. Purpose

The project studies runtime prompt change under guardrails: a system may revise its own prompt while it runs, but only inside an envelope that a human approved in advance. The paper's controller is built here and run on receipt field extraction (CORD v1) with a local model. The pilot produces real values for the measurements the paper lists as pending in §9.5 (repetition count m, within-cluster correlation ρ, review effort) and a first comparison against simple baselines.

This is a prototype. It is not a production system. It makes no claim about output quality beyond what the pilot measures.

## 2. Scope

In scope:
- Extract nine total-related fields that CORD labels (total, subtotal, tax, service, discount, cash, change, card, e-money), and menu items (name, count, price) if feasible (D-031, D-035).
- Input is text built from CORD annotations. No images, no OCR output.
- One local model, `qwen3:8b`, served by Ollama on the same machine (`http://localhost:11434`).
- One envelope for this task family, approved by one person with a recorded approval.
- Storage in SQLite files (Python standard library, nothing to install).
- Command-line runs, Markdown and CSV reports.
- Baselines: single-shot and naive retry in the first milestone.

Out of scope for v0.1:
- Vision input, other task families (text-to-SQL, contracts).
- An LLM plan compiler (deterministic compiler only).
- Cryptographic signing (see §8 for the approval record used instead).
- Multi-user approval, web UI, deployment, AWS.

## 3. Repository layout

Two independent projects, each with its own virtual environment.

```
envelope-based-prompting/
  data/                        CORD download (own .venv, requirements.txt), unchanged
  design/
    HLD.md                     this document
    TODO.md                    work plan and milestones
    DECISIONS.md               decision log (IDs D-001 onward)
  src/                         independent project: own .venv, own requirements.txt
    requirements.txt           httpx, pytest (stdlib sqlite3 needs no install)
    fenced_retry/
      llm/                     LLMClient (Ollama), usage tracker, call checks
      envelope/                load, canonicalize, hash, approval record, approval lint
      controller/              per-record state machine, fuse tiers, fallback, DLQ
      validator/               deterministic checks, normalizers, check-to-group map
      compiler/                deterministic plan compiler (v0.1)
      assurance/               sampler, fleet breaker, calibration helpers
      adapters/                CORD reader and reference-field mapping
      store/                   SQLite schema and access (run databases)
      harness/                 baselines, metrics, reports
      cli.py
    envelopes/                 approved envelope files (JSON) and approval records
    tests/
    runs/                      run databases and reports (gitignored)
```

The package name `fenced_retry` follows the paper's title and the envelope fence. It can change before the first commit.

## 4. Components

| Component | Responsibility | Paper reference |
|---|---|---|
| LLMClient | Single entry point to the model. Sends chats to Ollama, enforces per-call checks, records every call through the usage tracker | §4.2, A7, A13 |
| Usage tracker | Writes one row per LLM call to the shared usage database: tokens, text, timings, project, settings | §9.3 (cost), §4.4 (audit) |
| Envelope | Load and validate; canonical JSON; SHA-256 hash; approval lint (hard constraints, J_struct); approval record | §4.4, §5.3, A.1 |
| Controller | Per-record loop: assemble prompt, call LLMClient, validate, update ledger, compile next plan, evaluate fuse, fallback, dead letter | §4, §5.4, §5.5 |
| Validator | Deterministic checks for the fields; check IDs mapped to directive groups (A2) | §4.1, §5.2, §6.2 |
| Plan compiler | Map failing groups to the next approved plan; build the note from a template | §4.1, A.2 |
| Assurance | Sample accepted outputs; fleet breaker over dead-letter and sampled silent-failure windows | §5.5, §7 |
| Store | Run databases: runs, attempts, failures, ledger, dead letters, reviews, envelope approvals | §4.4, Appendix C |
| Harness | Run baselines, compute the metrics in §9.3, write reports | §9 |
| Adapter | Read CORD ground truth; build input text and reference fields | §9.1 |

## 5. Data flow

1. The adapter reads `data/cord/<split>/gt/*.json` and builds one record per receipt: input text, reference fields, record ID. Records are assigned to splits using CORD's official splits.
2. The CLI loads the envelope and checks its hash and approval record. A mismatch halts the run.
3. For each record, the controller assembles the prompt from the invariant block I, data payload D, task Q, ledger C, plan S, note x, and reminder R. The length check runs before the call.
4. The controller calls LLMClient. LLMClient sends the request to Ollama, records the call through the usage tracker, and returns the result.
5. The validator checks the output and returns pass, or the set of failing check IDs F.
6. On pass, the record is accepted and the output is queued for sampling (written once, per A6).
7. On fail, the failure is stored, the ledger is updated, the compiler picks the next plan, and the fuse is evaluated before the next attempt.
8. When the fuse opens, the fallback menu runs (at most K calls). A record that is still unresolved goes to the dead-letter queue.
9. After each record, the fleet breaker is evaluated. If it opens, the batch pauses until a recorded human decision.
10. Reports are generated from the run database.

## 6. LLMClient

### 6.1 Interface to the model

- Transport: Ollama's native chat endpoint at `http://localhost:11434/api/chat`, called with `httpx`. The native endpoint is used because it accepts the context size, seed, temperature, and thinking setting directly. The OpenAI-compatible endpoint is not used.
- Model: `qwen3:8b`. The model digest is read from Ollama at approval time and pinned in the envelope (A7).
- Options set on every call: `num_ctx` (4096 for the pilot, see §6.5), `temperature = 0` (the model's default is 0.6), and a fixed `seed` taken from the run configuration.
- Thinking: verified on Ollama 0.40.6. With `think: false` the response has no thinking output. With `think: true` the reasoning comes back in a separate `thinking` field, so no tag stripping is needed. The pilot default is thinking off. A run-level switch, `think_enabled` (default false), turns it on. When it is on, the thinking text is stored in the usage tracker and the `thinking_tokens` column is filled. The switch is recorded in the envelope, because it changes the model's behaviour and cost.
- Keep-alive: set so the model stays loaded for the length of a run.

### 6.2 Result

Each call returns a result object: call ID, output text, thinking text (if any), token counts, timings, and any error. The controller uses the output text only. The validator never sees the thinking text.

### 6.3 Usage tracker

Each call writes one row to `llm_calls` in a shared SQLite file, `src/runs/usage.sqlite`. The file persists across runs and projects.

Columns:
- `call_id`: UUID4, generated per call.
- `project_name`: from the caller. A blank or missing value becomes `"unassigned"`.
- `date_created`: UTC timestamp when the call was made.
- `model`, `model_digest`, `endpoint`.
- `num_ctx`, `temperature`, `seed`, `think`.
- `input_tokens` (prompt tokens, from Ollama's reported count), `output_tokens` (generated tokens), `thinking_tokens` if Ollama reports them separately, else NULL.
- `load_ms` (model load time, from Ollama), `latency_ms` (total wall-clock time of the call).
- `status` (`ok`, `timeout`, `error`) and `error_text`.
- `prompt_hash`, `output_hash`, `thinking_hash`: SHA-256 of the stored text, always stored.
- `store_full_text` (0 or 1), and `input_text`, `output_text`, `thinking_text`: populated only when `store_full_text` is on. Otherwise NULL.

The switch `store_full_text` is a setting in the run configuration. It defaults to off. When it is off, the database keeps hashes and metadata only.

### 6.4 Checks

Before each call:
- Context check: the estimated prompt size must fit the pilot limit of 4096 tokens, less the output allowance (`num_predict`) set in the envelope. The check uses the bound from the envelope (§5.1 of the paper). A prompt that does not fit is not sent. The envelope-tier fuse opens instead.
- Model check: the digest reported by Ollama must match the digest pinned in the envelope. A mismatch halts the batch.
- Budget check: cumulative input plus output tokens and cumulative wall-clock time, from the run configuration, must be below their limits. Model load time is excluded from the time budget and is tracked separately.

After each call:
- Timeout: a call over the per-call timeout from the envelope is recorded as `timeout` and counted as a failed attempt (A13).
- Result check: an empty or malformed response is recorded as `error` and counted as a failed attempt.

### 6.5 Context size

Ollama reports the model's context length as 40960 and runs it at 4096 by default. Measured on this machine (Ollama 0.40.6, 16GB RAM, GPU): with `num_ctx: 8192` set on each call, `ollama ps` reports a context of 8192, but a prompt of about 13,000 tokens reaches the model with its start removed, and `prompt_eval_count` stops at 4098. The usable input limit is therefore about 4096 tokens. The pilot sets `num_ctx` to 4096, and the envelope records 4096. The client treats any response with `prompt_eval_count` at or above 4096 as a truncated prompt, recorded as an overflow failure. The mismatch between `ollama ps` and the observed behaviour is not explained and is recorded as a known issue.

## 7. Data adapter (CORD)

- Input text: built from CORD's word-level annotations, joined in reading order by line. OCR is not used. The construction is checked on the downloaded data.
- Reference fields: taken from CORD's `gt_parse` (`total.total_price`, `sub_total.*`, `total.*` payment fields, and `menu` entries). CORD v1 has no merchant, date, or currency labels, so those are not extracted. The mapping is written once and tested so that the validators pass on the reference values.
- Splits: CORD's official splits. Train is used for calibration, validation for debugging prompts, and test is used once, at the end (§9.4 of the paper).

## 8. Envelope and approval

Contents follow Appendix A.1 of the paper:
- `I`: invariants (JSON output shape, ISO date, total format, verbatim rules, data-tag rule).
- `G0`: declared step IDs, precedence, and mandatory set for the extraction task.
- `P_approved`: approved plan variants. Plan variants are prompt structures, since each attempt is one model call. Proposed: A (direct extraction), B (extraction plus a check that the total matches the line items), C (extraction with the date and currency rules spelled out).
- `T`: directive templates, one group per check group.
- `Θ`: thresholds and limits: model name and digest, `num_ctx`, `temperature`, the seed, `N_max`, `t_max`, `K`, the per-call timeout, `m`, `θ_drift`, `α`, `γ`, `φ`, `φ_s`, `W_d`, `W_a`, and the budget limits.

Format and approval:
- Canonical JSON (sorted keys, UTF-8, no insignificant whitespace), hashed with SHA-256.
- One approver. The approval is recorded in the run database and in `envelopes/<version>.approval.json`. The record holds the approver name, the envelope hash, the UTC time, and the approver's note.
- Approval runs the approval lint first: hard constraints (mandatory steps, precedence) and the drift check.
- The hash is checked at batch start and per record. A mismatch halts the run.

Deviation from the paper, stated on purpose: the paper calls for signed envelopes and two-person approval (§4.4). The pilot uses a hash and a single recorded approval, because the goal is to exercise the controller, not to test key management. The hash makes changes detectable. It does not make them unforgeable. This is a stated gap for the pilot.

## 9. Controller

Record states: `NEW → ASSEMBLE → CALL → VALIDATE → ACCEPT | FAIL`. On FAIL: `RECORD_FAILURE → FUSE_CHECK → (COMPILE → ASSEMBLE) | FALLBACK → DEAD_LETTER`.

Fuse tiers (§5.4 of the paper):
- Limit tier: attempt index above `N_max`, or elapsed time above `t_max`.
- Repetition tier: failure count for the current signature at least `m`, or plan repeat count at least `m`.
- Envelope tier: envelope check fails for the compiled plan, or the assembled prompt exceeds its caps.

The fuse is tested before each attempt, on the plan compiled for that attempt. Fallback calls fall under the elapsed-time tier and the length check only (§5.4).

Terminal states: ACCEPT or DEAD_LETTER. The record's state is stored after every transition, so a crash resumes without repeating accepted work.

## 10. Validator

Checks, grouped by field group (A2: each check belongs to exactly one group):
- `TOTAL_NOT_VALID` (group total): the reported total, after normalisation, is numeric and appears in the input text.
- `SUBTOTAL_NOT_VALID` (group subtotal): the subtotal is numeric and appears in the input text.
- `TAX_NOT_VALID`, `SERVICE_NOT_VALID`, `DISCOUNT_NOT_VALID`, `CASH_NOT_VALID`, `CHANGE_NOT_VALID`, `CARD_NOT_VALID`, `EMONEY_NOT_VALID` (one group per field): each numeric value, when present, appears in the input text.
- `ARITHMETIC_SUBTOTAL` (group arithmetic): sum of menu item prices equals the subtotal (when both present).
- `ARITHMETIC_TOTAL` (group arithmetic): subtotal plus tax plus service minus discount equals the total (when all present).
- `MENU_ITEM_NOT_VERBATIM` (group menu): each menu item name appears verbatim in the input text.
- `MENU_PRICE_NOT_VALID` (group menu): each menu item price is numeric and appears in the input text.
- `SCHEMA_MISSING_FIELD` (group schema): all keys present (null allowed).
- `PARSE_ERROR` (group schema): output is valid JSON with the expected shape.

Normalisation (D-038): thousands separators are removed, trailing punctuation is stripped, and currency symbols are dropped before numbers are compared. The validator runs every check, so the failing set is complete. It makes no model calls. Subjective correctness is covered only by sampling (A8).

The envelope's template library T has one directive group per row above, with the arithmetic and menu groups added as the first set of directives.

## 11. Plan compiler (v0.1, deterministic)

A fixed table maps failing groups to the next plan in `P_approved`. If no entry applies, the compiler keeps the current plan and sets a note from the template library. The compiler never reads the input text or the raw output, and it cannot change the envelope. An LLM compiler is a later milestone, behind the same interface.

## 12. Assurance and breaker

- Sampling covers all accepted outputs, with no strata (paper §7 as revised). Clusters are (batch, directive group). Outputs with no directive form one cluster per batch.
- The reviewer is the operator. The CLI shows each sampled output beside its reference fields, and records the verdict and the time taken (feeds `Rev_E` and `h`).
- Breaker: a dead-letter window `W_d`, and a lower confidence bound at level `γ` on the sampled silent-failure rate over window `W_a`. Thresholds come from calibration on the train split (§9.4 of the paper).

With CORD's split sizes, the windows are small. The breaker in the pilot is mainly observational. It is reported, and it pauses a batch only when a threshold is crossed.

## 13. Harness and baselines

- Milestone 1: single-shot, and naive retry (same prompt).
- Later: retry with failing check IDs only (H2a), retry with full failing output (H2b), conversational retry with transcript, frozen-strategy envelope (plan fixed to S_1), and the review-everything oversight baseline.
- Repetition: each condition runs three times, with the seed varied across runs. The variance across the three runs is reported.
- Metrics (§9.3): success within limits, invariant adherence by position, prompt length versus attempt, containment count (target zero), oversight load `h`, cost per record by call type (tokens and time, since the model is local), fuse precision and recall (offline), drift–failure correlation, dead-letter delay and pause time.

Outputs: one run database per run under `src/runs/`, with a CSV of records and attempts and a Markdown summary. Each run records the envelope hash, model digest, `num_ctx`, seed, and per-call token counts.

## 14. Storage

- Run databases (one per run): `runs`, `envelope_approvals`, `records`, `attempts`, `failures` (F, signature, groups, pointer to the attempt), `ledger_entries`, `dead_letters`, `reviews` (sampled record, verdict, minutes), `breaker_events`. Each attempt references its `llm_calls` row by `call_id`.
- Shared usage database: `llm_calls` (§6.3).
- Both are SQLite files. Each table with a time has `date_created`.

Prompts are stored in full only when `store_full_text` is on. With it off, the hash, the metadata, and the envelope version are kept. A run without stored text can be rebuilt from the data files and the envelope, not from the database alone. For pilot runs, the switch is on, because replay is required by §4.4 of the paper.

## 15. Configuration

Two files, kept separate:
- Envelope file (JSON, hashed and approved): everything in `Θ`, including the model name, digest, `num_ctx`, temperature, and the seed set.
- Run configuration (TOML, not governed): paths, `store_full_text` (default false), budget limits (total tokens, wall-clock seconds), split choice, and the number of repetitions.

## 16. Error handling

| Condition | Handling |
|---|---|
| Ollama not reachable (server not running) | Halt the batch. Configuration error, not a record failure. |
| Timeout | Failed attempt (A13). Recorded in `llm_calls` with status `timeout`. |
| Empty or malformed model output | Failed attempt with `PARSE_ERROR`. |
| Digest mismatch | Halt the batch. |
| Prompt exceeds `num_ctx` | Envelope-tier fuse opens. No call is sent. |
| Token or time budget reached | Pause the batch. Resume only after a recorded decision. |
| Envelope hash mismatch | Halt the batch. |
| Database write failure | Halt the batch. Do not continue with unrecorded attempts. |

## 17. Security and privacy

- No external services. Prompts and outputs stay on this machine.
- The repository holds no credentials.
- CORD is public data. Run databases and the usage database sit under `src/runs/`, which is gitignored.
- `data/`, `.venv/`, and `runs/` are gitignored in both projects.

## 18. Testing

- Validators pass on the reference values for every CORD record in the split being tested. Failures are a bug in the adapter or the validator.
- Envelope: canonicalization is stable; the hash verifies; a mutated envelope is rejected.
- LLMClient: `httpx` mock transport for request shape, timeouts, and malformed responses. The usage tracker is tested on a temporary SQLite file, including the `"unassigned"` default, the UUID format, and the switch.
- Controller: a fake LLM client scripts failures. The traces in Appendix A (A.2, A.4) are reproduced. Attempt counts stay within 2N_max + K.
- Breaker and sampler: unit tests on simulated batches with known rates.
- Live smoke test: three records through the real Ollama server, then a check of `ollama ps` for `num_ctx`.

## 19. Milestones

Detailed in `design/TODO.md`:
M0 scaffolding and environment. M1 CORD adapter and validators on reference values. M2 LLMClient and usage tracker. M3 envelope tooling and approval record. M4 controller with a fake LLM client. M5 live smoke test against Ollama. M6 first pilot run with baselines. M7 assurance and breaker. M8 harness and reports.

## 20. Assumptions

- The Ollama server runs on this machine for the whole pilot. It must be running during any run.
- `qwen3:8b` is pulled and works with the Ollama version installed (0.40.6, confirmed).
- The model's thinking setting can be controlled, or its reasoning block can be stripped reliably.
- The CORD annotations can be turned into input text that the model reads without the image.
- The machine has enough memory for the model at the chosen context size (16GB, confirmed).
- Personal prototype: no company data or infrastructure is used.

## 21. Open questions

1. **Thinking:** Resolved. Off by default, behind the `think_enabled` switch. Turning it on for a run is a recorded setting, since it changes cost and output.
2. **Temperature:** The model's default is 0.6. The pilot uses 0 for repeatability. Is that acceptable?
3. **Field list:** Resolved (D-031). Nine total-related fields from CORD, plus menu items if feasible (D-035).
4. **Plan variants:** Confirm plans A, B, and C, or propose others.
5. **Package name:** `fenced_retry` is the default, from the paper's title. Confirm or replace.
6. **Budget values:** The token limit and wall-clock limit for the whole pilot. These go in the run configuration and can be set after the first smoke test.
