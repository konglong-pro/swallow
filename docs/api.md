# API And Tool Surfaces

## CLI

The CLI command is `swallow`.

Common ingest commands:

```bash
swallow file ./sample.txt
swallow url https://example.com/article
swallow browser-capture ./capture.json
swallow archive ./chatgpt-export.zip
swallow batch "tests/fixtures/**/*"
swallow batch "tests/fixtures/**/*" --workers 4
```

Store and config:

```bash
swallow --store ./store --config ./swallow.config.yaml file ./sample.txt
```

Inspection commands:

```bash
swallow workers
swallow doctor
swallow doctor --deep --json
swallow jobs
swallow jobs --json --limit 20
swallow rerun <job_id>
swallow rerun <job_id> --worker paddleocr_worker
swallow inspect <job_id>
swallow inspect <job_id> --raw --artifacts
swallow raw <job_id> --json
swallow artifacts <job_id> --json
swallow open <job_id>
swallow open <job_id> --target raw
swallow open <job_id> --target artifact --artifact 1
swallow open <job_id> --launch
swallow trace <job_id> --tail 20
swallow trace <job_id> --json --tail 5
```

Machine-readable ingest output:

- `--json` emits one terminal `IngestResult` JSON object.
- `--jsonl` emits a `job_submitted` event followed by a terminal result event.
- Exit code `0` means success.
- Exit code `2` means partial.
- Exit code `1` means failed ingest result.

Doctor and worker capability commands:

```bash
swallow workers
swallow doctor
swallow doctor --json
swallow doctor --deep --fail-on-missing
```

## Queue CLI

```bash
swallow queue worker
swallow queue worker --once
swallow queue stats
swallow queue cancel <job_id_or_batch_id>
```

The queue backend stores local durable state under `queue/queue.sqlite3`. Parallelism comes from
running multiple workers.

## Service API

Run locally:

```bash
uv run uvicorn swallow.service.api:app --host 127.0.0.1 --port 8765
```

Versioned routes:

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
GET  /v1/jobs/{job_id}/result
GET  /v1/jobs/{job_id}/document
GET  /v1/jobs/{job_id}/manifest
GET  /v1/jobs/{job_id}/trace
```

Current HTTP v1 contract:

- `POST /v1/ingest/*` defaults to `wait=false` and returns an `IngestJob`.
- `POST /v1/ingest/*?wait=true` returns an `IngestResult`.
- `GET /v1/jobs/{job_id}` returns an `IngestJob`.
- `GET /v1/jobs/{job_id}/result?content=none|preview|full` returns an `IngestResult`.
- Legacy `/ingest/*` routes remain as compatibility aliases and include deprecation headers.

## Python SDK

Main imports:

```python
from swallow.sdk import IngestClient, create_ingest_client
```

Facade example:

```python
client = IngestClient(store_root=".", backend="auto")
job = client.submit_file("sample.txt")
result = client.wait(job.job_id, content="preview")
```

Convenience example:

```python
result = client.file("sample.txt", wait=True)
```

Concrete clients:

- `LocalIngestClient`: in-process file ingest for lightweight local validation.
- `CliIngestClient`: subprocess wrapper around the CLI for file, URL, browser capture, and archive ingest.
- `HttpIngestClient`: HTTP service client, including explicit `start_local(...)`.
- `QueueIngestClient`: SQLite-backed queue client and batch support.
- `IngestClient`: hybrid facade over local, CLI, HTTP, and queue backends.

Facade auto routing:

- file -> local
- URL/browser capture/archive -> CLI
- batch/admin -> queue

## Capability Provider

The Capability Provider is a thin adapter over Swallow ingest for agent runtimes. It exposes
provider-shaped discovery, planning, submission, result, artifact, doctor, and cancellation methods.
It is not an agent framework and does not make provider outputs host truth.

Main import:

```python
from swallow.capability import SwallowCapabilityProvider
```

Default local-first usage:

```python
provider = SwallowCapabilityProvider(store_root=".")
manifest = provider.discover()
plan = await provider.plan({
    "capability_id": "swallow.ingest.file",
    "input": {"source_path": "sample.txt"},
})
job = await provider.submit({
    "capability_id": "swallow.ingest.file",
    "input": {"source_path": "sample.txt"},
})
result = await provider.wait(job.job_id, {"content_mode": "preview"})
```

P1 Agent-facing ingest capabilities:

- `swallow.ingest.file`
- `swallow.ingest.url`
- `swallow.ingest.browser_capture`
- `swallow.ingest.archive`
- `swallow.ingest.batch`

Provider operations such as `discover`, `doctor`, `plan`, `get_job`, `get_result`, `get_artifact`,
`wait`, and `cancel` are lifecycle methods, not submit-capable ingest capabilities. Batch uses
separate lifecycle methods: `submit_batch`, `get_batch`, `get_batch_result`, `wait_batch`, and
`cancel_batch`. Batch IDs are not job IDs.

Default profile:

- `local_isolated`
- backend order: CLI
- URL/network ingest unavailable unless policy/profile enables it
- batch unavailable; `swallow.ingest.batch` is available through the `heavy_queue` profile
- HTTP is not the local-first default path

Snapshot artifacts:

```bash
uv run --no-sync python scripts/generate_capability_artifacts.py
uv run --no-sync python scripts/generate_capability_artifacts.py --check
```

The script updates `schemas/*.schema.json` and `swallow.capabilities.json` from provider/Pydantic
models. These files are snapshots, not independent sources of truth.

## MCP Adapter

Run:

```bash
swallow mcp serve
```

Tools:

- `swallow_capabilities_list`
- `swallow_doctor`
- `swallow_ingest_file`
- `swallow_ingest_url`
- `swallow_ingest_browser_capture`
- `swallow_ingest_archive`
- `swallow_ingest_batch`
- `swallow_get_job`
- `swallow_get_batch`
- `swallow_get_result`
- `swallow_get_batch_result`
- `swallow_wait_for_result`
- `swallow_wait_for_batch`
- `swallow_get_artifact`
- `swallow_cancel_job`
- `swallow_cancel_batch`

Rules:

- MCP exposes tools only, not resources or prompts.
- It is backed by `SwallowCapabilityProvider`; MCP remains an adapter, not a provider backend.
- Existing single-job tools preserve the SDK path-first result contract for compatibility.
- Batch tools return provider-shaped batch models.
- Default result content is `preview`.
- URL ingest is disabled unless `--enable-url-ingest` is passed.
- When URL ingest is enabled, only absolute `http` and `https` URLs are allowed.
