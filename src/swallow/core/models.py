from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class RawRecord(BaseModel):
    raw_id: str
    sha256: str
    path: str
    original_filename: str
    mime_type: str | None = None
    size_bytes: int
    created_at: str


class JobRecord(BaseModel):
    id: str
    raw_id: str
    source_type: str
    source_url: str | None = None
    job_dir: str
    trace_path: str
    created_at: str


class JobMetadata(BaseModel):
    job_id: str
    raw_id: str
    sha256: str
    source_type: str
    source_url: str | None = None
    original_filename: str
    mime_type: str | None = None
    status: Literal["queued", "running", "success", "failed"] = "running"
    job_dir: str
    trace_path: str
    document_path: str | None = None
    ingest_document_path: str | None = None
    manifest_path: str | None = None
    ingest_document_id: str | None = None
    error: dict[str, Any] | None = None
    created_at: str
    finished_at: str | None = None


class JobSummary(BaseModel):
    job_id: str
    status: Literal["queued", "running", "success", "failed", "unknown"]
    raw_id: str | None = None
    source_type: str | None = None
    source_url: str | None = None
    original_filename: str | None = None
    primary_worker: str | None = None
    quality_score: float | None = None
    document_path: str | None = None
    ingest_document_path: str | None = None
    manifest_path: str | None = None
    trace_path: str
    created_at: str | None = None


class WorkerInput(BaseModel):
    job_id: str
    raw_id: str
    input_path: str
    mime_type: str | None = None
    source_type: str
    source_url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkerResult(BaseModel):
    status: Literal["success", "partial", "failed"]
    worker_name: str
    worker_version: str
    markdown: str | None = None
    title: str | None = None
    language: str | None = None
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    confidence: float | None = None


class WorkerCapability(BaseModel):
    input_mime_types: list[str] = Field(default_factory=list)
    source_types: list[str] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    cost_level: Literal["low", "medium", "high"] = "low"
    requires_gpu: bool = False
    requires_network: bool = False
    supports_batch: bool = False


class SourceInfo(BaseModel):
    source_type: str
    source_subtype: str | None = None
    source_url: str | None = None
    original_filename: str
    mime_type: str | None = None
    sha256: str


class IngestContent(BaseModel):
    title: str
    markdown: str
    language: str | None = None


class Provenance(BaseModel):
    primary_worker: str
    worker_version: str
    worker_chain: list[str] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    trace_path: str


class QualityReport(BaseModel):
    score: float
    warnings: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    confidence: float | None = None


class IngestDocument(BaseModel):
    id: str
    job_id: str
    raw_id: str
    source: SourceInfo
    content: IngestContent
    provenance: Provenance
    quality: QualityReport
    created_at: str
    ingested_at: str


class IngestRunResult(BaseModel):
    raw: RawRecord
    job: JobRecord
    document: IngestDocument
    document_path: str
    ingest_document_path: str
    trace_path: str
    manifest_path: str | None = None
