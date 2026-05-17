from __future__ import annotations

from pathlib import Path
from typing import Any

from swallow.core.errors import SecurityError
from swallow.core.models import WorkerInput

UNSAFE_ARTIFACT_PATH = "UNSAFE_ARTIFACT_PATH"


def get_url(input: WorkerInput) -> str | None:
    return input.source_url or input.metadata.get("source_url")


def get_job_dir(input: WorkerInput) -> Path | None:
    value = input.metadata.get("job_dir")
    if not value:
        return None
    return Path(value)


def save_text_artifact(job_dir: Path, relative_path: str, content: str) -> dict[str, Any]:
    artifact_path = safe_artifact_path(job_dir, relative_path)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(content, encoding="utf-8")
    return {"path": artifact_path.relative_to(job_dir).as_posix()}


def safe_artifact_path(job_dir: Path, relative_path: str) -> Path:
    if not relative_path or Path(relative_path).is_absolute():
        raise SecurityError(
            f"Blocked unsafe artifact path: {relative_path}",
            code=UNSAFE_ARTIFACT_PATH,
        )
    candidate = job_dir / relative_path
    resolved_job_dir = job_dir.resolve()
    resolved_candidate = candidate.resolve()
    try:
        resolved_candidate.relative_to(resolved_job_dir)
    except ValueError as error:
        raise SecurityError(
            f"Blocked unsafe artifact path: {relative_path}",
            code=UNSAFE_ARTIFACT_PATH,
        ) from error
    return candidate


def read_value(result: Any, key: str, default: Any = None) -> Any:
    if isinstance(result, dict):
        return result.get(key, default)
    return getattr(result, key, default)


def metadata_from_result(result: Any) -> dict[str, Any]:
    metadata = read_value(result, "metadata", {})
    return metadata if isinstance(metadata, dict) else {}


def short_error(error: Exception, *, max_length: int = 200) -> str:
    message = str(error).replace("\n", " ").strip()
    if len(message) <= max_length:
        return message
    return message[: max_length - 3] + "..."
