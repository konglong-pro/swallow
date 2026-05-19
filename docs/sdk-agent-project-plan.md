# Swallow SDK Agent Project Plan

This document is an implementation brief for agents working on the Swallow SDK roadmap. The release gate lives in `docs/sdk-final-acceptance.md`.

Swallow is an ingest-only toolkit. Its job is to convert raw inputs into traceable Markdown artifacts. The SDK must make that ingest capability easy to use from agents and other applications without turning Swallow into an agent framework, RAG system, chat app, memory system, or knowledge-base product.

## Current Implementation Status

The SDK contract, four concrete SDK clients, the hybrid facade, and the first agent adapter are implemented:

- `LocalIngestClient`: in-process file ingest for lightweight local validation.
- `CliIngestClient`: subprocess-backed ingest through the `swallow` CLI for files, URLs, browser captures, and export archives.
- `HttpIngestClient`: HTTP service client for files, URLs, browser captures, and export archives, with explicit local service startup through `HttpIngestClient.start_local(...)`.
- `QueueIngestClient`: SQLite-backed persistent queue client with explicit worker execution through `swallow queue worker`.
- `IngestClient` and `create_ingest_client(...)`: hybrid Python facade over local, CLI, HTTP, and queue backends.
- `swallow mcp serve`: stdio MCP adapter backed by `CliIngestClient`.

The current shared SDK shape is Python snake_case:

- `submit_file`, `submit_url`, `submit_browser_capture`, `submit_archive`
- `get_job`
- `get_result`
- `wait`
- queue batch methods such as `submit_batch`, `get_batch_result`, and `wait_batch`
- convenience methods such as `file(..., wait=True)`
- facade capability discovery through static `BackendCapabilities`

The default content policy is:

- preview: 4 KiB
- full content: explicit opt-in, capped at 1 MiB
- full large Markdown bodies are never returned by default

Current verification baseline for SDK and service work:

```bash
uv run pytest tests/test_sdk_facade.py tests/test_sdk_queue.py tests/test_queue_worker.py tests/test_batch.py tests/test_mcp_server.py tests/test_sdk_http.py tests/test_service_api.py tests/test_sdk_cli.py tests/test_sdk_local.py tests/test_cli_file.py tests/test_cli_errors.py -q
```

The SDK final acceptance gate now passes locally through `uv run python scripts/sdk_release_gate.py`. The latest gate run passed contract, transport, security, adapter, concurrency, and full-suite checks; the full default suite reported 244 tests passed and 4 skipped. The MCP real network URL smoke also passed separately with `RUN_SWALLOW_NETWORK_TESTS=1`.

## Non-Negotiable Product Boundary

Core Swallow owns ingest only:

- Accept raw inputs such as files, PDFs, images, audio, video, URLs, browser captures, and export archives.
- Store immutable raw inputs in `raw_store/`.
- Run the ingest pipeline through registered workers.
- Write structured trace events.
- Produce a canonical `document.md`.
- Produce `ingest_document.json`.
- Preserve reproducibility and provenance.

Core Swallow does not own:

- Agent orchestration.
- Prompting.
- Summarization.
- Translation.
- LLM cleanup.
- Vector databases.
- Embeddings.
- Question answering.
- Memory.
- Default chunking.
- Knowledge-base management.

Adapters may expose Swallow to agent ecosystems, but adapters must not change the core artifact contract.

## Fixed SDK Policy

All SDK work must follow these five decisions.

### 1. Async-First

All ingest tasks are job-based internally:

```text
submit -> job_id -> poll/wait -> result
```

Convenience methods may wait for completion, but every task must still have a `job_id`.

Required implication:

- The SDK should model `submitFile`, `getJob`, `wait`, and `getResult`.
- A convenience `file(..., wait=True)` API is allowed.
- Slow workloads such as OCR, ASR, Playwright, video, web capture, and batch ingest must fit the same job model.

### 2. Persistent By Default

The default storage mode is persistent.

Default runtime artifacts are written under the active store root:

```text
raw_store/
  <sha256>/
    original.<ext>
    original.meta.json
jobs/
  <job_id>/
    job.json
    document.md
    ingest_document.json
    trace.jsonl
    intermediate/
```

Supported storage modes:

- `persistent`: default; writes durable raw inputs, jobs, traces, and artifacts.
- `ephemeral`: uses temporary disk storage; may clean up after successful completion.
- `memory`: only for small text-like inputs and transition checks.

`memory` mode must not be treated as a universal backend. It is not appropriate for OCR, ASR, video, Playwright, large PDFs, batch ingest, or export archives.

### 3. Path-First Return Model

SDK results return artifact paths by default.

Default responses should include:

- `jobId`
- `status`
- output paths
- document metadata
- warnings
- errors
- a small preview when requested or configured

Default responses must not include full large Markdown bodies.

Full content may be returned only when explicitly requested and capped by a hard byte limit. If content exceeds the cap, the SDK must return the path and mark the content as truncated or omitted.

### 4. Chunking Reserved But Off

The canonical output remains the complete `document.md`.

The SDK schema may reserve `chunks` and `chunksPath`, but chunking is disabled by default. Chunking must be explicitly requested and must never replace the full document artifact.

The default position is:

```text
ingest produces document.md
RAG or agent layers decide how to chunk
```

### 5. Hybrid Error Model

Tool-level ingest failures return an `IngestResult` with `status` set to `failed` or `partial`.

SDK invocation failures throw exceptions.

Return `failed` or `partial` results for failures such as:

- Unsupported input type.
- Damaged PDF.
- OCR quality below threshold.
- ASR produced no useful transcript.
- Web page returned 403 or 429.
- Missing optional worker runtime when fallback is possible.
- Archive format is unsupported.
- Worker completed but quality gates failed.

Throw exceptions for failures such as:

- SDK configuration error.
- Ingest binary not found.
- HTTP service unavailable.
- MCP server failed to start.
- Path outside allowed roots.
- Sandbox or permission rejection.
- Invalid protocol response.
- Invalid `job_id`.
- Wait timeout where the job state is unknown.
- SDK bug.

## Canonical Terms

Use these names in core SDK design:

- `IngestClient`
- `IngestJob`
- `IngestResult`
- `IngestError`
- `IngestWarning`

Avoid naming core objects `ToolResult`. Agent adapters may translate `IngestResult` into framework-specific tool result objects.

Use these artifact terms:

- Raw input: `raw_store/<sha256>/`
- Ingest job: `jobs/<job_id>/`
- Canonical Markdown: `document.md`
- Structured document: `ingest_document.json`
- Execution trace: `trace.jsonl`

Do not introduce `manifest.json` casually. If a manifest is added later, define it as a job or run manifest. It must not duplicate or replace `ingest_document.json`.

Current status: `manifest.json` is now part of the persisted job artifact set. It records job status, route, workers, outputs, warnings, and errors. It complements `ingest_document.json`; it does not replace the structured document artifact.

## Suggested Result Shape

This is a planning schema, not a requirement to implement TypeScript.

```ts
type IngestStatus = "queued" | "running" | "success" | "partial" | "failed";

type StorageMode = "persistent" | "ephemeral" | "memory";

type ReturnContentMode = "none" | "preview" | "full";

type IngestResult = {
  jobId: string;
  status: IngestStatus;

  outputs: {
    markdownPath?: string;
    documentJsonPath?: string;
    tracePath?: string;
    chunksPath?: string;
  };

  document?: {
    id: string;
    title?: string;
    sourceType: string;
    mimeType?: string;
    sha256?: string;
  };

  preview?: {
    text: string;
    truncated: boolean;
  };

  chunks?: ChunkRef[];

  warnings: IngestWarning[];
  errors: IngestError[];
};

type IngestError = {
  code: string;
  message: string;
  stage?: string;
  worker?: string;
  retryable: boolean;
  recoverable: boolean;
  details?: Record<string, unknown>;
};
```

## Transport-Neutral Architecture

The SDK contract must be transport-neutral.

The same `IngestClient` concept should eventually support multiple backends:

- `IngestClient`: unified Python facade over local, CLI, HTTP, and queue clients.
- `LocalIngestClient`: calls the Python pipeline in process.
- `CliIngestClient`: calls the `swallow` CLI as a subprocess.
- `HttpIngestClient`: calls a local or remote Swallow service.
- `McpIngestAdapter`: exposes Swallow through MCP tools.
- `QueueIngestClient`: submits jobs to async workers.

All backends must preserve the same semantic result contract even if their mechanics differ.

## Implementation Roadmap

Implement in the following order.

## Phase 0: SDK Contract (Done)

Purpose: lock the API and artifact contract before adding transports.

Deliverables:

- SDK design document.
- Stable `IngestResult` schema.
- Stable `IngestJob` schema.
- Storage mode policy.
- Return content policy.
- Error model policy.
- Adapter boundary policy.

Success criteria:

- The roles of `document.md`, `ingest_document.json`, and `trace.jsonl` are explicit.
- The default return model is path-first.
- The storage default is persistent.
- `memory` mode restrictions are documented.
- Tool-level failures and SDK invocation failures are clearly separated.
- Adapters are prohibited from changing the core ingest contract.

## Phase 1: In-Process SDK (Done)

Purpose: early local prototype and core pipeline validation.

Use this for lightweight local tools only.

Advantages:

- Simple.
- Low latency.
- Easy to test.
- Useful for validating the core pipeline.

Costs:

- Heavy dependency exposure.
- Poor worker isolation.
- Not ideal for OCR, ASR, Playwright, video, or complex web ingest.

Scope:

- Support lightweight file ingest.
- Prefer text, Markdown, and small HTML-like inputs.
- Reuse the same core pipeline as the CLI.
- Preserve `job_id`, raw store, job directory, trace, and canonical outputs.
- Current implementation exposes `LocalIngestClient` for file ingest only.

Success criteria:

- A local caller can submit a file ingest task.
- Every task has a `job_id`.
- Default storage writes `raw_store/` and `jobs/`.
- The returned object maps to `IngestResult`.
- The CLI can reuse the same pipeline behavior.

## Phase 2: CLI Wrapper SDK (Done)

Purpose: first formal agent integration path.

This is the priority SDK integration layer.

The SDK should call the `swallow` CLI as a subprocess and parse machine-readable output into `IngestResult`.

Advantages:

- Decoupled from agent process runtime.
- Stable.
- Fast to build.
- Easier to sandbox.
- Easier to wrap from Python, Node, or other languages.

Costs:

- Process startup overhead.
- Must manage paths, working directories, stdout, stderr, and exit codes.
- Requires a stable CLI JSON protocol.

Scope:

- File, URL, browser capture, and export archive ingest.
- `wait` behavior for convenience calls.
- Preview support.
- Path-first response.
- Distinction between failed ingest results and SDK exceptions.
- Default allowed roots are `cwd` and `store_root`; callers can pass explicit `allowed_roots`.

Success criteria:

- An agent can invoke local file ingest through the SDK without importing the pipeline directly.
- CLI JSON output is machine-readable and stable.
- CLI exit code semantics are documented.
- Task failure, partial success, and invocation failure are distinguishable.

Implemented CLI machine protocol:

- `--json` emits one terminal `IngestResult` JSON object.
- `--jsonl` emits a `job_submitted` event first, then a terminal result event.
- Exit code `0` means success.
- Exit code `2` means partial.
- Exit code `1` means failed ingest result.
- CLI startup, config, and protocol failures are SDK invocation failures.

## Phase 3: HTTP Service SDK (Done)

Purpose: shared team service and cross-language access.

The SDK should call a local or remote Swallow HTTP service.

Advantages:

- Cross-language.
- Remote-capable.
- Easier to scale behind a service boundary.
- Centralized worker management.

Costs:

- Requires service lifecycle management.
- Requires API design.
- Requires upload, download, auth, retention, and cleanup policies.

Scope:

- Submit ingest jobs.
- Upload or reference inputs.
- Poll job status.
- Fetch result metadata.
- Read or download artifacts.
- Query trace when allowed.
- Health checks.
- Current implementation uploads local files for file and archive ingest, posts JSON for URL and browser capture ingest, and reads persisted results through the service.

Implemented v1 endpoints:

```text
POST /v1/ingest/file
POST /v1/ingest/url
POST /v1/ingest/browser-capture
POST /v1/ingest/archive
GET /v1/jobs/{job_id}
GET /v1/jobs/{job_id}/result
GET /v1/jobs/{job_id}/document
GET /v1/jobs/{job_id}/manifest
GET /v1/jobs/{job_id}/trace
GET /health
```

Current HTTP contract:

- `POST /v1/ingest/*` defaults to `wait=false` and returns an `IngestJob`.
- `POST /v1/ingest/*?wait=true` returns an `IngestResult`.
- `GET /v1/jobs/{job_id}` returns an `IngestJob`.
- `GET /v1/jobs/{job_id}/result?content=none|preview|full` returns an `IngestResult`.
- Tool-level ingest failures after job creation return HTTP 200 plus `IngestResult(status="failed")`.
- Pre-job invocation/config/input failures return HTTP error responses and map to SDK exceptions.
- Legacy `/ingest/*` routes remain for compatibility and keep the older synchronous shape.
- Authentication is intentionally out of scope for this phase.

Success criteria:

- HTTP results map to the same `IngestResult` semantics as CLI and local SDK.
- Ingest task failures do not become HTTP 500 responses unless the service itself failed.
- Job polling is reliable.
- Local service startup and shutdown are explicit.
- Authentication, retention, remote artifact authorization, and deployment hardening stay out of this phase.

Current local service lifecycle:

- `HttpIngestClient(base_url=...)` connects to an existing service.
- `HttpIngestClient.start_local(store_root=..., config_path=...)` explicitly starts a local uvicorn subprocess.
- `shutdown()` cleans up client resources and any owned local service subprocess.

## Phase 4: MCP Server (Done)

Purpose: agent tool ecosystem integration.

The MCP server can now build on the stable SDK result contract shared by the local, CLI, and HTTP clients.

Advantages:

- Standard tool protocol.
- Broad agent compatibility.
- Reduces the need for custom adapters for every framework.

Costs:

- Security boundaries must be strict.
- File path access must be controlled.
- Large content must not be returned into model context by default.

Implemented scope:

- `swallow_ingest_file`
- `swallow_ingest_url`
- `swallow_ingest_browser_capture`
- `swallow_ingest_archive`
- `swallow_get_job`
- `swallow_get_result`
- `swallow_wait_for_result`

Required safety rules:

- Enforce allowed roots for local paths.
- Default to path plus preview.
- Do not return full large Markdown by default.
- Treat wait timeout as an invocation issue unless the job reached a known failed state.
- Keep MCP output compatible with `IngestResult`.
- Default to stdio transport.
- Do not expose MCP resources or prompts in this slice.
- Disable URL ingest unless `--enable-url-ingest` is passed.
- Allow only absolute `http` and `https` URLs when URL ingest is enabled.

Success criteria:

- MCP tools preserve the path-first model.
- MCP tools cannot read arbitrary filesystem paths.
- Large documents are not placed directly into agent context by default.
- MCP results map cleanly back to `IngestResult`.
- The server is started through `swallow mcp serve`.

## Phase 5: Async Queue SDK (Done)

Purpose: production long-running jobs.

This phase handles OCR, ASR, video, batch ingest, complex websites, and other slow workloads.

Advantages:

- Retryable.
- Scalable.
- Worker crashes can be isolated.
- Long jobs do not block callers.

Costs:

- More infrastructure.
- Requires job state storage.
- Requires retry, timeout, cancellation, and cleanup policy.
- Requires idempotency design.

Scope:

- Durable local SQLite queue under `queue/queue.sqlite3`.
- `QueueIngestClient` for file, URL, browser capture, archive, and submit-time batch snapshots.
- Explicit worker execution through `swallow queue worker`.
- Queue admin commands: `swallow queue stats` and `swallow queue cancel`.
- Retryable-only automatic retry with stable `job_id`.
- Lease plus heartbeat recovery for stale running jobs.
- Pending cancellation and running cancel requests without hard-killing active work.
- Batch summary and trace persistence under `batch_runs/<batch_id>/`.

Success criteria:

- Long jobs do not block SDK callers.
- Job state is reliably persisted.
- Retryable and recoverable errors are explicit.
- Failed workers leave useful trace data.
- The artifact contract remains unchanged.
- Queue support remains persistent-only in this slice.

## Phase 6: Hybrid Facade SDK (Done)

Purpose: final unified Python SDK shape.

The facade lets callers use one client while switching underlying Python SDK backends.

Target shape:

```python
from swallow.sdk import IngestClient, create_ingest_client

client = IngestClient(backend="auto", store_root=".")
job = client.submit_file("sample.txt")
result = client.wait(job.job_id)
```

Advantages:

- Unified caller experience.
- Backend can be selected by environment.
- Agent integrations depend on one stable contract.

Costs:

- Requires strong capability negotiation.
- Backend differences must be explicit.
- The facade must not hide important safety or persistence differences.

Scope:

- Python-only facade over `LocalIngestClient`, `CliIngestClient`, `HttpIngestClient`, and `QueueIngestClient`.
- Static `BackendCapabilities` discovery.
- Explicit backend selection plus `backend="auto"` pre-submit selection.
- Conservative auto routing: file -> local, URL/browser capture/archive -> CLI, batch/admin -> queue.
- No post-submit silent fallback.
- Persistent facade registry under `sdk/facade_registry.jsonl` for facade-created jobs and batches.
- Separate registry records for jobs and batches.
- Common constructor parameters plus grouped backend config such as `cli={...}`, `http={...}`, and `queue={...}`.
- Facade-specific SDK exceptions for unsupported capabilities and registry failures.
- Shared path-first `IngestResult` and `IngestBatchResult` semantics.

Success criteria:

- Callers do not need to know whether the backend is CLI, HTTP, local, or queue for common flows.
- Backend-specific capability gaps are represented explicitly.
- All successful ingest paths still produce or reference `document.md`.
- The core artifact contract remains stable.
- MCP remains an adapter, not a facade backend.

## Adapter Rules

Agent adapters may exist for ecosystems such as LangChain, LlamaIndex, AutoGen, MCP, or ChatGPT Apps.

Adapters must:

- Wrap `IngestResult`, not replace it.
- Preserve `job_id`.
- Preserve artifact paths.
- Preserve trace access where allowed.
- Use preview by default.
- Require explicit opt-in for full content.
- Respect storage mode policy.
- Respect allowed roots and sandbox limits.

Adapters must not:

- Add summarization to core ingest.
- Add embeddings to core ingest.
- Change `document.md` semantics.
- Hide failed ingest results as successful tool calls.
- Return large Markdown content by default.
- Implement framework-specific behavior in the core package.

## Preferred Build Order

1. Done: Write and approve the SDK contract.
2. Done: Add the lightweight in-process SDK for local validation.
3. Done: Add the CLI wrapper SDK as the first practical agent integration.
4. Done: Add the HTTP service SDK for shared and remote use.
5. Done: Build the MCP server now that the result contract is stable across three backends.
6. Done: Add queue-backed execution for production long tasks.
7. Done: Consolidate the hybrid Python facade after MCP and queue work.
8. Next: Harden SDK integrations and documentation without expanding Swallow beyond ingest.

## Verification Expectations

For each implementation phase, agents must provide executable checks where possible.

Minimum verification should cover:

- Successful file ingest.
- Persistent artifact creation.
- Path-first result shape.
- Preview behavior.
- Failed ingest result behavior.
- SDK exception behavior.
- Trace file creation.
- `ingest_document.json` creation.

Do not claim checks passed unless they were run.

## Open Decisions

Resolved decisions:

- CLI JSON output shape: `--json` returns terminal `IngestResult`; `--jsonl` emits `job_submitted` followed by terminal result.
- CLI exit code policy: success `0`, partial `2`, failed ingest `1`.
- Full-content byte cap: 1 MiB.
- Preview byte default: 4 KiB.
- CLI wrapper allowed root default: `cwd` and `store_root`.
- HTTP v1 default submission mode: async `wait=false`.
- HTTP tool failure status: HTTP 200 plus `IngestResult(status="failed")` after a job has been created.
- HTTP SDK local service lifecycle: explicit `HttpIngestClient.start_local(...)`.
- MCP backend: stdio tools backed by `CliIngestClient`.
- MCP allowed root default: `cwd` and `store_root`.
- MCP URL policy: disabled by default; when enabled, only absolute `http` and `https` URLs are accepted.
- MCP content policy: default preview, explicit full content allowed with the existing 1 MiB cap.
- Queue backend: local SQLite, no external broker dependency.
- Queue execution: explicit worker process, one job per process; parallelism comes from multiple workers.
- Queue retries: only retry core errors with `retryable=true`, default `max_attempts=3`, stable `job_id`.
- Queue cancellation: pending jobs can be canceled; running jobs can be marked cancel-requested but are not hard-killed.
- Queue batch contract: `IngestBatch` and `IngestBatchResult`, separate from `IngestJob`.
- Hybrid facade scope: Python SDK backends only; MCP remains an adapter.
- Hybrid facade selection: explicit backend by default, with `backend="auto"` doing pre-submit selection only.
- Hybrid facade auto routing: file -> local, URL/browser capture/archive -> CLI, batch/admin -> queue.
- Hybrid facade capabilities: static `BackendCapabilities`, not dynamic health probing.
- Hybrid facade registry: persistent `sdk/facade_registry.jsonl` with separate job and batch records.

Still open before broad SDK release:

- Artifact retention policy for `ephemeral` mode.
- Whether to expose artifact download/list endpoints beyond the existing document, manifest, and trace endpoints.
- Authentication and authorization policy for non-local HTTP deployments.

## Hard Stop List

Do not implement these as part of SDK work unless the project owner explicitly changes scope:

- Agent framework.
- RAG pipeline.
- Vector database integration.
- Memory system.
- Chat UI.
- Default chunking.
- Default summarization.
- Default embedding.
- LLM cleanup during ingest.
- Returning full large documents to agents by default.
