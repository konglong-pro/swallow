from __future__ import annotations

from swallow.core.errors import InputError, WorkerError, WorkerTimeoutError, error_to_dict, format_error


def test_error_to_dict_serializes_ingest_error_fields():
    error = WorkerError("worker failed", code="WORKER_FAILED", fallback_allowed=False)

    assert error_to_dict(error) == {
        "type": "WorkerError",
        "code": "WORKER_FAILED",
        "message": "worker failed",
        "retryable": False,
        "fallback_allowed": False,
    }
    assert format_error(error) == "WORKER_FAILED: worker failed"


def test_error_to_dict_serializes_plain_exception():
    error = ValueError("bad input")

    assert error_to_dict(error) == {
        "type": "ValueError",
        "code": "VALUEERROR",
        "message": "bad input",
        "retryable": False,
        "fallback_allowed": False,
    }


def test_input_error_defaults_are_non_retryable_without_fallback():
    error = InputError("not found", code="JOB_NOT_FOUND")

    payload = error_to_dict(error)
    assert payload["code"] == "JOB_NOT_FOUND"
    assert payload["retryable"] is False
    assert payload["fallback_allowed"] is False


def test_worker_timeout_error_defaults_are_retryable_with_fallback():
    error = WorkerTimeoutError("timeout")

    payload = error_to_dict(error)
    assert payload["code"] == "WORKER_TIMEOUT"
    assert payload["retryable"] is True
    assert payload["fallback_allowed"] is True
