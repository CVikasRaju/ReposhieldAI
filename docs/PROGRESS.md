# Progress

Phase-by-phase build record. Each entry lists status, acceptance criteria, skips, deviations and how to run the result.

## Phase 0: Scaffold

**Status:** complete

**Acceptance criteria**

- Repository layout matches `docs/02-ARCHITECTURE.md` section 8, with stubs and docstrings. Met.
- `Makefile` provides every required target plus `clean-data`. Met.
- `backend/config.py` loads every `RS_` variable with the documented default. Met.
- `backend/db/schema.sql` matches `docs/05`, including `chain_head`. Met.
- Seed scripts produce the documented workspace (9 files, including the hidden injection) and a 50-row customers table with exactly three Luhn-valid card numbers in `notes`. Met, covered by tests.
- `.env.example`, `.gitignore`, `pyproject.toml`, `requirements.txt`, `docs/DECISIONS.md` exist. Met.
- `make setup`, `make seed`, `make test` succeed. Met.
- On this Windows host GNU make is available as `mingw32-make`; a local shim at `.tools/bin/make.exe` was used for verification. The Makefile is standard GNU make.

**Skipped:** none.

**Deviations:** see `docs/DECISIONS.md`.

**How to run:** `make setup && make seed && make test`.

## Phase 1: Policy engine

**Status:** complete

**Acceptance criteria**

- Canonical `.aishield` loads without error. Met.
- Pydantic validation rejects unknown keys, unknown profiles and weights that do not sum to 1.0 with clear messages. Met.
- Profile defaults implemented for developer, business, student and strict; explicit values override; `RS_PROFILE` override supported. Met.
- Matcher cases from the phase section pass. Met.
- Command patterns match on tokenised, single-space-joined commands. Met.
- Mode presets provide thresholds; explicit `scoring.thresholds` overrides. Met.
- Business profile applies HOLD/BLOCK to tables with sensitive column names; student profile holds deletes. Met.

**Skipped:** none.

**How to run:** `make test` (policy tests in `backend/tests/test_policy.py`).

## Phase 2: Detectors, maskers, restore-on-write

**Status:** complete

**Acceptance criteria**

- Every detector has positive and negative tests, including Luhn, Verhoeff, mod-97, placeholders and benign context. Met.
- Previews never contain a raw value. Met.
- Masking and restore satisfy the properties in `docs/06` section 7. Met; every seed workspace file round-trips through mask and restore.
- `changes` offsets reproduce `sanitized` from `raw` for text, code and table output. Met.
- Code masker handles decorators, async functions, nested functions, methods, docstrings, one-line functions, syntax errors and tabs. Met.
- Table masker produces consistent placeholders and masks free-text card numbers. Met.

**Skipped:** none.

**Deviations:** logic-block markers are `[MASKED_LOGIC_BLOCK_NN]` as specified; the block replacement line is stored in the delta `placeholder` field so applying changes reproduces the sanitized text exactly.

**How to run:** `make test` (detector and masking tests).

## Phase 3: Scoring and decisions

**Status:** complete

**Acceptance criteria**

- Four signals and combined score use the policy weights. Met.
- Worked examples E1 to E7 are parametrized tests within 0.01. Met.
- `decide()` implements strictest-wins including the masking floor. Met.
- Behaviour tracker implements the sliding window, injectable clock and `blocked_in_window`. Met.
- `make train-model` trained on 456 synthetic examples and printed held-out accuracy **0.9783** (recall 1.0000). Met.
- Missing model returns 0.0 and logs a warning. Met.
- Content-model inference measured **4.64 ms** per sample (budget 20 ms). Met.

**Skipped:** none.

**Deviations:** training data has 456 unique examples after de-duplication (minimum 400). No Faker dependency; deterministic generators are used.

**How to run:** `make train-model && make test`.
