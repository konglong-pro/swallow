# swallow

Swallow is an ingest-only toolkit for converting raw inputs into traceable Markdown documents.

The first milestone is intentionally small: local text files go through the same immutable raw store, job, trace, worker, quality, normalizer, and document writer pipeline that later workers will use.

Phase 2 adds a `markitdown_worker` for Office, PDF, HTML, CSV, JSON, XML, and EPUB style inputs. It is optional; install the extra before using those formats.

PDF files are analyzed before routing. Extractable text PDFs go through MarkItDown with an OCR fallback step in the plan. Scanned or very low-text PDFs are routed to `paddleocr_worker`.

Phase 3 adds `paddleocr_worker`. It can render PDFs into per-page PNG files under `jobs/<job_id>/intermediate/pages/`, run a PaddleOCR-compatible engine, write `intermediate/paddleocr/result.json`, and produce page-delimited OCR Markdown. PaddleOCR itself remains an optional runtime dependency.

Phase 4 adds `faster_whisper_worker` for audio and video files. It normalizes inputs with `ffmpeg`, writes `intermediate/audio/normalized.wav`, stores segment JSON at `intermediate/asr/transcript.json`, and produces timestamped transcript Markdown. `faster-whisper` remains an optional runtime dependency.

Phase 5 adds URL Level 1 ingest. Public URLs are stored as immutable URL reference payloads, routed to `firecrawl_worker`, quality checked, and can fall back to `crawl4ai_worker` before Markdown normalization.

Phase 6 adds URL Level 2 fallback through `playwright_worker`. Dynamic URLs route to Crawl4AI first and can fall back to Playwright. Public static URLs can now fall through Firecrawl, Crawl4AI, and then Playwright if quality remains low. Playwright saves rendered HTML and screenshots under `jobs/<job_id>/intermediate/playwright/`.

Phase 7 adds local login-state ingest paths. `browser_capture_worker` converts structured browser extension capture JSON into conversation Markdown and preserves `raw_dom` as an artifact. `playwright_profile_worker` can render URL pages with a local persistent browser profile for authenticated content. `export_archive_worker` adds a first ChatGPT export zip parser for `conversations.json`; other archive types remain explicit future work.

The service layer wraps the same ingest runner with FastAPI. It is intentionally thin: uploaded files or JSON payloads are handed to the existing RawStore, Router, workers, Trace, Quality, and MarkdownWriter pipeline.

Configuration is optional. Swallow looks for `swallow.config.yaml` in the current directory, accepts `--config` in the CLI, and honors `SWALLOW_CONFIG` for the service process. See `swallow.config.example.yaml`.

## Phase 1 Quick Start

```bash
uv sync --dev
uv run swallow file ./sample.txt
```

For MarkItDown-backed formats:

```bash
uv pip install --python ./.venv/Scripts/python.exe --default-index https://mirrors.cloud.tencent.com/pypi/simple "markitdown[docx,pdf,pptx,xlsx]==0.1.5"
uv run swallow file ./report.docx
uv run swallow file ./report.pdf
uv run swallow file ./sheet.xlsx
```

On this Windows development machine, the project is pinned to Python 3.11 through `.python-version`. Large binary wheels such as `numpy` and `onnxruntime` are slow from the default PyPI CDN, so the Tencent PyPI mirror is used for installing MarkItDown runtime dependencies.

For OCR-backed formats:

```bash
uv sync --dev --extra ocr --default-index https://mirrors.cloud.tencent.com/pypi/simple
uv run swallow file ./scanned.pdf
uv run swallow file ./screenshot.png
```

The OCR extra pins `paddlepaddle==2.6.2` with `paddleocr==2.10.0`. On this machine, `paddlepaddle==3.3.1` imports successfully but fails during OCR detection with a OneDNN runtime error.

To verify the local OCR runtime with both an image and an image-only scanned PDF:

```bash
uv run --no-sync python scripts/ocr_runtime_smoke.py
```

For ASR-backed formats:

```bash
uv sync --dev --extra asr --default-index https://mirrors.cloud.tencent.com/pypi/simple
uv run swallow file ./meeting.mp3
uv run swallow file ./video.mp4
```

The ASR worker uses `ffmpeg` for audio normalization. It first honors `ffmpeg_path` / `SWALLOW_FFMPEG_PATH`, then `ffmpeg` on `PATH`, then the `imageio-ffmpeg` binary included by the ASR extra. Runtime settings can be overridden with `SWALLOW_FASTER_WHISPER_MODEL`, `SWALLOW_FASTER_WHISPER_DEVICE`, and `SWALLOW_FASTER_WHISPER_COMPUTE_TYPE`.

To verify the local ASR runtime:

```bash
uv run --no-sync python scripts/asr_runtime_smoke.py
```

Pass `--audio ./sample.wav` to use a known fixture instead of Windows speech synthesis.

For URL ingest:

```bash
uv sync --dev --extra web --default-index https://mirrors.cloud.tencent.com/pypi/simple
uv run swallow url https://example.com/article
```

Firecrawl requires its normal API configuration, such as `FIRECRAWL_API_KEY`. The environment variable name can be changed with `workers.firecrawl.api_key_env`. Crawl4AI is used for dynamic pages and as the Level 1 fallback path.

Playwright requires browser binaries after installing the web extra:

```bash
uv run playwright install chromium
```

Login-required and restricted URLs route to `playwright_profile_worker` first. It uses a local persistent profile directory, defaults to `~/.swallow/browser-profiles/chrome-default`, and saves artifacts under `jobs/<job_id>/intermediate/playwright_profile/`. Run with `headless: false` the first time so you can log in locally; cookies stay on this machine.

URL quality checks detect common authentication walls such as login pages, "continue with Google" screens, restricted status codes, and JavaScript-required placeholders. If all fallback workers still leave the final quality score below `0.45`, the job fails with `QUALITY_BELOW_THRESHOLD` instead of writing a misleading `document.md`.

To verify the local web runtime against a real URL:

```bash
uv run --no-sync python scripts/web_runtime_smoke.py --url https://example.com/
```

The web smoke is layered. By default it runs the router path plus direct `firecrawl`, `crawl4ai`, `playwright`, and temporary-profile `playwright_profile_worker` checks. Firecrawl is skipped when its API key is not set unless explicitly required:

```bash
uv run --no-sync python scripts/web_runtime_smoke.py --target playwright
uv run --no-sync python scripts/web_runtime_smoke.py --target profile --profile-dir ~/.swallow/browser-profiles/chrome-default
uv run --no-sync python scripts/web_runtime_smoke.py --target firecrawl --require-firecrawl
```

For local browser captures:

```bash
uv run swallow browser-capture ./chatgpt_capture.json
```

Browser capture JSON is schema-validated before conversion:

```json
{
  "platform": "chatgpt",
  "url": "https://chatgpt.com/c/example",
  "title": "Swallow design",
  "captured_at": "2026-05-14T10:30:00-07:00",
  "messages": [
    {"role": "user", "content": "Build the ingest core."},
    {"role": "assistant", "content": "RawStore, Trace, WorkerResult, and Markdown."}
  ],
  "raw_dom": "<html>...</html>"
}
```

Invalid capture payloads fail with `BROWSER_CAPTURE_INVALID` and are recorded in `job.json` and `trace.jsonl`.

For ChatGPT export archives:

```bash
uv run swallow archive ./chatgpt-export.zip
```

For batch regression runs:

```bash
uv run swallow batch "tests/fixtures/**/*"
uv run swallow batch "tests/fixtures/**/*" --workers 4
uv run swallow batch "tests/fixtures/**/*" --json
```

Batch runs write `batch_runs/<batch_id>/summary.json` and `batch_runs/<batch_id>/trace.jsonl`. Each matched input is ingested as an independent job, so one failed file does not stop the rest of the batch.

The fixed local regression corpus lives under `tests/fixtures/`:

```text
tests/fixtures/
  files/      simple text, Markdown, DOCX, XLSX, PPTX, electronic PDF
  ocr/        scanned PDFs and screenshot image
  audio/      short WAV/MP3 plus silent WAV
  video/      short MP4
  web/        static, dynamic, lazy-load, and blocked HTML pages
  browser/    ChatGPT, Claude, and Gemini capture JSON
  archives/   ChatGPT export sample zip
  bad/        corrupted, unsupported, empty, and zip-slip samples
```

Regenerate it after changing fixture expectations:

```bash
uv run --no-sync python scripts/generate_test_fixtures.py
```

For the local service API:

```bash
uv sync --dev --extra service
uv run uvicorn swallow.service.api:app --host 127.0.0.1 --port 8765
```

Versioned service endpoints:

```text
GET  /health
GET  /workers
GET  /v1/jobs
POST /v1/ingest/file
POST /v1/ingest/url
POST /v1/ingest/browser-capture
POST /v1/ingest/archive
POST /v1/jobs/{job_id}/rerun
GET  /v1/jobs/{job_id}
GET  /v1/jobs/{job_id}/document
GET  /v1/jobs/{job_id}/manifest
GET  /v1/jobs/{job_id}/trace
```

The previous unversioned endpoints remain as compatibility aliases and return `Deprecation: true` plus a `Link` header pointing to the `/v1` successor.

To verify the local service API without starting a server:

```bash
uv run --no-sync python scripts/service_runtime_smoke.py
```

To run every local runtime smoke test in sequence:

```bash
uv sync --dev --all-extras
uv run playwright install chromium
uv run --no-sync python scripts/runtime_smoke_all.py
```

Useful options:

```bash
uv run --no-sync python scripts/runtime_smoke_all.py --skip-asr --skip-web
uv run --no-sync python scripts/runtime_smoke_all.py --web-url https://example.com/ --web-token Example
uv run --no-sync python scripts/runtime_smoke_all.py --web-target playwright --web-target profile
uv run --no-sync python scripts/runtime_smoke_all.py --asr-audio ./sample.wav
```

## Final QA And P2 Gates

Default local CI gate:

```bash
uv run --no-sync pytest -q -m "not network and not performance and not heavy"
uv run --no-sync python -m compileall -q src tests scripts
```

P1/P2 focused gates:

```bash
uv run --no-sync pytest -q -m batch
uv run --no-sync pytest -q -m api
uv run --no-sync pytest -q -m security
uv run --no-sync pytest -q -m concurrency
uv run --no-sync pytest -q -m integration
```

Performance thresholds are opt-in:

```bash
$env:RUN_PERFORMANCE_TESTS = "1"
uv run --no-sync pytest -q -m performance
```

The OCR and ASR performance checks remain conditional because they may download or load large runtime models:

```bash
$env:RUN_PERFORMANCE_TESTS = "1"
$env:RUN_OCR_TESTS = "1"
$env:RUN_ASR_TESTS = "1"
uv run --no-sync pytest -q -m performance
```

Firecrawl real API is also conditional:

```bash
$env:RUN_FIRECRAWL_TESTS = "1"
$env:FIRECRAWL_API_KEY = "..."
uv run --no-sync pytest -q -m network
```

Without those environment variables, network and heavy performance checks skip explicitly instead of failing default CI.

GitHub Actions is configured in `.github/workflows/ci.yml`:

- Windows, Linux, and macOS default test matrix
- Docker smoke build/run
- workflow-dispatch performance job
- workflow-dispatch Firecrawl real API job when `FIRECRAWL_API_KEY` is configured

Output is written under the current working directory:

```text
raw_store/
jobs/
batch_runs/
```

Each ingest creates `jobs/<job_id>/job.json` as soon as the job starts. Successful ingests also create:

```text
jobs/<job_id>/job.json
jobs/<job_id>/document.md
jobs/<job_id>/ingest_document.json
jobs/<job_id>/trace.jsonl
```

Failed jobs keep `job.json` and `trace.jsonl`, which is enough for `swallow jobs` and `swallow rerun <job_id>` to recover the raw/source metadata.

`swallow inspect <job_id>` works for both successful and failed jobs. Successful jobs return `ingest_document.json`; failed jobs return `job.json` plus a `trace_tail` so the structured error and final trace events are visible without opening files manually.

Use `swallow inspect <job_id> --raw --artifacts` to include the immutable raw metadata, resolved raw file path, declared artifacts, and discovered files under `jobs/<job_id>/intermediate/`. The service endpoint supports the same enrichment through `GET /v1/jobs/{job_id}?raw=true&artifacts=true`.

Failures are recorded as structured errors in `job.json`, `trace.jsonl`, and API error responses:

```json
{
  "type": "WorkerError",
  "code": "WORKER_NOT_REGISTERED",
  "message": "Worker is not registered: plain_text_worker",
  "retryable": false,
  "fallback_allowed": false
}
```

Worker trace events also include a deterministic `params_hash` and a sanitized `details.params` block derived from that worker's effective config. Sensitive keys such as `api_key`, `password`, `token`, `secret`, `credential`, and `cookie` are redacted before trace writing and hashing; env-var names such as `api_key_env` remain visible for reproducibility.

## Current CLI

```bash
swallow file ./sample.txt
swallow url https://example.com/article
swallow browser-capture ./capture.json
swallow archive ./chatgpt-export.zip
swallow batch "tests/fixtures/**/*"
swallow batch "tests/fixtures/**/*" --workers 4
swallow --config ./swallow.config.yaml file ./sample.txt
swallow workers
swallow doctor
swallow doctor --deep --json
swallow jobs
swallow jobs --json --limit 20
swallow rerun <job_id>
swallow rerun <job_id> --worker paddleocr_worker
swallow inspect <job_id>
swallow inspect <job_id> --raw
swallow inspect <job_id> --artifacts
swallow raw <job_id>
swallow raw <job_id> --json
swallow artifacts <job_id>
swallow artifacts <job_id> --json
swallow open <job_id>
swallow open <job_id> --target raw
swallow open <job_id> --target artifact --artifact 1
swallow open <job_id> --launch
swallow trace <job_id>
swallow trace <job_id> --tail 20
swallow trace <job_id> --json --tail 5
```

## Configuration

Worker settings can be kept in YAML:

```yaml
workers:
  paddleocr:
    dpi: 200
    timeout_seconds: 600

  faster_whisper:
    model: large-v3
    device: auto
    compute_type: default
    ffmpeg_path: null
    timeout_seconds: 1800

  firecrawl:
    api_key_env: FIRECRAWL_API_KEY
    timeout_seconds: 120

  crawl4ai:
    timeout_seconds: 180

  playwright:
    enabled: true
    headless: true
    screenshot: true
    timeout_seconds: 240

  playwright_profile:
    enabled: true
    profile_dir: ~/.swallow/browser-profiles/chrome-default
    headless: false
    screenshot: true
    timeout_seconds: 240

limits:
  max_file_size_bytes: 104857600
  max_pdf_size_bytes: 314572800
  max_audio_video_size_bytes: 2147483648
  max_html_size_bytes: 52428800
```

Set `enabled: false` to disable a worker. Worker-specific keys are injected into that worker's `WorkerInput.metadata`, and built-in workers consume their supported runtime fields such as `dpi`, `model`, `device`, `compute_type`, `api_key_env`, `profile_dir`, `headless`, `screenshot`, and `timeout_seconds`. Defaults stay in place when a config file only overrides one field.

`timeout_seconds` is enforced by the runner as a soft worker timeout and is recorded as `WORKER_TIMEOUT` in `job.json`, `trace.jsonl`, and API error responses. Worker-specific hard timeouts still matter for external tools because Python cannot safely terminate an already-running thread.

Synchronous ingest enforces these size limits before writing to RawStore. Oversized inputs fail with `INPUT_TOO_LARGE_SYNC`; future async job mode can take over this path without weakening RawStore immutability.

## Worker Capabilities

Every built-in worker exposes a machine-readable capability block:

```json
{
  "name": "playwright_worker",
  "version": "0.1.0",
  "capability": {
    "input_mime_types": [],
    "source_types": ["url"],
    "strengths": ["browser_render", "dynamic_page", "screenshot", "lazy_loaded_content"],
    "cost_level": "medium",
    "requires_gpu": false,
    "requires_network": true,
    "supports_batch": false
  }
}
```

Use `swallow workers` or `GET /workers` to inspect enabled workers and their capabilities. Disabled workers are omitted from this list.

## Environment Diagnostics

Use `swallow doctor` before running heavy workers on a new machine:

```bash
swallow doctor
swallow doctor --json
swallow doctor --deep --fail-on-missing
```

The default check is lightweight and reports Python version, enabled worker dependencies, `ffmpeg` resolution, Firecrawl API key status, and local Playwright profile directory status. `--deep` additionally verifies Firecrawl client import shape, Crawl4AI `AsyncWebCrawler` import, normal Chromium launch, and Chromium persistent-context launch for profile-based capture. Warnings, such as a missing `FIRECRAWL_API_KEY` or profile directory, do not fail because fallback routes may still succeed.

## Optional Worker Dependencies

Heavy integrations are optional:

```bash
uv sync --extra markitdown
uv sync --extra ocr
uv sync --extra asr
uv sync --extra service
uv sync --extra web
```

## Boundary

Swallow only does ingest. It does not implement agents, RAG, vector storage, memory, chat, summaries, or knowledge-base management.
