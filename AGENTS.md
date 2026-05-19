# AGENTS.md

## Project: swallow

This repository implements an ingest-only toolkit.

Its job is to take raw inputs such as files, PDFs, images, audio, video, URLs, browser captures, and export archives, then convert them into a unified `IngestDocument.md`.

This project is infrastructure for other agents and applications, but this repository itself must stay focused on ingest.

Do not turn this project into an agent framework, RAG system, chat app, memory system, or knowledge-base product.

## Current Naming

- Repository: `swallow`
- Python distribution: `swallow`
- Python import namespace: `swallow`
- CLI command: `swallow`

## Core Goal

Build a modular, upgradeable ingest pipeline with pluggable workers:

- General file conversion: `MarkItDown`
- OCR: `PaddleOCR`
- ASR: `faster-whisper`
- Web ingest Level 1: `Firecrawl` / `Crawl4AI`
- Web ingest Level 2: `Playwright`
- Web ingest Level 3:
  - local browser extension capture
  - local Playwright profile capture
  - official export archive parsing

The final primary artifact is always:

```text
document.md
```

## Hard Boundaries

Swallow does not do:

- Agent calling
- Vector databases
- Question answering
- Memory
- Multi-agent orchestration
- Frontend knowledge-base management
- Summarization, rewriting, translation, or LLM cleanup during ingest

The ingest layer must preserve traceability and reproducibility.

## SDK Direction

Swallow is being packaged as an SDK so agents and applications can call ingest reliably.

The SDK must expose ingest capabilities without changing the repository's ingest-only scope. Core Swallow must not become an agent framework. Agent integrations belong in thin adapter layers that wrap Swallow results for a target ecosystem.

Detailed SDK planning lives in `docs/sdk-agent-project-plan.md`. Final SDK release gates and the hardening checklist live in `docs/sdk-final-acceptance.md`.

## Current SDK Status

The SDK contract, four concrete SDK clients, the hybrid facade, and the first agent adapter are implemented:

1. `LocalIngestClient`: in-process SDK for lightweight local file ingest.
2. `CliIngestClient`: subprocess wrapper around the `swallow` CLI for file, URL, browser capture, and archive ingest.
3. `HttpIngestClient`: HTTP client for a Swallow FastAPI service, including explicit local service startup through `start_local()`.
4. `QueueIngestClient`: SQLite-backed persistent queue client with explicit worker execution through `swallow queue worker`.
5. `IngestClient` and `create_ingest_client()`: hybrid Python SDK facade over local, CLI, HTTP, and queue backends.
6. `swallow mcp serve`: stdio MCP adapter backed by `CliIngestClient`.

The shared SDK contract is path-first and async-first:

- Submit methods return an `IngestJob`.
- `get_job`, `get_result`, and `wait` expose job state and terminal `IngestResult` data.
- Convenience methods such as `file(..., wait=True)` may wait, but the task still has a `job_id`.
- Default result content is a small preview. Full `document.md` content must be explicitly requested and is capped.

The current HTTP v1 API is also async-first:

- `POST /v1/ingest/*` defaults to `wait=false` and returns an `IngestJob`.
- `POST /v1/ingest/*?wait=true` returns an `IngestResult`.
- `GET /v1/jobs/{job_id}` returns an `IngestJob`.
- `GET /v1/jobs/{job_id}/result` returns an `IngestResult`.

Legacy `/ingest/*` service routes remain for compatibility and keep their older synchronous shape, but new SDK work should target `/v1`.

The current MCP adapter exposes tools only, not resources or prompts. It preserves the SDK path-first result contract, disables URL ingest unless `--enable-url-ingest` is passed, and keeps `content="preview"` as the default result mode.

The current hybrid facade is Python-only. It does not treat MCP as a backend. `backend="auto"` performs only pre-submit backend selection, using local for file ingest, CLI for URL/browser capture/archive ingest, and queue for batch/admin operations. It records facade-created jobs and batches in a small persistent registry under `sdk/facade_registry.jsonl` so later facade instances can route `get_job`, `get_result`, and wait calls.

The SDK final acceptance gate passes locally through `uv run python scripts/sdk_release_gate.py`. This verifies contract, transport, security, adapter, concurrency, and full-suite checks. A versioned release artifact or tag has not been cut in this working tree.

## SDK Policy

All SDK work must follow these decisions:

1. Async-first: every ingest task is internally job-based with `submit -> job_id -> poll/wait -> result`.
2. Persistent by default: the default mode writes `raw_store/`, `jobs/`, `document.md`, `ingest_document.json`, and `trace.jsonl`.
3. Path-first returns: SDK results return artifact paths and metadata by default, with optional preview. Full content must be explicit and size-limited.
4. Chunking reserved but off: schemas may reserve chunk fields, but the canonical output remains the complete `document.md`.
5. Hybrid error model: tool-level ingest failures return failed or partial results; SDK configuration, transport, sandbox, protocol, and invocation failures raise exceptions.

Use core SDK names such as `IngestClient`, `IngestJob`, `IngestResult`, `IngestError`, and `IngestWarning`. Avoid naming core SDK objects `ToolResult`; adapters may translate `IngestResult` into framework-specific tool result objects.

## SDK Backend Order

Build SDK support in this order. Completed items are part of the current baseline and should be preserved.

1. Done: SDK contract and artifact contract.
2. Done: In-process SDK for lightweight local validation only.
3. Done: CLI Wrapper SDK as the first formal agent integration path.
4. Done: HTTP Service SDK for local and remote service use.
5. Done: MCP Server for agent tool ecosystems.
6. Done: Async Queue SDK for production long-running jobs.
7. Done: Hybrid Facade SDK as the unified Python client over local, CLI, HTTP, and queue backends.
8. Next: SDK hardening and integration polish.

The SDK contract must stay transport-neutral. All backends should preserve the same `IngestResult` semantics and must not weaken the `document.md` artifact contract.

## Four Stable Primitives

These are the core invariants. Do not weaken them.

1. `RawStore`: raw inputs are immutable and stored by content hash.
2. `WorkerResult`: all workers return one shared result shape.
3. `Trace`: every pipeline step is written as structured JSONL.
4. `IngestDocument.md`: every successful ingest produces one standardized Markdown artifact.

## Storage Layout

By default, runtime artifacts are written under the current working directory:

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
queue/
  queue.sqlite3
batch_runs/
  <batch_id>/
    summary.json
    trace.jsonl
sdk/
  facade_registry.jsonl
```

The CLI may support `--store` to override this root, but the default is the current directory.

## Current Implementation Scope

The repository has moved beyond the original Phase 1 text-file slice.

Current ingest surfaces include:

- CLI ingest for files, URLs, browser captures, export archives, batch ingest, reruns, job inspection, artifacts, raw metadata, and trace reads.
- Service API ingest for files, URLs, browser captures, export archives, reruns, job reads, result reads, document reads, manifest reads, and trace reads.
- SDK clients for local in-process file ingest, CLI wrapper ingest, HTTP service ingest, and SQLite queue-backed ingest.
- Hybrid Python SDK facade over local, CLI, HTTP, and queue clients.
- MCP tools for file, URL, browser capture, archive, job, result, and wait operations.
- Queue CLI commands for worker execution, stats, and pending cancellation.

The canonical successful job artifacts remain:

- `raw_store/<sha256>/original.<ext>`
- `raw_store/<sha256>/original.meta.json`
- `jobs/<job_id>/job.json`
- `jobs/<job_id>/document.md`
- `jobs/<job_id>/ingest_document.json`
- `jobs/<job_id>/trace.jsonl`
- `jobs/<job_id>/manifest.json`

## Accepted Phase 1 Decisions

- `raw_id` is derived from sha256: `raw_<first_12_sha_chars>`.
- `job_id` and `doc_id` use ULID-style IDs: `ing_<ulid>` and `doc_<ulid>`.
- `trace.jsonl` uses fixed event names with common fields and a flexible `details` object.
- `document.md` uses YAML front matter.
- `ingest_document.json` stores the structured document object, not just duplicated front matter.
- Plain text ingest does not rewrite body text beyond minimal newline and blank-line normalization.
- Python uses `uv`, `pyproject.toml`, and Python `3.11+`.
- Heavy integrations are optional extras.

## Development Order

Completed baseline:

1. Core models, stores, trace, registry, router, quality, markdown writer.
2. Worker interface and plain text worker.
3. CLI and service API.
4. Tests for raw-store dedupe, trace, CLI ingest, service API, Markdown output, SDK clients, and security boundaries.
5. MarkItDown, PaddleOCR, faster-whisper, web, browser capture, and export archive workers.
6. SDK contract, `LocalIngestClient`, `CliIngestClient`, and `HttpIngestClient`.
7. MCP Server backed by `CliIngestClient`.
8. Async Queue SDK backed by local SQLite and explicit `swallow queue worker` processes.
9. Hybrid Facade SDK over local, CLI, HTTP, and queue clients.

Next planned work:

1. SDK hardening and integration polish without expanding Swallow beyond ingest.
