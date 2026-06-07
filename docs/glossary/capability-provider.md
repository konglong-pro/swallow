# Capability Provider Glossary

## Terms

**Capability Provider**: A thin Swallow adapter layer that registers, describes, validates, executes,
tracks, and exposes artifacts for Swallow ingest capabilities. It is not a generic agent runtime or a
new backend.
_Avoid_: using this term for internal workers, agent orchestration, or cross-project framework code.

**Ingest Capability**: A provider-declared Swallow action that can be submitted as a job, such as file,
URL, browser capture, archive, or batch ingest.
_Avoid_: exposing internal workers as ingest capabilities.

**Provider Operation**: A lifecycle or management method on the provider, such as discovery, doctor,
job lookup, result lookup, artifact retrieval, waiting, or cancellation.
_Avoid_: listing provider operations as submit-capable ingest capabilities.

**Routing Policy**: Agent-visible ingest preference expressed in coarse terms such as automatic
routing, network allowance, OCR allowance, ASR allowance, or fast/quality trade-off.
_Avoid_: exposing concrete worker names as routing controls in provider request schemas.

**Artifact Reference**: A provider-scoped opaque reference to a Swallow artifact. Agents may store and
pass it back to the provider, but must not parse it as a filesystem path or stable cross-machine URI.
_Avoid_: treating `swallow://...` references as direct local paths.

**MCP Adapter**: An external ecosystem adapter for Swallow tools. It wraps the Capability Provider,
but it is not a provider backend.
_Avoid_: routing provider execution through MCP.

**Capability Plan**: A non-mutating provider response that validates a request, checks declared
permissions and profile fit, and describes expected side effects and risks before submission.
_Avoid_: treating a plan as a committed route, job, trace, or artifact-producing execution.

**Provider Profile**: A named provider execution policy that selects allowed source types, backend
order, network allowance, heavy-runtime allowance, and size limits. P1 profiles are built in and may
be overridden through provider construction, not through `swallow.config.yaml`.
_Avoid_: making agents choose raw SDK backends directly.

**Capability Status**: Provider job status values use Swallow's canonical spelling:
`queued`, `running`, `success`, `partial`, `failed`, and `canceled`.
_Avoid_: using `cancelled` in provider schemas.

**Capability Job Lifecycle**: Provider execution is always asynchronous at the interface boundary.
`submit()` returns a job, while `wait()` and `get_result()` return results.
_Avoid_: returning a full capability result directly from `submit()`.

**Capability Batch Lifecycle**: Batch ingest uses provider batch methods and `batch_id` values:
`submit_batch()`, `get_batch()`, `get_batch_result()`, `wait_batch()`, and `cancel_batch()`.
_Avoid_: treating `batch_id` as a `job_id`.

**Operation Summary**: A deterministic provider message about execution status, produced without
summarizing, rewriting, translating, or interpreting document content.
_Avoid_: using `summary` fields for content summaries.

**Artifact Trust Level**: A provider label for how an artifact should be treated. P1 trust levels are
`source`, `candidate`, `evidence`, and `diagnostic`; `document.md` is a `candidate`, even when the job
status is `success`.
_Avoid_: treating ingest success as factual verification.

**P1 Provider Scope**: The first Swallow Capability Provider release supports ingest capabilities for
file, URL, browser capture, archive, and queue-backed batch ingest.
_Avoid_: treating `batch_id` as a `job_id`.

**Provider Cancellation**: A provider operation whose effect depends on the selected profile/backend.
Queue-backed jobs may be canceled or marked cancel-requested; other backends may report
`not_cancelable` or `already_terminal`.
_Avoid_: promising hard cancellation for every backend.

**Provider Schema Source**: P1 provider schemas are defined by Pydantic models in the Swallow package;
JSON Schema files are generated or snapshot artifacts.
_Avoid_: hand-maintaining divergent Python models and JSON schema contracts.

**Provider Backend Selection**: Provider profiles may choose a backend before submission, but a job is
bound to the backend that created it. Provider execution does not silently fall back to another backend
after a job has been submitted.
_Avoid_: post-submit backend fallback that hides provenance.

**Provider Trace Strategy**: P1 Capability Provider results reference Swallow's existing job
`trace.jsonl` and carry provider metadata in structured result/provenance fields. P1 does not create a
separate provider trace artifact.
_Avoid_: expanding the artifact contract with provider traces before there is an audit requirement.

**Artifact View**: A bounded provider response for reading an artifact reference. It may include
metadata, capped text, or capped binary data, but never unbounded artifact content by default.
_Avoid_: exposing direct local paths or full large artifacts unless provider policy explicitly allows it.

**Provider Manifest Source**: `discover()` is the source of truth for provider capability metadata.
Static files such as `swallow.capabilities.json` are snapshots or release artifacts.
_Avoid_: maintaining a separate static manifest that can drift from provider code.

**Provider Policy**: A host-supplied execution policy that grants or denies provider permissions such
as filesystem reads, network access, heavy runtimes, raw artifact reads, full content reads, and local
path exposure.
_Avoid_: treating manifest risk declarations as permission enforcement.

**Async-Compatible Provider**: The provider exposes async methods while reusing existing SDK clients.
P1 may wrap blocking backend calls with thread offloading instead of rewriting transports as native
async implementations.
_Avoid_: treating P1 provider work as an SDK transport rewrite.

**Host Truth**: A trusted revision or source of record owned by a host system outside Swallow.
Provider outputs are never host truth.
_Avoid_: treating Swallow `document.md` or other provider artifacts as trusted host revisions.

**Local-First Provider Default**: Swallow provider runtime should prefer local SDK, CLI, or queue
execution profiles for local trusted use. HTTP is available for service scenarios, but is not the
default local-first path.
_Avoid_: adding a service failure domain for ordinary local ingest.

**Provider Registry**: A provider-owned record of jobs and batches created through the Capability
Provider, including capability, profile, backend, provider version, and store root metadata.
_Avoid_: mixing provider job binding with the SDK facade registry or silently importing unrelated jobs.

**Default Provider Profile**: P1 defaults to `local_isolated`: local-first CLI execution with process
isolation, file/browser-capture/archive support, OCR/ASR allowed, and network URL ingest disabled by
default.
_Avoid_: making HTTP, queue, or network ingest the default local path.

**Capability Availability**: Provider discovery lists supported ingest capabilities and marks whether
each is available under the current profile and policy. A supported capability may be unavailable when
policy requirements such as network access are disabled.
_Avoid_: hiding supported capabilities only because the current profile cannot run them.

## Relationships

- Ingest capabilities are Agent-facing submit actions.
- Provider operations are lifecycle methods around those actions.
- Profiles and policies select execution constraints before a job is submitted.
- Artifact references are resolved only by the provider.
- Host truth is outside Swallow.
