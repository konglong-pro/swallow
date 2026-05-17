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
```

The CLI may support `--store` to override this root, but the default is the current directory.

## Phase 1 Scope

Phase 1 only needs to run a local text-file ingest end to end:

```bash
swallow file ./sample.txt
```

It must generate:

- `raw_store/<sha256>/original.txt`
- `raw_store/<sha256>/original.meta.json`
- `jobs/<job_id>/document.md`
- `jobs/<job_id>/ingest_document.json`
- `jobs/<job_id>/trace.jsonl`

Commands for URL, browser capture, and archive ingest may exist as placeholders, but they must clearly report that they are not implemented in this phase.

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

1. Core models, stores, trace, registry, router, quality, markdown writer.
2. Worker interface and simple Phase 1 text worker.
3. CLI.
4. Tests for raw-store dedupe, trace, CLI file ingest, and Markdown output.
5. MarkItDown worker.
6. PaddleOCR worker.
7. faster-whisper worker.
8. Web workers.
9. Service API.
