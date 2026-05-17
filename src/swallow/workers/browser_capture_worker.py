from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from swallow.core.models import WorkerCapability, WorkerInput, WorkerResult
from swallow.normalizers.conversation_to_markdown import browser_capture_to_markdown
from swallow.workers.base import BaseWorker

BROWSER_CAPTURE_INVALID = "BROWSER_CAPTURE_INVALID"


class BrowserCaptureMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: str
    content: Any

    @field_validator("role")
    @classmethod
    def role_must_not_be_empty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("role must not be empty")
        return stripped

    @field_validator("content")
    @classmethod
    def content_must_exist(cls, value: Any) -> Any:
        if value is None:
            raise ValueError("content is required")
        return value


class BrowserCapturePayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    platform: str
    url: str
    title: str
    captured_at: str
    messages: list[BrowserCaptureMessage]
    raw_dom: str | None = None

    @field_validator("platform", "url", "title", "captured_at")
    @classmethod
    def required_string_must_not_be_empty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("value must not be empty")
        return stripped


class BrowserCaptureWorker(BaseWorker):
    name = "browser_capture_worker"
    version = "0.1.0"
    capability = WorkerCapability(
        input_mime_types=["application/json"],
        source_types=["browser_capture"],
        strengths=["login_state_capture", "conversation_markdown", "raw_dom_artifact"],
        cost_level="low",
        requires_gpu=False,
        requires_network=False,
        supports_batch=False,
    )

    def can_handle(self, input: WorkerInput) -> bool:
        return input.source_type == "browser_capture"

    def run(self, input: WorkerInput) -> WorkerResult:
        try:
            capture = load_browser_capture(input.input_path)
        except (json.JSONDecodeError, ValidationError, ValueError) as error:
            return WorkerResult(
                status="failed",
                worker_name=self.name,
                worker_version=self.version,
                errors=[f"browser_capture_invalid: {short_error(error)}"],
                metadata={"error_code": BROWSER_CAPTURE_INVALID},
            )
        except Exception as error:
            return WorkerResult(
                status="failed",
                worker_name=self.name,
                worker_version=self.version,
                errors=[f"browser_capture_load_failed: {type(error).__name__}: {error}"],
            )

        payload = capture.model_dump(mode="python")
        messages = payload["messages"]

        markdown = browser_capture_to_markdown(payload)
        warnings: list[str] = []
        if not messages:
            warnings.append("browser_capture_no_messages")

        artifacts: list[dict[str, Any]] = []
        raw_dom = payload.get("raw_dom")
        job_dir = get_job_dir(input)
        if isinstance(raw_dom, str) and raw_dom.strip() and job_dir is not None:
            raw_dom_path = job_dir / "intermediate" / "browser_capture" / "raw_dom.html"
            raw_dom_path.parent.mkdir(parents=True, exist_ok=True)
            raw_dom_path.write_text(raw_dom, encoding="utf-8")
            artifacts.append({"type": "raw_dom", "path": raw_dom_path.relative_to(job_dir).as_posix()})

        return WorkerResult(
            status="success" if messages else "partial",
            worker_name=self.name,
            worker_version=self.version,
            markdown=markdown,
            title=str(payload.get("title") or Path(input.metadata.get("original_filename", input.input_path)).stem),
            artifacts=artifacts,
            metadata={
                "platform": payload.get("platform"),
                "url": payload.get("url"),
                "captured_at": payload.get("captured_at"),
                "message_count": len(messages),
            },
            warnings=warnings,
        )


def load_browser_capture(path: str | Path) -> BrowserCapturePayload:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("browser capture payload must be a JSON object")
    return BrowserCapturePayload.model_validate(payload)


def short_error(error: Exception, *, max_length: int = 200) -> str:
    message = str(error).replace("\n", " ").strip()
    if len(message) <= max_length:
        return message
    return message[: max_length - 3] + "..."


def get_job_dir(input: WorkerInput) -> Path | None:
    value = input.metadata.get("job_dir")
    if not value:
        return None
    return Path(value)
