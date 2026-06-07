# AGENTS.md

## Purpose

This file is the implementation-agent bootloader for Swallow. Swallow is an ingest-only Python
toolkit that converts raw inputs into traceable `document.md` artifacts. Keep agent context small:
start here, then follow the linked docs for details.

## Start Here

- Current work: `docs/active/current.md`
- Machine-readable current phase: `docs/phase-manifest.yaml`
- Compressed status: `docs/project-status.md`
- Architecture: `docs/architecture.md`
- Development setup: `docs/development.md`
- Testing and gates: `docs/testing.md`
- API and SDK surfaces: `docs/api.md`
- Durable contract: `docs/contracts/ingest-contract.md`
- Capability Provider P1: `docs/capability-provider-p1.md`
- Provider boundary ADR: `docs/adr/0001-swallow-capability-provider-boundary.md`

## Current Naming

- Repository: `swallow`
- Python distribution: `swallow`
- Python import namespace: `swallow`
- CLI command: `swallow`

## Working Rules

- State non-obvious assumptions before coding.
- Ask before high-risk guesses involving API behavior, auth, data models, migrations, payments, or
  user-visible behavior.
- Use the smallest change that solves the task within the existing architecture.
- Do not add speculative features, frameworks, abstractions, or dependencies unless explicitly
  approved.
- Touch only files required for the task. Do not reformat, rename, reorganize, or refactor unrelated
  code.
- For non-trivial tasks, define success criteria before implementation and verify with executable
  checks where practical.
- Report changed files, checks run, checks skipped, and remaining risks.

## Repo Map

- `src/swallow/core/`: RawStore, jobs, queue, trace, routing, quality, runner, config, doctor.
- `src/swallow/workers/`: built-in ingest workers for text, MarkItDown, OCR, ASR, web, browser capture, and archives.
- `src/swallow/detectors/`: file, PDF, and URL classification.
- `src/swallow/normalizers/`: Markdown and conversation normalization.
- `src/swallow/cli/main.py`: Typer CLI, queue commands, and MCP command entry.
- `src/swallow/service/api.py`: FastAPI service and `/v1` HTTP API.
- `src/swallow/sdk/`: Local, CLI, HTTP, queue clients, facade, SDK models, and sandboxing.
- `src/swallow/capability/`: Capability Provider P1 adapter package.
- `src/swallow/mcp/server.py`: stdio MCP tool adapter backed by the Capability Provider.
- `tests/`: unit, contract, transport, SDK, service, security, batch, concurrency, and runtime smoke tests.
- `scripts/`: fixture generation, runtime smoke scripts, and SDK release gate.
- `docs/`: current state, contracts, architecture, API, testing, development, SDK plans, and QA evidence.

## Common Commands

Install development dependencies:

```bash
uv sync --dev
```

Run default local gate:

```bash
uv run --no-sync pytest -q -m "not network and not performance and not heavy"
uv run --no-sync python -m compileall -q src tests scripts
```

Run SDK release gate:

```bash
uv run python scripts/sdk_release_gate.py
```

Run focused gates:

```bash
uv run pytest -m contract -q
uv run pytest -m transport -q
uv run pytest -m security -q
uv run pytest -m adapter -q
uv run pytest -m concurrency -q
```

Run the CLI:

```bash
uv run swallow file ./sample.txt
uv run swallow jobs
uv run swallow inspect <job_id> --raw --artifacts
```

## Task Routing

- Core ingest behavior: read `docs/contracts/ingest-contract.md`, then work in `src/swallow/core/`.
- Worker support: read `docs/architecture.md` and `docs/development.md`, then work in `src/swallow/workers/`.
- CLI changes: read `docs/api.md`, then work in `src/swallow/cli/main.py` and CLI tests.
- Service changes: read `docs/api.md`, then work in `src/swallow/service/api.py` and service tests.
- SDK changes: read `docs/sdk-final-acceptance.md`, `docs/sdk-agent-project-plan.md`, and `docs/api.md`, then work in `src/swallow/sdk/`.
- Capability Provider P1: read `docs/capability-provider-p1.md`, `docs/contracts/ingest-contract.md`,
  and `docs/adr/0001-swallow-capability-provider-boundary.md`, then work in `src/swallow/capability/`
  and provider conformance tests.
- MCP changes: read `docs/api.md`, `docs/capability-provider-p1.md`, and `docs/sdk-final-acceptance.md`, then work in
  `src/swallow/mcp/server.py`.
- Queue changes: read `docs/api.md` and `docs/architecture.md`, then work in `src/swallow/core/queue_*` and `src/swallow/sdk/queue.py`.
- Documentation changes: preserve the roles described in `docs/active/current.md` and `docs/phase-manifest.yaml`.

## Non-Negotiable Rules

- Keep Swallow ingest-only. Do not add agent orchestration, RAG, vector stores, memory, chat UI,
  summarization, translation, embeddings, or LLM cleanup to core Swallow.
- Every successful ingest must preserve `document.md`, `ingest_document.json`, and `trace.jsonl`.
- Raw inputs are immutable and content-addressed under `raw_store/`.
- Worker execution must preserve traceability and reproducibility.
- SDK results are path-first. Full large Markdown content must not be returned by default.
- Provider outputs are not host truth. `document.md` is a conversion candidate, not a trusted
  external revision.
- Agents must not choose Swallow SDK backends or internal workers directly. Provider profiles choose
  local, CLI, HTTP, or queue execution.
- SDK and adapter failures must keep the hybrid error model: job-level ingest failures return failed
  or partial results; configuration, transport, sandbox, protocol, and invocation failures raise SDK
  exceptions.
- MCP remains an adapter, not a facade backend.
- MCP is not a Capability Provider backend in P1.
- MCP may wrap `SwallowCapabilityProvider`; it must not define a separate capability model.
- HTTP is not the local-first default provider path.
- URL ingest in MCP stays disabled unless explicitly enabled.
- Heavy integrations remain optional extras.

## Do Not Edit Unless Asked

- `.qa-final/`, `.pytest_cache/`, `.uv-cache/`, `.venv/`, `raw_store/`, `jobs/`, `queue/`,
  `batch_runs/`, and `sdk/facade_registry.jsonl` runtime output.
- `uv.lock` unless dependency changes are explicitly part of the task.
- Historical QA evidence such as `docs/final-qa-2026-05-17.md` except to correct links or metadata.

## Deep Context Index

- `docs/contracts/ingest-contract.md`: stable artifact, boundary, SDK, trace, and error rules.
- `docs/capability-provider-p1.md`: completed plan for the Swallow Capability Provider P1 adapter.
- `docs/closeout/capability-provider-p1-closeout.md`: closeout evidence and accepted local gates for
  Capability Provider P1.
- `docs/glossary/capability-provider.md`: terms for provider profiles, policies, artifacts, and
  host-truth boundaries.
- `docs/adr/0001-swallow-capability-provider-boundary.md`: why provider stays a thin adapter, not an
  agent runtime or backend model.
- `docs/architecture.md`: pipeline shape, worker routing, storage layout, and boundaries.
- `docs/api.md`: CLI, service, SDK, queue, and MCP command/API surface.
- `docs/testing.md`: local gates, release gate, optional heavy/network gates, and CI behavior.
- `docs/development.md`: optional worker dependencies, config, fixtures, and runtime smoke scripts.
- `docs/sdk-agent-project-plan.md`: detailed SDK roadmap and implementation history.
- `docs/sdk-final-acceptance.md`: final SDK acceptance checklist and release commands.
- `docs/project-status.md`: shipped, frozen, active, next, and remaining release items.

## Done Means

For non-trivial changes, report changed files, commands run, commands not run, and remaining risks.
Do not claim a check passed unless it was run in the current session.
