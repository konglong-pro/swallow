from __future__ import annotations

from pathlib import Path
from typing import Any

from swallow.core.models import WorkerCapability, WorkerInput, WorkerResult
from swallow.detectors.file_type import MARKITDOWN_MIME_TYPES, is_markitdown_candidate
from swallow.workers.base import BaseWorker


class MarkItDownWorker(BaseWorker):
    name = "markitdown_worker"
    version = "0.1.0"
    capability = WorkerCapability(
        input_mime_types=sorted(MARKITDOWN_MIME_TYPES),
        source_types=["file"],
        strengths=["fast", "general_file_conversion", "office_documents", "extractable_pdf"],
        cost_level="low",
        requires_gpu=False,
        requires_network=False,
        supports_batch=True,
    )

    def can_handle(self, input: WorkerInput) -> bool:
        return input.source_type == "file" and is_markitdown_candidate(input.input_path, input.mime_type)

    def run(self, input: WorkerInput) -> WorkerResult:
        try:
            from markitdown import MarkItDown
        except ImportError:
            return WorkerResult(
                status="failed",
                worker_name=self.name,
                worker_version=self.version,
                errors=["missing_optional_dependency: install with `uv sync --extra markitdown`"],
                metadata={"converter": "markitdown", "input_mime_type": input.mime_type},
            )

        path = Path(input.input_path)
        try:
            converted = MarkItDown().convert(str(path))
        except Exception as error:
            return WorkerResult(
                status="failed",
                worker_name=self.name,
                worker_version=self.version,
                errors=[f"markitdown_convert_failed: {type(error).__name__}: {error}"],
                metadata={"converter": "markitdown", "input_mime_type": input.mime_type},
            )

        markdown = extract_markdown(converted)
        warnings: list[str] = []
        if not markdown.strip():
            warnings.append("markitdown_output_empty")
            status = "failed"
        elif len(markdown.strip()) < 100:
            warnings.append("markitdown_output_short")
            status = "partial"
        else:
            status = "success"

        return WorkerResult(
            status=status,
            worker_name=self.name,
            worker_version=self.version,
            markdown=markdown,
            title=infer_title(converted, input),
            metadata={
                "converter": "markitdown",
                "input_mime_type": input.mime_type,
            },
            warnings=warnings,
            errors=[] if markdown.strip() else ["markitdown produced empty markdown"],
        )


def extract_markdown(converted: Any) -> str:
    for attr in ("markdown", "text_content", "text"):
        value = getattr(converted, attr, None)
        if isinstance(value, str):
            return value
    if isinstance(converted, str):
        return converted
    return str(converted) if converted is not None else ""


def infer_title(converted: Any, input: WorkerInput) -> str:
    title = getattr(converted, "title", None)
    if isinstance(title, str) and title.strip():
        return title.strip()
    return Path(input.metadata.get("original_filename", input.input_path)).stem
