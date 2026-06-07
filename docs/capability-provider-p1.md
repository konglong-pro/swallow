---
doc_type: phase_plan
phase_id: capability-provider-p1
title: Swallow Capability Provider P1
status: completed
canonical: true
related_contracts:
  - docs/contracts/ingest-contract.md
related_adrs:
  - docs/adr/0001-swallow-capability-provider-boundary.md
release_gate: uv run python scripts/sdk_release_gate.py
---

# Swallow Capability Provider P1

## Goal

Package Swallow's existing ingest SDK capability behind a thin Capability Provider adapter so an
agent runtime can discover, validate, submit, track, wait for, and read artifacts through one stable
provider-shaped interface.

The provider is not a new agent runtime, not a new backend model, and not a host truth system. It is
an adapter over Swallow ingest.

Terminology lives in `docs/glossary/capability-provider.md`.

## Confirmed Boundaries

- Provider outputs are never host truth. Swallow `document.md` is a conversion candidate.
- Agents request ingest capabilities. They do not choose local, CLI, HTTP, queue, or worker backends.
- MCP is an external adapter backed by the provider. It is not a P1 provider backend.
- HTTP is not the local-first default path. Local trusted use defaults to local CLI isolation.
- Internal workers are not Agent-facing capabilities.
- Provider `summary` fields are deterministic operation summaries, not content summaries.
- `submit()` always returns a job. Results are retrieved through `wait()` or `get_result()`.

## P1 Scope

P1 supports these Agent-facing ingest capabilities:

- `swallow.ingest.file`
- `swallow.ingest.url`
- `swallow.ingest.browser_capture`
- `swallow.ingest.archive`
- `swallow.ingest.batch`

Provider operations are not listed as submit-capable ingest capabilities:

- `discover`
- `doctor`
- `plan`
- `submit`
- `get_job`
- `wait`
- `get_result`
- `get_artifact`
- `cancel`
- `submit_batch`
- `get_batch`
- `get_batch_result`
- `wait_batch`
- `cancel_batch`

Single-job capability methods use job IDs. Batch methods use batch IDs and must not treat a batch ID
as a job ID.

## Non-Goals

- Do not create a public cross-repository capability framework.
- Do not add new transports or rewrite SDK transports as native async.
- Do not expose internal workers such as `paddleocr_worker` or `markitdown_worker` as capabilities.
- Do not route provider execution through MCP.
- Do not make HTTP the default local provider path.
- Do not add provider-owned trace artifacts in P1.
- Do not add summarization, rewriting, embeddings, RAG, memory, chat, or host revision semantics.

## Proposed Package Shape

Implementation should stay inside the Swallow package:

```text
src/swallow/capability/
  __init__.py
  provider.py
  manifest.py
  schemas.py
  errors.py
  artifacts.py
  conformance.py
  adapters/
    local.py
    cli.py
    http.py
    queue.py
```

Generated or snapshot artifacts:

```text
schemas/capability-manifest.schema.json
schemas/capability-result.schema.json
schemas/artifact-ref.schema.json
swallow.capabilities.json
```

Pydantic models in `src/swallow/capability/schemas.py` are the schema source of truth. JSON Schema and
`swallow.capabilities.json` are snapshots or release artifacts generated from code.

## Provider Interface

P1 exposes async-compatible Python methods:

```text
provider_id: str
provider_version: str

discover() -> ProviderManifest
doctor(input=None) -> DoctorResult
plan(request) -> CapabilityPlan
submit(request) -> CapabilityJob
get_job(job_id) -> CapabilityJob
wait(job_id, options=None) -> CapabilityResult
get_result(job_id, options=None) -> CapabilityResult
get_artifact(ref, options=None) -> ArtifactView
cancel(job_id) -> CancelResult
submit_batch(request) -> CapabilityBatch
get_batch(batch_id) -> CapabilityBatch
get_batch_result(batch_id) -> CapabilityBatchResult
wait_batch(batch_id, options=None) -> CapabilityBatchResult
cancel_batch(batch_id) -> CancelResult
```

The methods may reuse existing synchronous SDK clients. Blocking backend calls may be wrapped with
thread offloading; P1 is async-compatible, not a transport rewrite.

## Manifest Rules

`discover()` is the source of truth for provider capability metadata. Static manifest files are
snapshots.

The manifest lists supported capabilities and marks whether each is available under the current
profile and policy. For example, `swallow.ingest.url` is supported but unavailable under the default
profile because network ingest is disabled.

Capability manifests describe:

- `capability_id`
- title and description
- input and output schemas
- artifact kinds
- side effects
- trust output
- async lifecycle
- risk flags
- evidence requirements
- current profile availability
- required policy grants

Worker capabilities remain internal and may only appear in doctor output, plan explanations, or
provenance as evidence. They are not Agent-facing capability IDs.

## Profiles

P1 profiles are built in and can be overridden through provider construction. They are not added to
`swallow.config.yaml` in P1.

Default profile: `local_isolated`.

```text
local_light:
  backend_order: [local]
  allowed_source_types: [file]
  allow_network: false
  allow_ocr: false
  allow_asr: false
  max_file_size_mb: 20

local_isolated:
  backend_order: [cli]
  allowed_source_types: [file, browser_capture, archive]
  allow_network: false
  allow_ocr: true
  allow_asr: true
  content_mode_default: preview

service:
  backend_order: [http]
  requires_service_url: true
  allow_network: configurable

heavy_queue:
  backend_order: [queue]
  allowed_source_types: [file, url, archive, browser_capture, batch]
  allow_ocr: true
  allow_asr: true
  allow_network: true
```

Backend selection is pre-submit only. Once a backend creates a job, that job is bound to the selected
backend and profile. Provider execution must not silently fall back to another backend after
submission.

## Policy And Permissions

Provider policy is supplied by the host runtime. The provider enforces policy; it does not present an
interactive approval UI.

P1 policy grants:

```text
allow_filesystem_read
allow_network
allow_heavy_runtime
allow_raw_artifact_read
allow_full_content
expose_local_paths
```

`plan()` returns required permissions and approvals. `submit()` must check profile and policy before
creating a Swallow job. Permission failure is a provider invocation error, not an ingest job failure.

## Plan Semantics

`plan()` is non-mutating but may inspect within declared permissions.

Allowed:

- validate request schema
- resolve capability and profile availability
- check allowed roots and local file metadata
- perform lightweight MIME or extension inspection
- validate URL syntax and URL sandbox policy
- return expected side effects and risk

Not allowed:

- write `raw_store/` or `jobs/`
- create a job id
- start OCR, ASR, Playwright, or other heavy runtimes
- download URL content
- create trace or artifact output

Worker chain information in a plan is predicted or possible, not committed.

## Result Semantics

`CapabilityResult` wraps Swallow results without changing the ingest artifact contract.

Canonical status values:

```text
queued
running
success
partial
failed
canceled
```

Result output rules:

- `outputs.primary` points to `document.md` when present.
- `document.md` artifact kind is `markdown_candidate`.
- `document.md` trust level is `candidate` even when status is `success`.
- trace and manifest artifacts are evidence.
- `summary` is an operation summary only.
- errors and warnings are mapped from Swallow SDK/service/CLI results without hiding failed ingest as
  success.

Provenance should include:

- provider id and version
- capability id
- selected profile
- selected backend
- input SHA-256 when available
- worker chain when it can be read or inferred from Swallow manifest/trace
- trace refs

## Artifact References And Views

Artifact references are provider-scoped opaque references. Agents may store and pass them back to the
provider, but must not parse them as filesystem paths.

P1 reference shape:

```text
swallow://jobs/{job_id}/document.md
swallow://jobs/{job_id}/ingest_document.json
swallow://jobs/{job_id}/manifest.json
swallow://jobs/{job_id}/trace.jsonl
swallow://raw/{raw_id}/original
swallow://raw/{raw_id}/original.meta.json
swallow://batch_runs/{batch_id}/summary.json
swallow://batch_runs/{batch_id}/trace.jsonl
```

`get_artifact()` returns a bounded `ArtifactView`:

- default `max_bytes`: 65536
- text artifacts may return capped text
- binary artifacts return metadata and optional capped encoded data only when explicitly allowed
- raw original reads require `allow_raw_artifact_read`
- full content reads require `allow_full_content`
- local paths are hidden unless `expose_local_paths` is true

The URI path is not a direct local filesystem path.

## Provider Registry

Provider-created jobs are recorded independently from the SDK facade registry:

```text
sdk/capability_provider_registry.jsonl
```

Minimum record:

```json
{
  "kind": "job",
  "job_id": "ing_...",
  "provider_id": "swallow",
  "provider_version": "0.1.0",
  "capability_id": "swallow.ingest.file",
  "profile": "local_isolated",
  "backend": "cli",
  "store_root": ".",
  "created_at": "2026-06-07T00:00:00Z"
}
```

Batch records use the same registry file with `kind: "batch"` and `batch_id`. The provider uses
these records to bind later `get_batch`, `get_batch_result`, `wait_batch`, and `cancel_batch` calls
to the selected profile/backend.

Registry failure is a provider invocation error. The provider does not silently import unrelated
Swallow jobs in P1.

## Cancellation

`cancel(job_id)` is provider-level and backend-dependent:

- queue backend may cancel pending jobs or mark running jobs cancel-requested
- local, CLI, and HTTP may return `not_cancelable` or `already_terminal`
- cancellation must include reason and observed job status
- the provider must not promise hard cancellation for every backend

`cancel_batch(batch_id)` is separate from `cancel(job_id)` and is currently queue-backed.

## Doctor

`doctor()` reports provider readiness at two levels:

- provider/profile readiness: policy, allowed roots, selected profile, backend availability
- Swallow runtime readiness: worker dependencies and existing `swallow doctor` data where available

Doctor output may mention internal worker capability because it is diagnostic, not an Agent-facing
capability list.

## Conformance Tests

P1 should include conformance tests that verify:

- `discover()` validates against provider manifest models
- static schema/manifest snapshots match generated output
- default profile is `local_isolated`
- URL ingest is supported but unavailable by default
- Agent-facing capabilities do not include internal worker names
- `submit()` returns a job, not a result
- permission denial happens before Swallow job creation
- job registry binds job id to capability/profile/backend
- `get_result()` maps Swallow outputs to provider artifacts and trust levels
- `get_artifact()` enforces bounded reads and local path hiding
- `cancel()` reports backend-dependent results honestly
- batch operations keep `batch_id` separate from `job_id`
- provider output is never represented as host truth

## Implementation Order

1. Add Pydantic provider schema models and generated schema snapshot tests.
2. Add provider manifest generation and `swallow.capabilities.json` snapshot test.
3. Add built-in profiles and policy validation.
4. Add provider registry.
5. Add backend adapters over existing Local, CLI, HTTP, and queue SDK clients.
6. Add `SwallowCapabilityProvider` operations for P1 single-job capabilities.
7. Add artifact reference parsing and bounded artifact views.
8. Add conformance tests.
9. Add provider batch operations over the existing queue SDK.
10. Refactor MCP adapter to wrap the provider while preserving compatibility tool shapes.

## Validation

For implementation work, run targeted tests plus the SDK release gate when provider behavior touches
SDK semantics:

```bash
uv run pytest -m contract -q
uv run pytest -m transport -q
uv run pytest -m security -q
uv run pytest -m adapter -q
uv run pytest -m concurrency -q
uv run python scripts/sdk_release_gate.py
```

For documentation-only edits, run the documentation gate described in `docs/active/current.md` and
verify links/YAML.

## Acceptance Criteria

- Agent-facing manifest exposes P1 ingest capabilities only.
- Internal workers are not registered as capabilities.
- Default provider profile is local-first CLI isolation.
- HTTP is available only through explicit service profile selection.
- MCP is not a provider backend.
- Provider results expose conversion candidates and evidence, not host truth.
- Artifact refs are opaque and artifact reads are bounded.
- Provider policy is enforced before job creation.
- Provider registry preserves job-to-backend binding.
- Provider registry preserves batch-to-backend binding.
- Static schema and manifest artifacts are generated from provider code, not hand-maintained.
- MCP is backed by the provider but remains an adapter.

## Deferred

- Provider-owned trace artifact.
- Cross-repository public capability framework.
- `swallow.config.yaml` provider profile configuration.
- Remote HTTP authentication and authorization policy.
