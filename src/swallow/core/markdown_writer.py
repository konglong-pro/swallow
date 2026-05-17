from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from swallow.core.models import IngestDocument


class MarkdownWriter:
    def write(self, doc: IngestDocument, job_dir: Path) -> tuple[Path, Path]:
        document_path = job_dir / "document.md"
        ingest_document_path = job_dir / "ingest_document.json"

        document_path.write_text(render_document_markdown(doc), encoding="utf-8")
        ingest_document_path.write_text(
            json.dumps(doc.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return document_path, ingest_document_path


def render_document_markdown(doc: IngestDocument) -> str:
    front_matter = build_front_matter(doc)
    front_matter_yaml = yaml.safe_dump(
        front_matter,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    ).strip()
    body = doc.content.markdown.rstrip()
    return (
        f"---\n{front_matter_yaml}\n---\n\n"
        f"# {doc.content.title}\n\n"
        "<!-- ingest:source\n"
        f"raw_id: {doc.raw_id}\n"
        f"sha256: {doc.source.sha256}\n"
        "-->\n\n"
        f"{body}\n"
    )


def build_front_matter(doc: IngestDocument) -> dict[str, Any]:
    return {
        "ingest_document_id": doc.id,
        "job_id": doc.job_id,
        "raw_id": doc.raw_id,
        "source_type": doc.source.source_type,
        "source_subtype": doc.source.source_subtype,
        "source_url": doc.source.source_url,
        "original_filename": doc.source.original_filename,
        "mime_type": doc.source.mime_type,
        "sha256": doc.source.sha256,
        "created_at": doc.created_at,
        "ingested_at": doc.ingested_at,
        "primary_worker": doc.provenance.primary_worker,
        "worker_chain": doc.provenance.worker_chain,
        "quality_score": doc.quality.score,
        "warnings": doc.quality.warnings,
    }
