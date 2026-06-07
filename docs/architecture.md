# Architecture

## Purpose

Swallow is a modular ingest pipeline. It turns raw inputs into a canonical Markdown artifact while
preserving raw bytes, structured metadata, and trace events.

## Data Flow

```text
input path/url/capture/archive
  -> RawStore
  -> detector/classifier
  -> Router
  -> worker chain and fallback
  -> quality checks
  -> normalizer
  -> MarkdownWriter
  -> job artifacts and trace
```

## Stable Primitives

1. `RawStore`: immutable raw inputs stored by content hash.
2. `WorkerResult`: one shared result shape from workers.
3. `Trace`: structured JSONL events for every pipeline step.
4. `document.md`: the canonical Markdown artifact for successful ingest.

## Main Packages

- `swallow.core`: runner, raw store, job store, queue store, trace, quality, router, config, doctor.
- `swallow.detectors`: file type, PDF, and URL classification.
- `swallow.workers`: plain text, MarkItDown, PaddleOCR, faster-whisper, web, browser capture,
  Playwright profile, and export archive workers.
- `swallow.normalizers`: Markdown and conversation normalization.
- `swallow.cli`: human and machine CLI entry points.
- `swallow.service`: FastAPI service wrapper around the ingest runner.
- `swallow.sdk`: local, CLI, HTTP, queue clients and the hybrid facade.
- `swallow.mcp`: MCP tools adapter backed by an SDK client.

## Worker Routing

- Plain text and Markdown use the lightweight local text path.
- Office, extractable PDFs, HTML, CSV, JSON, XML, and EPUB-style inputs can use MarkItDown.
- Scanned or low-text PDFs and image inputs route to PaddleOCR when the OCR extra is installed.
- Audio and video route to faster-whisper with `ffmpeg` normalization.
- Public URL ingest can use Firecrawl, Crawl4AI, and Playwright fallback.
- Login-required and restricted URLs route to local browser capture paths: browser capture JSON,
  local persistent Playwright profile capture, or official export archive parsing.

## Storage Layout

Default store root is the current working directory:

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
    manifest.json
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

Use the CLI `--store` option or SDK `store_root` argument to write elsewhere.

## Service Boundary

The FastAPI service is intentionally thin. It accepts uploads or JSON payloads, hands them to the same
ingest runner, and exposes job/result/artifact/trace reads. The service does not own worker semantics
or a separate artifact model.

## SDK Boundary

The SDK is transport-neutral. Local, CLI, HTTP, queue, and facade clients preserve shared
`IngestJob` and `IngestResult` semantics. The facade performs conservative pre-submit selection only:

- file -> local
- URL/browser capture/archive -> CLI
- batch/admin -> queue

MCP remains an adapter and is not a facade backend.

## Artifact Boundary

The durable artifact rules live in `docs/contracts/ingest-contract.md`. In short:

- `document.md` is canonical.
- `ingest_document.json` is the structured document object.
- `trace.jsonl` is required for reproducibility.
- `manifest.json` is supplemental job/run metadata.
