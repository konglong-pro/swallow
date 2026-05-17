from __future__ import annotations

from pathlib import Path

from swallow.core.ids import new_doc_id
from swallow.core.models import (
    IngestContent,
    IngestDocument,
    JobRecord,
    Provenance,
    QualityReport,
    RawRecord,
    SourceInfo,
    WorkerResult,
)
from swallow.core.time import now_iso


def build_ingest_document(
    *,
    job: JobRecord,
    raw: RawRecord,
    result: WorkerResult,
    quality: QualityReport,
    worker_chain: list[str],
) -> IngestDocument:
    markdown = result.markdown or ""
    title = result.title or infer_title(markdown, raw.original_filename)
    return IngestDocument(
        id=new_doc_id(),
        job_id=job.id,
        raw_id=raw.raw_id,
        source=SourceInfo(
            source_type=job.source_type,
            source_subtype=infer_source_subtype(raw),
            source_url=job.source_url,
            original_filename=raw.original_filename,
            mime_type=raw.mime_type,
            sha256=raw.sha256,
        ),
        content=IngestContent(
            title=title,
            markdown=markdown,
            language=result.language,
        ),
        provenance=Provenance(
            primary_worker=result.worker_name,
            worker_version=result.worker_version,
            worker_chain=worker_chain,
            artifacts=result.artifacts,
            trace_path=job.trace_path,
        ),
        quality=quality,
        created_at=raw.created_at,
        ingested_at=now_iso(),
    )


def infer_title(markdown: str, filename: str) -> str:
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip() or Path(filename).stem
    return Path(filename).stem or "document"


def infer_source_subtype(raw: RawRecord) -> str | None:
    suffix = Path(raw.original_filename).suffix.lower().lstrip(".")
    if suffix:
        return suffix
    if raw.mime_type and "/" in raw.mime_type:
        return raw.mime_type.split("/", 1)[1]
    return None
