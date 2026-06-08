# Development

## Prerequisites

- Python 3.11+
- `uv`

The project uses `pyproject.toml`, `uv.lock`, and the `swallow` console script.

## Base Setup

```bash
uv sync --dev
uv run swallow --help
```

On Windows machines with slow access to the default PyPI CDN, optional heavy runtime installs may use
a mirror, for example:

```bash
uv sync --dev --extra ocr --default-index https://mirrors.cloud.tencent.com/pypi/simple
```

## Optional Worker Dependencies

Install only the extras needed for the ingest path:

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

Notes:

- The MarkItDown extra pins `markitdown[docx,pdf,pptx,xlsx]==0.1.5`.
- The OCR extra currently pins `paddlepaddle==2.6.2` with PaddleOCR.
- The ASR worker resolves `ffmpeg` through `ffmpeg_path` / `SWALLOW_FFMPEG_PATH`, then `ffmpeg` on
  `PATH`, then the `imageio-ffmpeg` binary included by the ASR extra.
- Firecrawl requires normal API configuration, usually `FIRECRAWL_API_KEY`. The environment variable
  name can be changed with `workers.firecrawl.api_key_env`.
- Login-required URL capture through `playwright_profile_worker` uses a local persistent profile
  directory. Run with `headless: false` the first time if local login is required.

## Configuration

Swallow looks for config in this order:

1. `--config` passed to the CLI.
2. `SWALLOW_CONFIG` for the service process.
3. `swallow.config.yaml` in the current directory.

Start from `swallow.config.example.yaml`.

Worker settings live under `workers`, and runtime size limits live under `limits`. Worker-specific
keys are passed into worker metadata and consumed by built-in workers.

Minimal worker config shape:

```yaml
workers:
  paddleocr:
    dpi: 200
    timeout_seconds: 600
  faster_whisper:
    model: large-v3
    device: auto
    compute_type: default
  firecrawl:
    api_key_env: FIRECRAWL_API_KEY
  playwright:
    enabled: true
    headless: true
  playwright_profile:
    enabled: true
    profile_dir: ~/.swallow/browser-profiles/chrome-default
    headless: false
  gemini_share:
    enabled: true
    headless: true
  wechat_article:
    enabled: true
    headless: true
  youtube_transcript:
    enabled: true
  youtube_asr:
    enabled: false
    allow_media_download: false
    max_media_size_bytes: 524288000

limits:
  max_file_size_bytes: 104857600
  max_pdf_size_bytes: 314572800
  max_audio_video_size_bytes: 2147483648
  max_html_size_bytes: 52428800
```

Set `enabled: false` to disable a worker. `timeout_seconds` is enforced by the runner as a soft worker
timeout and is recorded as `WORKER_TIMEOUT` in job and trace outputs.

## Common Runtime Commands

File ingest:

```bash
uv run swallow file ./sample.txt
uv run swallow file ./report.docx
uv run swallow file ./scanned.pdf
```

URL ingest:

```bash
uv run swallow url https://example.com/article
```

Platform URL ingest uses the same command and SDK URL surface. Core routing selects internal platform
workers for recognized share, article, and transcript URLs; callers should not select platform
worker names through SDK/provider APIs.

`youtube_asr` is an opt-in policy gate in this phase. It stays disabled by default and refuses to run
unless media download consent is explicit.

Browser capture and export archive ingest:

```bash
uv run swallow browser-capture ./chatgpt_capture.json
uv run swallow archive ./chatgpt-export.zip
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

Batch ingest:

```bash
uv run swallow batch "tests/fixtures/**/*"
uv run swallow batch "tests/fixtures/**/*" --workers 4
uv run swallow batch "tests/fixtures/**/*" --json
```

## Fixtures

The fixed local regression corpus lives under `tests/fixtures/`:

```text
tests/fixtures/
  files/
  ocr/
  audio/
  video/
  web/
  browser/
  archives/
  bad/
```

Regenerate fixtures after changing fixture expectations:

```bash
uv run --no-sync python scripts/generate_test_fixtures.py
```

## Runtime Smoke Scripts

```bash
uv run --no-sync python scripts/ocr_runtime_smoke.py
uv run --no-sync python scripts/asr_runtime_smoke.py
uv run --no-sync python scripts/web_runtime_smoke.py --url https://example.com/
uv run --no-sync python scripts/service_runtime_smoke.py
uv run --no-sync python scripts/runtime_smoke_all.py
```

Useful runtime smoke options:

```bash
uv run --no-sync python scripts/runtime_smoke_all.py --skip-asr --skip-web
uv run --no-sync python scripts/runtime_smoke_all.py --web-url https://example.com/ --web-token Example
uv run --no-sync python scripts/runtime_smoke_all.py --web-target playwright --web-target profile
uv run --no-sync python scripts/runtime_smoke_all.py --asr-audio ./sample.wav
```

Platform URL live checks are manual evidence only. Do not add repository tests that depend on live
platform pages; use deterministic fixtures for parser regression coverage.

## Diagnostics

Use `swallow doctor` before running heavy workers on a new machine:

```bash
uv run swallow doctor
uv run swallow doctor --json
uv run swallow doctor --deep --fail-on-missing
```

The lightweight doctor reports Python version, enabled worker dependencies, `ffmpeg` resolution,
Firecrawl API key status, and local Playwright profile status. `--deep` may start local runtimes such
as Chromium.
