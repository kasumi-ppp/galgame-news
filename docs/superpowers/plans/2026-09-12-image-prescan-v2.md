# Galgame Weekly Image Prescan v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use test-driven-development for every production change. Tasks are executed in the order listed in `agents/README.md`.

**Goal:** Convert the single-file prescan into a modular Python CLI that searches every news item and selects up to 20 trustworthy candidates with cross-issue history and human review.

**Architecture:** The frozen contract is `docs/architecture/image-prescan-v2.md`. Five deep modules communicate only through Pydantic domain models; SQLite provides history and adapters isolate external sources.

**Tech Stack:** Python 3.11+, Pydantic v2, SQLite, httpx, Beautiful Soup, Pillow, DDGS, pytest.

**Spec:** `docs/architecture/image-prescan-v2.md`

## Global Constraints

- Every news item is processed; low-quality images are never used to pad the issue total.
- LLM and paid APIs are optional and disabled without explicit configuration.
- Ordinary tests are offline and deterministic.
- Secrets are read only from environment variables.
- Luna tasks may not change frozen public models or interfaces.

---

### Task 1: Domain models, configuration, and history

**Brief:** `agents/luna/01-domain-storage.md`

- [ ] Add failing contract and storage tests.
- [ ] Implement the minimal domain/config/history surface.
- [ ] Run the task verification and commit.

### Task 2: DOCX ingestion and news analysis

**Brief:** `agents/luna/02-ingestion.md`

- [ ] Add failing parser and analyzer tests.
- [ ] Implement OOXML parsing, rules, and optional LLM fallback.
- [ ] Run the task verification and commit.

### Task 3: Source discovery and adapters

**Brief:** `agents/luna/03-discovery.md`

- [ ] Add failing resolver, adapter, and network-safety tests.
- [ ] Implement discovery providers and adapters.
- [ ] Run the task verification and commit.

### Task 4: Image curation

**Brief:** `agents/luna/04-curation.md`

- [ ] Add failing filter, dedupe, scoring, and allocation tests.
- [ ] Implement curation using TOML policy.
- [ ] Run the task verification and commit.

### Task 5: Delivery and CLI

**Brief:** `agents/luna/05-delivery-cli.md`

- [ ] Add failing output, application, CLI, and compatibility tests.
- [ ] Implement orchestration and atomic output.
- [ ] Run the task verification and commit.

### Task 6: Corpus regression and hardening

**Brief:** `agents/luna/06-integration-hardening.md`

- [ ] Add corpus and labeled evaluation fixtures.
- [ ] Implement evaluation and opt-in live smoke scripts.
- [ ] Run the full suite and evaluation, then commit.

### Final Sol Max review

- [ ] Review the complete branch against the frozen contract.
- [ ] Require fixes for interface drift, duplicated rules, unsafe networking, nondeterministic ranking, or missing failure isolation.
- [ ] Re-run the full offline suite and two-issue evaluation before integration.
