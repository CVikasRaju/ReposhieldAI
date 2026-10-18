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
- `make setup`, `make seed`, `make test` succeed. Met (`33 passed` after Phase 1; 3 tests at the end of Phase 0).

**Skipped:** none.

**Deviations:** see `docs/DECISIONS.md` (MCP pinned below 2.0, no Faker dependency, documented fake secrets).

**How to run:** `make setup && make seed && make test`.

On this Windows host GNU make is available as `mingw32-make`; a local shim at `.tools/bin/make.exe` was used for verification. The Makefile itself is standard GNU make.

## Phase 1: Policy engine

**Status:** complete

**Acceptance criteria**

- Canonical `.aishield` loads without error. Met.
- Pydantic validation rejects unknown keys, unknown profiles and weights that do not sum to 1.0 with clear messages. Met (tests).
- Profile defaults implemented for developer, business, student and strict; explicit values override; `RS_PROFILE` override supported by `load_policy(profile_override=...)`. Met.
- Matcher cases from the phase section pass. Met.
- Command patterns match on tokenised, single-space-joined commands (rule lists use `fnmatch`). Met.
- Mode presets provide thresholds; explicit `scoring.thresholds` overrides them. Met.
- Business profile applies HOLD/BLOCK to tables with sensitive column names. Implemented in `Policy.protect_rule_for_table`.
- Student profile applies HOLD to file deletes by default. Implemented in `Policy.default_path_decision`.

**Skipped:** none.

**Deviations:** none beyond `docs/DECISIONS.md`.

**How to run:** `make test` (policy tests in `backend/tests/test_policy.py`).
