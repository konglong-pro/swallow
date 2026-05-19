# Swallow SDK Final Acceptance

This document defines the release gate for the Swallow SDK. It is intentionally stricter than the current RC state.

The SDK is accepted only when every transport backend exposes the same `IngestClient` contract and returns stable, path-first `IngestResult` objects without weakening the core Swallow artifact contract:

```text
document.md
ingest_document.json
trace.jsonl
```

## Current Status

Current label:

```text
Final SDK acceptance gate: PASS in local verification
Python transport facade accepted
MCP adapter accepted
Release artifact/tag: not cut
```

Current accepted decisions:

- Python SDK public fields use `snake_case`.
- TypeScript-style `camelCase` examples are cross-language mapping references, not the current Python field contract.
- `manifest_path` is allowed as an optional supplemental output. It is not a canonical artifact and must not replace `ingest_document.json`.
- `LocalIngestClient` is file-only. Backend differences are exposed through `BackendCapabilities`.
- MCP is a P2 agent adapter, not a P1 transport backend and not a facade backend.
- Existing Python exception names remain stable. Short names may exist as aliases, but the canonical Python names stay under the `IngestSdkError` hierarchy.
- `ephemeral` and `memory` storage modes are reserved. They must fail clearly until implemented.
- Local and HTTP result equivalence means schema and semantic equivalence, not byte-for-byte artifact or JSON equality.
- MCP has an accepted adapter exception: it is backed directly by `CliIngestClient` rather than the facade `IngestClient`, but it must not import core workers or call `IngestRunner` directly.
- Direct `HttpIngestClient` uses `cwd` as its default allowed root. Callers must pass `allowed_roots` when uploading files outside `cwd`; the facade passes its own allowed roots through.

## Acceptance Layers

### P0 Core SDK Acceptance

P0 must pass before publishing any core SDK release.

Required:

- Stable `IngestResult` schema.
- Stable `IngestJob` schema.
- Stable `IngestClient` contract.
- Async-first job model: `submit -> job_id -> wait/get_result`.
- Persistent-by-default storage.
- Path-first results.
- Optional capped preview.
- Explicit capped full content.
- Chunking fields reserved but disabled by default.
- Hybrid error model.
- No agent framework concepts in core SDK code.

### P1 Transport Acceptance

P1 must prove that multiple backends preserve the same SDK contract.

Required for final release:

- `LocalIngestClient` passes its declared file-only capability contract.
- `HttpIngestClient` passes the shared result contract.
- `IngestClient` facade routes across local, CLI, HTTP, and queue backends without changing result semantics.
- Backend capability gaps are explicit through `BackendCapabilities`.

MCP is not a P1 backend in the Python SDK. It is covered by P2.

### P2 Agent Adapter Acceptance

At least one adapter must pass P2 before the SDK can be called agent-ready.

Required:

- Adapter wraps `IngestResult`; it does not replace it.
- Adapter preserves `job_id`, `markdown_path`, `document_json_path`, and `trace_path`.
- Adapter does not require `manifest_path`.
- Adapter does not require chunks by default.
- Adapter distinguishes SDK invocation errors from failed ingest results.
- Adapter does not call workers directly or change the canonical artifact contract.

Current P2 baseline: MCP adapter.

## Release Gate Checklist

### Done For Current RC

- [x] Python `IngestClient` facade exists.
- [x] `create_ingest_client(...)` exists.
- [x] Local, CLI, HTTP, and queue clients return Pydantic SDK models.
- [x] MCP adapter exists and is backed by `CliIngestClient`.
- [x] `backend="auto"` does pre-submit selection only.
- [x] Facade registry persists job and batch backend routing metadata.
- [x] `manifest_path` is treated as optional supplemental output.
- [x] Reserved storage modes fail clearly.
- [x] `IngestResult.meta.schema_version` and `IngestResult.meta.sdk_version` exist.
- [x] Exception aliases map short acceptance names to the stable Python names.
- [x] SDK path sandbox rejects paths outside allowed roots.
- [x] SDK path sandbox rejects sensitive files such as `.env` and common SSH private key names.
- [x] SDK path sandbox rejects symlink escapes where symlinks are available.
- [x] SDK URL sandbox rejects dangerous schemes, localhost, loopback, link-local, private, multicast, and unspecified IP hosts.
- [x] Contract, transport, local, HTTP, adapter, security, and concurrency pytest markers are registered.
- [x] Schema fixture snapshots cover success, partial, failed, running, and queued `IngestResult` shapes.
- [x] Service URL endpoints enforce the same URL sandbox as SDK clients.
- [x] MCP adapter-boundary test proves the adapter uses an SDK client and does not import core workers.

### Final-Release Hardening Checklist

- [x] Expand SDK-wide sandbox tests to every concrete client, not only facade and queue smoke paths.
- [x] Add facade-level concurrent submit/wait tests.
- [x] Add explicit timeout contract tests for facade and HTTP submit/connect timeout paths.
- [x] Add an automated check that core SDK docs do not describe Swallow as an agent framework.
- [x] Add an adapter-boundary test proving MCP uses `IngestClient` or document the accepted exception if MCP remains backed directly by `CliIngestClient`.
- [x] Add schema fixture snapshots for success, running, queued, failed, and partial result shapes.
- [x] Add a release matrix command set for `contract`, `transport`, `security`, `adapter`, and `concurrency`.
- [x] Decide whether direct service URL endpoints should enforce the same SSRF policy as SDK clients.
- [x] Decide whether direct `HttpIngestClient` should require `allowed_roots` by default or remain opt-in outside the facade.

Open release packaging items:

- [ ] Cut a versioned release artifact or tag.
- [ ] Decide whether to publish a package version immediately or keep this as a verified source-tree acceptance point.

## Required Artifact Contract

Every successful or partial job must be able to locate:

```text
jobs/<job_id>/document.md
jobs/<job_id>/ingest_document.json
jobs/<job_id>/trace.jsonl
```

Required SDK output fields:

- `outputs.markdown_path`
- `outputs.document_json_path`
- `outputs.trace_path`

Optional supplemental fields:

- `outputs.manifest_path`
- `outputs.chunks_path`

`manifest.json` is a job/run manifest. It is not the structured document object and must not replace `ingest_document.json`.

## Required Error Model

Return `IngestResult(status="failed")` or `IngestResult(status="partial")` for job-level ingest failures after a job exists.

Raise SDK exceptions for invocation-level failures:

- `IngestSdkConfigError`
- `IngestSandboxError`
- `IngestTransportError`
- `IngestWaitTimeout`
- `IngestSdkProtocolError`
- `IngestSdkJobNotFound`

Compatibility aliases:

- `IngestConfigError -> IngestSdkConfigError`
- `IngestTimeoutError -> IngestWaitTimeout`
- `IngestProtocolError -> IngestSdkProtocolError`

Do not mark a job failed only because `wait()` timed out.

## Release Commands

Current RC gate:

```bash
uv run python scripts/sdk_release_gate.py
```

Equivalent manual commands:

```bash
uv run pytest -m contract -q
uv run pytest -m transport -q
uv run pytest -m security -q
uv run pytest -m adapter -q
uv run pytest -m concurrency -q
uv run pytest -q
```

Latest local verification:

- `uv run python scripts/sdk_release_gate.py`: passed.
- `uv run pytest -m contract -q`: 24 passed, 1 skipped.
- `uv run pytest -m transport -q`: 33 passed, 1 skipped.
- `uv run pytest -m security -q`: 14 passed, 2 skipped.
- `uv run pytest -m adapter -q`: 9 passed, 2 skipped.
- `uv run pytest -m concurrency -q`: 6 passed, 1 skipped.
- Full default suite through the release gate: 244 passed, 4 skipped.
- Optional MCP real network smoke: 1 passed.

Optional network smoke:

```bash
RUN_SWALLOW_NETWORK_TESTS=1 uv run pytest tests/test_mcp_server.py::test_mcp_url_ingest_real_network_example_dot_com -q
```

The SDK can be marked final only when P0, P1, P2, security, concurrency, and contract gates all pass.
