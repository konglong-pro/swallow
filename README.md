# swallow

Swallow is an ingest-only toolkit for converting raw inputs into traceable Markdown artifacts.

The canonical successful output is always `jobs/<job_id>/document.md`, with structured metadata in
`ingest_document.json` and reproducibility events in `trace.jsonl`.

## Boundary

Swallow does ingest. It stores immutable raw inputs, routes them through workers, writes trace data,
and produces Markdown.

Swallow does not implement agents, RAG, vector databases, memory, chat, summarization, translation,
or knowledge-base management. Agent integrations belong in thin adapters around Swallow results.

## Supported Inputs

- Local files: text, Markdown, Office, PDF, HTML, CSV, JSON, XML, EPUB-style inputs.
- OCR inputs: scanned PDFs and images through the optional PaddleOCR worker.
- Audio/video: timestamped transcripts through the optional faster-whisper worker.
- URLs: platform-aware routes plus Firecrawl, Crawl4AI, and Playwright fallback for public and dynamic pages.
- Local capture: browser extension capture JSON and local Playwright profile capture.
- Export archives: current ChatGPT export zip parsing.

Worker dependencies are optional. Install only the extras needed for the ingest path you run.

## Install

Prerequisites:

- Python 3.11+
- `uv`

```bash
uv sync --dev
uv run swallow --help
```

Optional extras:

```bash
uv sync --extra markitdown
uv sync --extra ocr
uv sync --extra asr
uv sync --extra service
uv sync --extra web
uv sync --extra mcp
```

For all local runtime workers:

```bash
uv sync --dev --all-extras
uv run playwright install chromium
```

## Quick Start

Ingest one local file:

```bash
uv run swallow file ./sample.txt
```

Ingest other supported sources:

```bash
uv run swallow url https://example.com/article
uv run swallow browser-capture ./capture.json
uv run swallow archive ./chatgpt-export.zip
uv run swallow batch "tests/fixtures/**/*" --workers 4
```

Inspect results:

```bash
uv run swallow jobs
uv run swallow inspect <job_id> --raw --artifacts
uv run swallow trace <job_id> --tail 20
uv run swallow open <job_id>
```

Runtime artifacts are written under the current working directory by default:

```text
raw_store/
jobs/
queue/
batch_runs/
sdk/
```

Use `--store <path>` to choose another store root.

## SDK Quick Start

```python
from swallow.sdk import IngestClient

client = IngestClient(store_root=".", backend="auto")
result = client.file("sample.txt", wait=True)

print(result.job_id)
print(result.outputs.markdown_path)
```

The SDK is async-first internally: submit methods return an `IngestJob`, and callers can poll or wait
for an `IngestResult`. Results are path-first by default. Preview content is capped, and full
`document.md` content must be explicitly requested.

## Capability Provider

Agent runtimes should use `SwallowCapabilityProvider` when they need discovery, planning, policy
checks, job/batch lifecycle, bounded artifact reads, and cancellation behind one provider-shaped
adapter. Provider outputs are conversion candidates and evidence, not host-trusted revisions.

## Service, Queue, And MCP

Run the local HTTP service:

```bash
uv sync --dev --extra service
uv run uvicorn swallow.service.api:app --host 127.0.0.1 --port 8765
```

Run a queue worker:

```bash
uv run swallow queue worker
```

Run the MCP adapter:

```bash
uv sync --dev --extra mcp
uv run swallow mcp serve
```

The MCP adapter is backed by `SwallowCapabilityProvider`. URL ingest is disabled unless
`--enable-url-ingest` is passed.

## Common Validation

Default local gate:

```bash
uv run --no-sync pytest -q -m "not network and not performance and not heavy"
uv run --no-sync python -m compileall -q src tests scripts
```

SDK release gate:

```bash
uv run python scripts/sdk_release_gate.py
```

Runtime smoke suite:

```bash
uv run --no-sync python scripts/runtime_smoke_all.py
```

## Documentation

- Current work: `docs/active/current.md`
- Project status: `docs/project-status.md`
- Architecture: `docs/architecture.md`
- Development setup and runtime workers: `docs/development.md`
- Testing and release gates: `docs/testing.md`
- CLI, service, SDK, queue, and MCP surfaces: `docs/api.md`
- Durable ingest and SDK contract: `docs/contracts/ingest-contract.md`
- Capability Provider P1: `docs/capability-provider-p1.md`
- SDK implementation plan: `docs/sdk-agent-project-plan.md`
- SDK final acceptance gate: `docs/sdk-final-acceptance.md`
