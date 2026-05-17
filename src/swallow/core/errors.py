from __future__ import annotations

from typing import Any


class IngestError(Exception):
    code = "INGEST_ERROR"
    retryable = False
    fallback_allowed = False

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        retryable: bool | None = None,
        fallback_allowed: bool | None = None,
    ) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code
        if retryable is not None:
            self.retryable = retryable
        if fallback_allowed is not None:
            self.fallback_allowed = fallback_allowed


class InputError(IngestError):
    code = "INPUT_ERROR"


class WorkerError(IngestError):
    code = "WORKER_ERROR"
    fallback_allowed = True


class WorkerTimeoutError(WorkerError):
    code = "WORKER_TIMEOUT"
    retryable = True
    fallback_allowed = True


class QualityError(IngestError):
    code = "QUALITY_ERROR"
    fallback_allowed = True


class SystemResourceError(IngestError):
    code = "SYSTEM_RESOURCE_ERROR"
    retryable = True


class SecurityError(IngestError):
    code = "SECURITY_ERROR"
    retryable = False
    fallback_allowed = False


def error_to_dict(error: Exception) -> dict[str, Any]:
    if isinstance(error, IngestError):
        return {
            "type": type(error).__name__,
            "code": error.code,
            "message": str(error),
            "retryable": error.retryable,
            "fallback_allowed": error.fallback_allowed,
        }
    return {
        "type": type(error).__name__,
        "code": type(error).__name__.upper(),
        "message": str(error),
        "retryable": False,
        "fallback_allowed": False,
    }


def format_error(error: Exception) -> str:
    if isinstance(error, IngestError):
        return f"{error.code}: {error}"
    return str(error)
