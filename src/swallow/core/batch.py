from __future__ import annotations

import glob
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Sequence

from swallow.core.config import IngestConfig
from swallow.core.errors import error_to_dict
from swallow.core.ids import new_batch_id
from swallow.core.job_store import safe_relative
from swallow.core.models import IngestRunResult
from swallow.core.runner import IngestRunner
from swallow.core.time import now_iso


WILDCARD_CHARS = {"*", "?", "["}


class BatchRunner:
    def __init__(self, store_root: Path | str = ".", *, config: IngestConfig | None = None) -> None:
        self.store_root = Path(store_root)
        self.config = config or IngestConfig()

    def run(self, patterns: Sequence[str], *, max_workers: int = 1) -> dict[str, Any]:
        batch_id = new_batch_id()
        batch_dir = self.store_root / "batch_runs" / batch_id
        batch_dir.mkdir(parents=True, exist_ok=False)
        trace = BatchTraceWriter(batch_dir / "trace.jsonl", batch_id=batch_id)

        started_at = now_iso()
        inputs, warnings = expand_batch_inputs(patterns)
        trace.write("batch_started", status="running", details={"patterns": list(patterns), "total": len(inputs)})

        if not inputs:
            errors = [
                {
                    "type": "InputError",
                    "code": "NO_BATCH_INPUTS",
                    "message": "No batch input files matched.",
                    "retryable": False,
                    "fallback_allowed": False,
                }
            ]
            summary = build_summary(
                batch_id=batch_id,
                jobs=[],
                started_at=started_at,
                finished_at=now_iso(),
                warnings=warnings,
                errors=errors,
            )
            write_summary(batch_dir / "summary.json", summary)
            trace.write("batch_finished", status=summary["status"], details=summary_counts(summary))
            return attach_batch_paths(summary, self.store_root, batch_dir)

        max_workers = max(1, int(max_workers))
        jobs: list[dict[str, Any] | None] = [None] * len(inputs)
        if max_workers == 1:
            for index, input_path in enumerate(inputs):
                jobs[index] = self._run_one(input_path, trace)
        else:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {executor.submit(self._run_one, input_path, trace): index for index, input_path in enumerate(inputs)}
                for future in as_completed(futures):
                    jobs[futures[future]] = future.result()

        finished_at = now_iso()
        summary = build_summary(
            batch_id=batch_id,
            jobs=[job for job in jobs if job is not None],
            started_at=started_at,
            finished_at=finished_at,
            warnings=warnings,
            errors=[],
        )
        write_summary(batch_dir / "summary.json", summary)
        trace.write("batch_finished", status=summary["status"], details=summary_counts(summary))
        return attach_batch_paths(summary, self.store_root, batch_dir)

    def _run_one(self, input_path: Path, trace: "BatchTraceWriter") -> dict[str, Any]:
        input_label = path_label(input_path)
        trace.write("input_started", details={"input": input_label})
        runner = IngestRunner(store_root=self.store_root, config=self.config)
        try:
            result = runner.ingest_file(input_path)
        except Exception as error:
            item = failed_job_item(input_label, error)
            trace.write("input_failed", status="failed", details=item)
            return item

        item = successful_job_item(self.store_root, input_label, result)
        trace.write("input_finished", status=item["status"], details=item)
        return item


class BatchTraceWriter:
    def __init__(self, trace_path: Path, *, batch_id: str) -> None:
        self.trace_path = trace_path
        self.batch_id = batch_id
        self._lock = threading.Lock()
        self.trace_path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, event: str, *, status: str | None = None, details: dict[str, Any] | None = None) -> None:
        record: dict[str, Any] = {
            "event": event,
            "batch_id": self.batch_id,
            "timestamp": now_iso(),
            "details": details or {},
        }
        if status is not None:
            record["status"] = status
        line = json.dumps(record, ensure_ascii=False)
        with self._lock:
            with self.trace_path.open("a", encoding="utf-8") as file:
                file.write(line + "\n")


def expand_batch_inputs(patterns: Sequence[str]) -> tuple[list[Path], list[str]]:
    inputs: list[Path] = []
    warnings: list[str] = []
    seen: set[str] = set()

    for pattern in patterns:
        matches = expand_one_pattern(pattern)
        if not matches:
            warnings.append(f"batch_pattern_matched_no_files:{pattern}")
            continue
        for path in matches:
            key = str(path.resolve(strict=False))
            if key in seen:
                continue
            seen.add(key)
            inputs.append(path)

    return inputs, warnings


def expand_one_pattern(pattern: str) -> list[Path]:
    if has_wildcard(pattern):
        return sorted((Path(match) for match in glob.glob(pattern, recursive=True) if Path(match).is_file()), key=path_sort_key)

    path = Path(pattern)
    if path.is_dir():
        return sorted((child for child in path.rglob("*") if child.is_file()), key=path_sort_key)
    return [path]


def has_wildcard(pattern: str) -> bool:
    return any(char in pattern for char in WILDCARD_CHARS)


def path_sort_key(path: Path) -> str:
    return path.as_posix().lower()


def successful_job_item(store_root: Path, input_label: str, result: IngestRunResult) -> dict[str, Any]:
    manifest = read_manifest(store_root, result.manifest_path)
    status = read_manifest_status(manifest)
    return {
        "input": input_label,
        "job_id": result.job.id,
        "status": status,
        "document": result.document_path,
        "manifest": result.manifest_path,
        "trace": result.trace_path,
    }


def failed_job_item(input_label: str, error: Exception) -> dict[str, Any]:
    return {
        "input": input_label,
        "job_id": getattr(error, "swallow_job_id", None),
        "status": "failed",
        "document": getattr(error, "swallow_document_path", None),
        "manifest": getattr(error, "swallow_manifest_path", None),
        "trace": getattr(error, "swallow_trace_path", None),
        "error": error_to_dict(error),
    }


def read_manifest(store_root: Path, manifest_path: str | None) -> dict[str, Any]:
    if not manifest_path:
        return {}
    path = store_root / manifest_path
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def read_manifest_status(manifest: dict[str, Any]) -> str:
    value = manifest.get("status")
    if value in {"success", "partial", "failed"}:
        return str(value)
    return "success"


def build_summary(
    *,
    batch_id: str,
    jobs: list[dict[str, Any]],
    started_at: str,
    finished_at: str,
    warnings: list[str],
    errors: list[dict[str, Any]],
) -> dict[str, Any]:
    success_count = sum(1 for job in jobs if job.get("status") == "success")
    partial_count = sum(1 for job in jobs if job.get("status") == "partial")
    failed_count = sum(1 for job in jobs if job.get("status") == "failed")
    total = len(jobs)
    if total == 0 or failed_count == total:
        status = "failed"
    elif failed_count or partial_count:
        status = "partial"
    else:
        status = "success"

    return {
        "batch_id": batch_id,
        "status": status,
        "total": total,
        "success": success_count,
        "partial": partial_count,
        "failed": failed_count,
        "jobs": jobs,
        "started_at": started_at,
        "finished_at": finished_at,
        "warnings": warnings,
        "errors": errors,
    }


def summary_counts(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "total": summary["total"],
        "success": summary["success"],
        "partial": summary["partial"],
        "failed": summary["failed"],
    }


def write_summary(path: Path, summary: dict[str, Any]) -> None:
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def attach_batch_paths(summary: dict[str, Any], store_root: Path, batch_dir: Path) -> dict[str, Any]:
    return {
        **summary,
        "summary_path": safe_relative(batch_dir / "summary.json", store_root),
        "trace_path": safe_relative(batch_dir / "trace.jsonl", store_root),
    }


def path_label(path: Path) -> str:
    try:
        return path.relative_to(Path.cwd()).as_posix()
    except ValueError:
        return path.as_posix()
