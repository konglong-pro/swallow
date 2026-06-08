# Ingest Contract

## Purpose

This contract defines the durable boundaries that Swallow code, SDK clients, service routes, queue
workers, and adapters must preserve.

Related decision: `docs/adr/0001-swallow-capability-provider-boundary.md`.
Current provider plan: `docs/capability-provider-p1.md`.

## Applies To

- `src/swallow/core/`
- `src/swallow/workers/`
- `src/swallow/cli/main.py`
- `src/swallow/service/api.py`
- `src/swallow/sdk/`
- `src/swallow/mcp/server.py`
- Tests and documentation that describe ingest behavior.

## Terms

- Raw input: immutable source material stored under `raw_store/<sha256>/`.
- Ingest job: one pipeline execution stored under `jobs/<job_id>/`.
- Canonical Markdown: `jobs/<job_id>/document.md`.
- Structured document: `jobs/<job_id>/ingest_document.json`.
- Execution trace: `jobs/<job_id>/trace.jsonl`.
- Job manifest: `jobs/<job_id>/manifest.json`, supplemental to the structured document.
- Preview: small capped text returned by SDK/API/adapters for convenience.

## Rules

### Scope

- Swallow owns ingest only.
- Core Swallow must not implement agent orchestration, prompting, summarization, translation, LLM
  cleanup, embeddings, vector databases, RAG, memory, chat UI, or knowledge-base management.
- Agent integrations must be thin adapters around Swallow results.
- Provider outputs are not host truth. `document.md` is a conversion candidate, not a trusted revision
  owned by a host system.
- Agents request ingest capabilities; the provider runtime chooses local, CLI, HTTP, or queue backend
  execution according to profile and policy.
- MCP is an adapter. It may wrap `SwallowCapabilityProvider`, but it must not define Swallow's core
  capability model and must not be a provider backend in P1.
- HTTP is not the local-first default path. Local trusted use should avoid adding a service failure
  domain unless the service profile is explicitly selected.

### Raw Store

- Raw inputs are immutable and content-addressed.
- `raw_id` is derived from SHA-256 as `raw_<first_12_sha_chars>`.
- The raw store preserves original bytes and metadata needed for reproducibility.

### Successful Job Artifacts

Every successful ingest must be able to locate:

```text
jobs/<job_id>/document.md
jobs/<job_id>/ingest_document.json
jobs/<job_id>/trace.jsonl
```

Current successful jobs also write:

```text
jobs/<job_id>/job.json
jobs/<job_id>/manifest.json
```

Failed jobs keep `job.json` and `trace.jsonl` when a job was created.

### Document Semantics

- `document.md` is the primary artifact.
- `document.md` is always a conversion candidate. Ingest success does not verify factual correctness
  and does not create a trusted host revision.
- `ingest_document.json` stores the structured document object.
- `manifest.json` records job/run metadata and must not replace `ingest_document.json`.
- Plain text ingest must not rewrite body text beyond minimal newline and blank-line normalization.
- Chunk fields may exist in schemas, but chunking is reserved and off by default.

### Trace

- Every pipeline step must write structured JSONL trace events.
- Trace events use fixed event names, common fields, and a flexible `details` object.
- Worker parameters recorded in trace must redact sensitive values such as API keys, passwords,
  tokens, secrets, credentials, and cookies.

### Workers

- All workers return the shared `WorkerResult` shape.
- Heavy workers remain optional extras.
- Missing optional runtime dependencies should fail clearly or use documented fallback behavior.
- Quality gates must fail low-quality outputs rather than writing misleading `document.md` content.

### SDK

- The SDK is async-first: `submit -> job_id -> poll/wait -> result`.
- SDK results are path-first by default.
- Default content is no content or capped preview. Full `document.md` content must be explicit and
  size-limited.
- Default storage mode is persistent.
- Reserved `ephemeral` and `memory` modes must fail clearly until implemented.
- Core SDK names are `IngestClient`, `IngestJob`, `IngestResult`, `IngestError`, and `IngestWarning`.
- MCP is an adapter, not a facade backend.
- Provider batch lifecycle uses `batch_id` and separate batch methods. It must not be routed through
  ordinary `job_id` APIs.

### Error Model

- Job-level ingest failures after job creation return `IngestResult(status="failed")` or
  `IngestResult(status="partial")`.
- SDK configuration, transport, sandbox, protocol, invocation, and unknown job failures raise SDK
  exceptions.
- A wait timeout must not mark a job failed when the final job state is unknown.

### Security

- SDK and MCP local path access must honor allowed roots.
- SDK path sandboxing must reject sensitive files and symlink escapes where available.
- URL ingest must reject dangerous schemes and non-public hosts according to the SDK/service URL
  sandbox.
- MCP URL ingest is disabled unless `--enable-url-ingest` is passed.
- Capability Provider `submit()` must enforce provider profile and policy permissions before creating
  a Swallow job. Provider approval UI belongs to the host runtime, not to Swallow.

## Non-Goals

- Default summarization or cleanup.
- Default embeddings or chunking for RAG.
- Remote service authentication policy before it is explicitly designed.
- A universal in-memory ingest backend for heavy workers.

## Validation

Use narrow gates when changing the related surface:

```bash
uv run pytest -m contract -q
uv run pytest -m transport -q
uv run pytest -m security -q
uv run pytest -m adapter -q
uv run pytest -m concurrency -q
uv run python scripts/sdk_release_gate.py
```
