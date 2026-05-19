from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import Any, Literal

from pydantic import BaseModel, Field


IngestStatus = Literal["queued", "running", "success", "partial", "failed"]
BatchStatus = Literal["queued", "running", "success", "partial", "failed", "canceled"]
StorageMode = Literal["persistent", "ephemeral", "memory"]
ReturnContentMode = Literal["none", "preview", "full"]
IngestBackend = Literal["local", "cli", "http", "queue"]
IngestBackendSelection = Literal["auto", "local", "cli", "http", "queue"]


class BackendCapabilities(BaseModel):
    backend: IngestBackend
    inputs: list[str] = Field(default_factory=list)
    supports_batch: bool = False
    supports_cancel: bool = False
    supports_stats: bool = False
    requires_worker: bool = False
    requires_service: bool = False


class IngestResultMeta(BaseModel):
    schema_version: int = 1
    sdk_version: str = Field(default_factory=lambda: read_sdk_version())


class IngestOutputs(BaseModel):
    markdown_path: str | None = None
    document_json_path: str | None = None
    trace_path: str | None = None
    manifest_path: str | None = None
    chunks_path: str | None = None


class IngestDocumentRef(BaseModel):
    id: str | None = None
    title: str | None = None
    source_type: str | None = None
    mime_type: str | None = None
    sha256: str | None = None
    raw_id: str | None = None
    original_filename: str | None = None


class IngestTextPayload(BaseModel):
    text: str
    truncated: bool
    bytes_read: int
    limit_bytes: int


class IngestWarning(BaseModel):
    code: str | None = None
    message: str
    stage: str | None = None
    worker: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class IngestError(BaseModel):
    code: str
    message: str
    stage: str | None = None
    worker: str | None = None
    retryable: bool = False
    recoverable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class IngestJob(BaseModel):
    job_id: str
    status: IngestStatus
    source_type: str
    created_at: str | None = None
    trace_path: str
    document_path: str | None = None
    ingest_document_path: str | None = None
    manifest_path: str | None = None


class IngestResult(BaseModel):
    job_id: str
    status: IngestStatus
    outputs: IngestOutputs
    document: IngestDocumentRef | None = None
    preview: IngestTextPayload | None = None
    content: IngestTextPayload | None = None
    warnings: list[IngestWarning] = Field(default_factory=list)
    errors: list[IngestError] = Field(default_factory=list)
    meta: IngestResultMeta = Field(default_factory=IngestResultMeta)


class IngestBatchJob(BaseModel):
    input: str | None = None
    job_id: str | None = None
    status: BatchStatus
    attempts: int = 0
    document: str | None = None
    manifest: str | None = None
    trace: str | None = None
    error: IngestError | None = None


class IngestBatch(BaseModel):
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
    summary_path: str
    trace_path: str


class IngestBatchResult(IngestBatch):
    jobs: list[IngestBatchJob] = Field(default_factory=list)
    warnings: list[IngestWarning] = Field(default_factory=list)
    errors: list[IngestError] = Field(default_factory=list)


def read_sdk_version() -> str:
    try:
        return version("swallow")
    except PackageNotFoundError:
        return "0+unknown"
