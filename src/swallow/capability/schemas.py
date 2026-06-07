from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


ProviderBackend = Literal["local", "cli", "http", "queue"]
CapabilityStatus = Literal["queued", "running", "success", "partial", "failed", "canceled"]
BatchStatus = Literal["queued", "running", "success", "partial", "failed", "canceled"]
CapabilityId = Literal[
    "swallow.ingest.file",
    "swallow.ingest.url",
    "swallow.ingest.browser_capture",
    "swallow.ingest.archive",
    "swallow.ingest.batch",
]
ContentMode = Literal["none", "preview", "full"]
CapabilityPlanStatus = Literal["ready", "requires_permission", "unavailable"]
TrustLevel = Literal["source", "candidate", "evidence", "diagnostic"]
ArtifactRole = Literal["primary", "evidence", "source", "intermediate", "diagnostic"]
CancelStatus = Literal["canceled", "cancel_requested", "not_cancelable", "already_terminal", "failed"]


class CapabilityBaseModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class AsyncLifecycle(CapabilityBaseModel):
    required: bool = True
    lifecycle: list[str] = Field(
        default_factory=lambda: ["submit", "get_job", "wait", "get_result", "get_artifact"]
    )


class CapabilityRisk(CapabilityBaseModel):
    filesystem_read: bool = False
    network: bool = False
    heavy_runtime_possible: bool = False
    requires_approval: bool = True


class CapabilityManifest(CapabilityBaseModel):
    capability_id: CapabilityId
    provider_id: str
    version: str
    title: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    artifact_kinds: list[str]
    side_effects: list[str]
    trust_output: Literal["conversion_candidate"] = "conversion_candidate"
    async_: AsyncLifecycle = Field(default_factory=AsyncLifecycle, alias="async")
    risk: CapabilityRisk
    evidence_required: list[str]
    supported: bool = True
    available: bool
    unavailable_reason: str | None = None
    requires_policy: list[str] = Field(default_factory=list)


class ProviderManifest(CapabilityBaseModel):
    provider_id: str
    provider_version: str
    profile: str
    capabilities: list[CapabilityManifest]
    operations: list[str]


class ProviderPolicy(CapabilityBaseModel):
    allow_filesystem_read: bool = True
    allow_network: bool = False
    allow_heavy_runtime: bool = False
    allow_raw_artifact_read: bool = False
    allow_full_content: bool = False
    expose_local_paths: bool = False


class CapabilityRequest(CapabilityBaseModel):
    capability_id: CapabilityId
    input: dict[str, Any] = Field(default_factory=dict)


class CapabilityPlan(CapabilityBaseModel):
    provider_id: str
    capability_id: CapabilityId
    status: CapabilityPlanStatus
    profile: str
    backend_candidates: list[ProviderBackend] = Field(default_factory=list)
    required_permissions: list[str] = Field(default_factory=list)
    required_approvals: list[str] = Field(default_factory=list)
    side_effects: list[str] = Field(default_factory=list)
    artifact_kinds: list[str] = Field(default_factory=list)
    risk: CapabilityRisk
    message: str


class CapabilityJob(CapabilityBaseModel):
    provider_id: str
    capability_id: CapabilityId
    job_id: str
    status: CapabilityStatus
    created_at: str | None = None
    profile: str
    backend: ProviderBackend
    trace_refs: list["TraceRef"] = Field(default_factory=list)


class OperationSummary(CapabilityBaseModel):
    title: str
    message: str


class CapabilityOutput(CapabilityBaseModel):
    artifact_ref: str
    kind: str
    content_mode: ContentMode = "none"
    preview: str | None = None
    truncated: bool = False


class CapabilityOutputs(CapabilityBaseModel):
    primary: CapabilityOutput | None = None


class CapabilityWarning(CapabilityBaseModel):
    code: str | None = None
    message: str
    severity: Literal["warning"] = "warning"
    stage: str | None = None
    worker: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class CapabilityError(CapabilityBaseModel):
    code: str
    message: str
    severity: Literal["error"] = "error"
    stage: str | None = None
    worker: str | None = None
    retryable: bool = False
    recoverable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class ArtifactDescriptor(CapabilityBaseModel):
    artifact_ref: str
    kind: str
    role: ArtifactRole
    trust_level: TrustLevel
    media_type: str | None = None


class TraceRef(CapabilityBaseModel):
    system: Literal["swallow"] = "swallow"
    job_id: str
    artifact_ref: str


class BatchTraceRef(CapabilityBaseModel):
    system: Literal["swallow"] = "swallow"
    batch_id: str
    artifact_ref: str


class CapabilityProvenance(CapabilityBaseModel):
    provider_version: str
    profile: str
    backend: ProviderBackend
    input_sha256: str | None = None
    worker_chain: list[str] = Field(default_factory=list)


class CapabilityResult(CapabilityBaseModel):
    provider_id: str
    capability_id: CapabilityId
    job_id: str
    status: CapabilityStatus
    started_at: str | None = None
    finished_at: str | None = None
    summary: OperationSummary
    outputs: CapabilityOutputs = Field(default_factory=CapabilityOutputs)
    artifacts: list[ArtifactDescriptor] = Field(default_factory=list)
    warnings: list[CapabilityWarning] = Field(default_factory=list)
    errors: list[CapabilityError] = Field(default_factory=list)
    trace_refs: list[TraceRef] = Field(default_factory=list)
    provenance: CapabilityProvenance


class CapabilityBatchJob(CapabilityBaseModel):
    input: str | None = None
    job_id: str | None = None
    status: BatchStatus
    attempts: int = 0
    document: str | None = None
    manifest: str | None = None
    trace: str | None = None
    error: CapabilityError | None = None


class CapabilityBatch(CapabilityBaseModel):
    provider_id: str
    capability_id: Literal["swallow.ingest.batch"] = "swallow.ingest.batch"
    batch_id: str
    status: BatchStatus
    total: int
    queued: int = 0
    running: int = 0
    success: int = 0
    partial: int = 0
    failed: int = 0
    canceled: int = 0
    job_ids: list[str] = Field(default_factory=list)
    created_at: str | None = None
    profile: str
    backend: ProviderBackend
    summary_ref: str
    trace_refs: list[BatchTraceRef] = Field(default_factory=list)


class CapabilityBatchResult(CapabilityBatch):
    summary: OperationSummary
    artifacts: list[ArtifactDescriptor] = Field(default_factory=list)
    jobs: list[CapabilityBatchJob] = Field(default_factory=list)
    warnings: list[CapabilityWarning] = Field(default_factory=list)
    errors: list[CapabilityError] = Field(default_factory=list)


class ArtifactRef(CapabilityBaseModel):
    artifact_ref: str


class ArtifactViewOptions(CapabilityBaseModel):
    content_mode: ContentMode = "preview"
    max_bytes: int = Field(default=65536, ge=0)
    include_binary: bool = False


class ArtifactView(CapabilityBaseModel):
    artifact_ref: str
    kind: str
    role: ArtifactRole
    trust_level: TrustLevel
    media_type: str | None = None
    size_bytes: int | None = None
    content_mode: ContentMode = "none"
    text: str | None = None
    data_base64: str | None = None
    truncated: bool = False
    local_path: str | None = None


class WaitOptions(CapabilityBaseModel):
    timeout: float | None = None
    poll_interval: float = Field(default=0.1, gt=0)
    content_mode: ContentMode = "preview"


class BatchWaitOptions(CapabilityBaseModel):
    timeout: float | None = None
    poll_interval: float = Field(default=0.2, gt=0)


class ResultOptions(CapabilityBaseModel):
    content_mode: ContentMode = "preview"


class DoctorInput(CapabilityBaseModel):
    deep: bool = False


class DoctorCheck(CapabilityBaseModel):
    name: str
    status: Literal["ok", "warning", "missing"]
    detail: str
    install_hint: str | None = None


class DoctorResult(CapabilityBaseModel):
    provider_id: str
    provider_version: str
    profile: str
    status: Literal["ok", "warning", "missing"]
    checks: list[DoctorCheck] = Field(default_factory=list)


class CancelResult(CapabilityBaseModel):
    provider_id: str
    job_id: str | None = None
    batch_id: str | None = None
    status: CancelStatus
    job_status: CapabilityStatus | None = None
    batch_status: BatchStatus | None = None
    reason: str


ProviderManifest.model_rebuild()
CapabilityJob.model_rebuild()
CapabilityBatch.model_rebuild()
CapabilityBatchResult.model_rebuild()
