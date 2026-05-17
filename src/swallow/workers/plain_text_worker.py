from __future__ import annotations

from pathlib import Path

from swallow.core.models import WorkerCapability, WorkerInput, WorkerResult
from swallow.workers.base import BaseWorker


class PlainTextWorker(BaseWorker):
    name = "plain_text_worker"
    version = "0.1.0"
    capability = WorkerCapability(
        input_mime_types=["text/plain", "text/markdown"],
        source_types=["file"],
        strengths=["fast", "plain_text_passthrough"],
        cost_level="low",
        requires_gpu=False,
        requires_network=False,
        supports_batch=True,
    )

    def can_handle(self, input: WorkerInput) -> bool:
        return input.source_type == "file" and (
            input.mime_type is None or input.mime_type.startswith("text/")
        )

    def run(self, input: WorkerInput) -> WorkerResult:
        path = Path(input.input_path)
        try:
            markdown = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as error:
            return WorkerResult(
                status="failed",
                worker_name=self.name,
                worker_version=self.version,
                errors=[f"utf8_decode_failed: {error}"],
            )

        return WorkerResult(
            status="success",
            worker_name=self.name,
            worker_version=self.version,
            markdown=markdown,
            title=Path(input.metadata.get("original_filename", path.name)).stem,
            metadata={
                "converter": "plain_text",
                "input_mime_type": input.mime_type,
            },
        )
