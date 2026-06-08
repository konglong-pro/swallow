from __future__ import annotations

import hashlib
import json
import mimetypes
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from swallow.core.builder import build_ingest_document
from swallow.core.config import IngestConfig
from swallow.core.errors import (
    IngestError,
    InputError,
    QualityError,
    SystemResourceError,
    WorkerError,
    WorkerTimeoutError,
    error_to_dict,
)
from swallow.core.job_store import JobStore
from swallow.core.markdown_writer import MarkdownWriter
from swallow.core.models import IngestRunResult, JobMetadata, JobRecord, QualityReport, RawRecord, WorkerInput, WorkerResult
from swallow.core.quality import QUALITY_CHECKER_NAME, QUALITY_CHECKER_VERSION, check_quality
from swallow.core.raw_store import RawStore
from swallow.core.registry import WorkerRegistry, default_registry
from swallow.core.router import Router
from swallow.core.trace import TraceWriter
from swallow.core.time import now_iso
from swallow.detectors.url_classifier import classify_url
from swallow.detectors.file_type import is_audio_video_file, is_pdf_file
from swallow.normalizers.markdown_normalizer import (
    MARKDOWN_NORMALIZER_NAME,
    MARKDOWN_NORMALIZER_VERSION,
    normalize_markdown_body,
)


@dataclass(frozen=True)
class PreparedIngestJob:
    raw: RawRecord
    job: JobRecord


class IngestRunner:
    def __init__(
        self,
        store_root: Path | str = ".",
        *,
        config: IngestConfig | None = None,
        registry: WorkerRegistry | None = None,
        router: Router | None = None,
        writer: MarkdownWriter | None = None,
    ) -> None:
        self.store_root = Path(store_root)
        self.config = config or IngestConfig()
        self.raw_store = RawStore(self.store_root)
        self.job_store = JobStore(self.store_root)
        self.registry = registry or default_registry(self.config)
        self.router = router or Router(
            youtube_asr_enabled=self.config.worker_enabled("youtube_asr_worker"),
        )
        self.writer = writer or MarkdownWriter()

    def ingest_file(self, input_path: Path | str) -> IngestRunResult:
        return self.run_prepared_job(self.prepare_file_job(input_path))

    def prepare_file_job(self, input_path: Path | str) -> PreparedIngestJob:
        self._enforce_sync_size_limit(input_path)
        raw = self.raw_store.save_immutable(input_path)
        job = self.job_store.create_job(raw, source_type="file")
        return PreparedIngestJob(raw=raw, job=job)

    def run_prepared_job(
        self,
        prepared: PreparedIngestJob,
        *,
        plan_override: list[str] | None = None,
    ) -> IngestRunResult:
        return self._run_prepared_ingest(prepared.raw, prepared.job, plan_override=plan_override)

    def ingest_url(self, url: str) -> IngestRunResult:
        return self.run_prepared_job(self.prepare_url_job(url))

    def prepare_url_job(self, url: str) -> PreparedIngestJob:
        raw = self.raw_store.save_url_reference(url)
        job = self.job_store.create_job(raw, source_type="url", source_url=url)
        return PreparedIngestJob(raw=raw, job=job)

    def ingest_browser_capture(self, input_path: Path | str) -> IngestRunResult:
        return self.run_prepared_job(self.prepare_browser_capture_job(input_path))

    def prepare_browser_capture_job(self, input_path: Path | str) -> PreparedIngestJob:
        self._enforce_sync_size_limit(input_path)
        raw = self.raw_store.save_immutable(input_path)
        job = self.job_store.create_job(raw, source_type="browser_capture")
        return PreparedIngestJob(raw=raw, job=job)

    def ingest_archive(self, input_path: Path | str) -> IngestRunResult:
        return self.run_prepared_job(self.prepare_archive_job(input_path))

    def prepare_archive_job(self, input_path: Path | str) -> PreparedIngestJob:
        self._enforce_sync_size_limit(input_path)
        raw = self.raw_store.save_immutable(input_path)
        job = self.job_store.create_job(raw, source_type="export_archive")
        return PreparedIngestJob(raw=raw, job=job)

    def rerun_job(self, job_id: str, *, worker_name: str | None = None) -> IngestRunResult:
        metadata = self.job_store.read_job_metadata(job_id)
        original_doc_path = self.store_root / "jobs" / job_id / "ingest_document.json"
        if metadata is None and not original_doc_path.exists():
            raise InputError(f"Job not found: {job_id}", code="JOB_NOT_FOUND")

        if metadata is None:
            metadata = self._metadata_from_ingest_document(job_id, original_doc_path)

        raw_meta_path = self.store_root / "raw_store" / metadata.sha256 / "original.meta.json"
        if not raw_meta_path.exists():
            raise InputError(f"Raw metadata not found for job {job_id}: {raw_meta_path}", code="RAW_METADATA_NOT_FOUND")

        raw = RawRecord.model_validate_json(raw_meta_path.read_text(encoding="utf-8"))
        source_type = metadata.source_type
        source_url = metadata.source_url

        plan_override = None
        if worker_name:
            if worker_name in {QUALITY_CHECKER_NAME, MARKDOWN_NORMALIZER_NAME}:
                raise InputError(f"Cannot rerun with internal pipeline step: {worker_name}", code="INVALID_RERUN_WORKER")
            if not self.registry.has(worker_name):
                raise WorkerError(
                    f"Worker is not registered: {worker_name}",
                    code="WORKER_NOT_REGISTERED",
                    fallback_allowed=False,
                )
            self._validate_forced_worker(worker_name, raw, source_type=source_type, source_url=source_url)
            plan_override = [worker_name, QUALITY_CHECKER_NAME, MARKDOWN_NORMALIZER_NAME]

        return self._run_ingest(raw, source_type=source_type, source_url=source_url, plan_override=plan_override)

    def _run_ingest(
        self,
        raw: RawRecord,
        *,
        source_type: str,
        source_url: str | None = None,
        plan_override: list[str] | None = None,
    ) -> IngestRunResult:
        job = self.job_store.create_job(raw, source_type=source_type, source_url=source_url)
        return self._run_prepared_ingest(raw, job, plan_override=plan_override)

    def _run_prepared_ingest(
        self,
        raw: RawRecord,
        job: JobRecord,
        *,
        plan_override: list[str] | None = None,
    ) -> IngestRunResult:
        trace = TraceWriter(self.job_store.resolve_trace_path(job))
        plan: list[str] = []
        manifest_workers: list[dict[str, Any]] = []
        current_result: WorkerResult | None = None
        quality = QualityReport(score=0.0, warnings=[], metrics={})

        try:
            trace.job_started(job, raw)
            trace.raw_saved(job, raw)

            worker_input = WorkerInput(
                job_id=job.id,
                raw_id=raw.raw_id,
                input_path=str(self.raw_store.resolve_path(raw)),
                mime_type=raw.mime_type,
                source_type=job.source_type,
                source_url=job.source_url,
                metadata={
                    "original_filename": raw.original_filename,
                    "job_dir": str(self.job_store.resolve_job_dir(job)),
                    "job_dir_rel": job.job_dir,
                },
            )
            if job.source_type == "url" and job.source_url:
                classification = classify_url(job.source_url)
                trace.url_classified(
                    job,
                    kind=classification.kind.value,
                    platform=classification.platform,
                    normalized_url=classification.normalized_url,
                    confidence=classification.confidence,
                    needs_redirect_resolution=classification.needs_redirect_resolution,
                    needs_browser_profile=classification.needs_browser_profile,
                )
                worker_input.metadata.update(
                    {
                        "url_kind": classification.kind.value,
                        "platform": classification.platform,
                        "normalized_url": classification.normalized_url,
                        "needs_redirect_resolution": classification.needs_redirect_resolution,
                        "needs_browser_profile": classification.needs_browser_profile,
                    }
                )

            plan = plan_override or self.router.route(worker_input)
            trace.route_selected(job, plan)
            self.job_store.write_manifest(job, raw, status="running", route=plan)

            worker_chain: list[str] = []
            skip_until_fallback = False

            for index, step in enumerate(plan):
                is_fallback_step = step.startswith("fallback:")
                if skip_until_fallback and not is_fallback_step:
                    continue
                if is_fallback_step:
                    skip_until_fallback = False
                    if quality.metrics and quality.score >= 0.75:
                        continue
                    step = step.removeprefix("fallback:")
                    trace.fallback_selected(
                        job,
                        worker_name=step,
                        reason="quality_below_accept_threshold" if quality.metrics else "previous_worker_failed_or_unchecked",
                        quality_score=quality.score if quality.metrics else None,
                        warnings=quality.warnings,
                    )

                if step == QUALITY_CHECKER_NAME:
                    if current_result is None:
                        raise IngestError("quality_checker requires a previous worker result")
                    quality = self._run_quality_step(trace, job, current_result, worker_input)
                    current_result.warnings = quality.warnings
                    worker_chain.append(f"{QUALITY_CHECKER_NAME}@{QUALITY_CHECKER_VERSION}")
                    manifest_workers.append(
                        {
                            "worker": QUALITY_CHECKER_NAME,
                            "worker_version": QUALITY_CHECKER_VERSION,
                            "status": quality_status(quality),
                            "quality_score": quality.score,
                        }
                    )
                    continue

                if step == MARKDOWN_NORMALIZER_NAME:
                    if current_result is None:
                        raise IngestError("markdown_normalizer requires a previous worker result")
                    current_result = self._run_normalizer_step(trace, job, current_result)
                    worker_chain.append(f"{MARKDOWN_NORMALIZER_NAME}@{MARKDOWN_NORMALIZER_VERSION}")
                    manifest_workers.append(
                        {
                            "worker": MARKDOWN_NORMALIZER_NAME,
                            "worker_version": MARKDOWN_NORMALIZER_VERSION,
                            "status": current_result.status,
                        }
                    )
                    continue

                if not self.registry.has(step):
                    raise WorkerError(f"Worker is not registered: {step}", code="WORKER_NOT_REGISTERED", fallback_allowed=False)

                worker = self.registry.get(step)
                step_input = self._input_for_worker(worker_input, worker.name)
                params = sanitized_worker_params(step_input.metadata.get("worker_config", {}))
                params_digest = hash_params(params) if params else None
                started_at = now_iso()
                trace.worker_started(
                    job,
                    worker.name,
                    worker.version,
                    raw_id=raw.raw_id,
                    input_path=step_input.input_path,
                    started_at=started_at,
                    params_hash=params_digest,
                    params=params,
                )
                started = time.perf_counter()
                try:
                    result = run_worker_with_timeout(
                        worker,
                        step_input,
                        timeout_seconds=read_timeout_seconds(step_input.metadata.get("timeout_seconds")),
                    )
                    duration_ms = elapsed_ms(started)
                    finished_at = now_iso()
                    output_hash = hash_text(result.markdown)
                    trace.worker_finished(
                        job,
                        result,
                        duration_ms=duration_ms,
                        output_hash=output_hash,
                        raw_id=raw.raw_id,
                        input_path=step_input.input_path,
                        started_at=started_at,
                        finished_at=finished_at,
                        params_hash=params_digest,
                        params=params,
                    )
                    if isinstance(result.metadata.get("security_error"), dict):
                        trace.security_error(
                            job,
                            worker_name=result.worker_name,
                            raw_id=raw.raw_id,
                            details=result.metadata["security_error"],
                        )
                    manifest_workers.append(
                        {
                            "worker": worker.name,
                            "worker_version": worker.version,
                            "status": result.status,
                            "duration_ms": duration_ms,
                            "output_hash": output_hash,
                            "params_hash": params_digest,
                        }
                    )
                except Exception as error:
                    duration_ms = elapsed_ms(started)
                    finished_at = now_iso()
                    trace.worker_failed(
                        job,
                        worker.name,
                        worker.version,
                        error,
                        duration_ms=duration_ms,
                        raw_id=raw.raw_id,
                        input_path=step_input.input_path,
                        started_at=started_at,
                        finished_at=finished_at,
                        params_hash=params_digest,
                        params=params,
                    )
                    manifest_workers.append(
                        {
                            "worker": worker.name,
                            "worker_version": worker.version,
                            "status": "failed",
                            "duration_ms": duration_ms,
                            "params_hash": params_digest,
                            "error": error_to_dict(error),
                        }
                    )
                    if has_later_fallback(plan, index) and bool(getattr(error, "fallback_allowed", False)):
                        worker_chain.append(f"{worker.name}@{worker.version}")
                        quality = QualityReport(
                            score=0.0,
                            warnings=[worker_exception_warning(error)],
                            metrics={"worker_exception": type(error).__name__},
                        )
                        current_result = None
                        skip_until_fallback = True
                        continue
                    raise

                current_result = result
                worker_chain.append(f"{worker.name}@{worker.version}")
                if is_fallback_step and result.status != "failed":
                    quality = QualityReport(score=0.0, warnings=[], metrics={})
                if result.status == "failed" and not has_later_fallback(plan, index):
                    raise WorkerError(
                        "; ".join(result.errors) or f"{worker.name} failed",
                        code=worker_result_error_code(result),
                        fallback_allowed=False,
                    )

            if current_result is None:
                raise IngestError("No worker result was produced")
            if current_result.status == "failed":
                raise WorkerError(
                    "; ".join(current_result.errors) or f"{current_result.worker_name} failed",
                    code=worker_result_error_code(current_result),
                    fallback_allowed=False,
                )

            if not quality.metrics:
                quality = check_quality(current_result, worker_input)
                trace.quality_checked(job, quality)
            if quality.score < 0.45:
                warning_text = ", ".join(quality.warnings) if quality.warnings else "no warnings"
                raise QualityError(
                    f"Final ingest quality below failure threshold: score={quality.score:.2f}; warnings={warning_text}",
                    code="QUALITY_BELOW_THRESHOLD",
                    fallback_allowed=False,
                )

            doc = build_ingest_document(
                job=job,
                raw=raw,
                result=current_result,
                quality=quality,
                worker_chain=worker_chain,
            )
            trace.document_built(job, doc)

            job_dir = self.job_store.resolve_job_dir(job)
            document_path, ingest_document_path = self.writer.write(doc, job_dir)
            document_rel = document_path.relative_to(self.store_root).as_posix()
            ingest_document_rel = ingest_document_path.relative_to(self.store_root).as_posix()
            self.job_store.mark_job_finished(
                job,
                doc,
                document_path=document_rel,
                ingest_document_path=ingest_document_rel,
            )
            manifest_status = "partial" if quality.score < 0.75 or current_result.status == "partial" else "success"
            manifest_rel = f"{job.job_dir}/manifest.json"
            self.job_store.write_manifest(
                job,
                raw,
                status=manifest_status,
                route=plan,
                workers=manifest_workers,
                outputs={
                    "markdown": document_rel,
                    "json": ingest_document_rel,
                    "trace": job.trace_path,
                    "manifest": manifest_rel,
                },
                warnings=quality.warnings,
                errors=[],
            )
            trace.document_written(job, document_rel, ingest_document_rel)
            trace.job_finished(job, doc)

            return IngestRunResult(
                raw=raw,
                job=job,
                document=doc,
                document_path=document_rel,
                ingest_document_path=ingest_document_rel,
                trace_path=job.trace_path,
                manifest_path=manifest_rel,
            )
        except Exception as error:
            self.job_store.mark_job_failed(job, error)
            attach_job_context_to_error(error, job)
            self.job_store.write_manifest(
                job,
                raw,
                status="failed",
                route=plan,
                workers=manifest_workers,
                outputs={"trace": job.trace_path, "manifest": f"{job.job_dir}/manifest.json"},
                warnings=current_result.warnings if current_result is not None else [],
                errors=[error_to_dict(error)],
            )
            trace.job_failed(job, error)
            raise

    def _metadata_from_ingest_document(self, job_id: str, path: Path) -> JobMetadata:
        payload = json.loads(path.read_text(encoding="utf-8"))
        source = payload.get("source")
        if not isinstance(source, dict):
            raise InputError(f"Job has no source metadata: {job_id}", code="JOB_SOURCE_MISSING")

        sha256 = source.get("sha256")
        if not isinstance(sha256, str) or not sha256:
            raise InputError(f"Job has no raw sha256: {job_id}", code="JOB_RAW_SHA_MISSING")

        return JobMetadata(
            job_id=str(payload.get("job_id") or job_id),
            raw_id=str(payload.get("raw_id") or ""),
            sha256=sha256,
            source_type=str(source.get("source_type") or "file"),
            source_url=str(source["source_url"]) if source.get("source_url") is not None else None,
            original_filename=str(source.get("original_filename") or "original.bin"),
            mime_type=str(source["mime_type"]) if source.get("mime_type") is not None else None,
            status="success",
            job_dir=f"jobs/{job_id}",
            trace_path=f"jobs/{job_id}/trace.jsonl",
            document_path=f"jobs/{job_id}/document.md",
            ingest_document_path=f"jobs/{job_id}/ingest_document.json",
            ingest_document_id=str(payload.get("id")) if payload.get("id") is not None else None,
            created_at=str(payload.get("created_at") or payload.get("ingested_at") or ""),
            finished_at=str(payload.get("ingested_at")) if payload.get("ingested_at") is not None else None,
        )

    def _input_for_worker(self, input: WorkerInput, worker_name: str) -> WorkerInput:
        params = self.config.worker_params(worker_name)
        if not params:
            return input
        metadata = dict(input.metadata)
        metadata.update(params)
        metadata["worker_config"] = params
        return input.model_copy(update={"metadata": metadata})

    def _validate_forced_worker(
        self,
        worker_name: str,
        raw: RawRecord,
        *,
        source_type: str,
        source_url: str | None = None,
    ) -> None:
        worker = self.registry.get(worker_name)
        preview_input = WorkerInput(
            job_id="ing_preview",
            raw_id=raw.raw_id,
            input_path=str(self.raw_store.resolve_path(raw)),
            mime_type=raw.mime_type,
            source_type=source_type,
            source_url=source_url,
            metadata={"original_filename": raw.original_filename},
        )
        preview_input = self._input_for_worker(preview_input, worker_name)
        if not worker.can_handle(preview_input):
            source_label = source_url or raw.original_filename
            raise WorkerError(
                f"Worker cannot handle source: {worker_name} for {source_type} {source_label}",
                code="WORKER_CANNOT_HANDLE",
                fallback_allowed=False,
            )

    def _enforce_sync_size_limit(self, input_path: Path | str) -> None:
        path = Path(input_path)
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"Input file does not exist: {path}")
        size = path.stat().st_size
        limit_name, limit = select_size_limit(path, self.config)
        if size > limit:
            raise SystemResourceError(
                f"Input file exceeds synchronous {limit_name} limit: {size} > {limit} bytes",
                code="INPUT_TOO_LARGE_SYNC",
            )

    def _run_quality_step(
        self,
        trace: TraceWriter,
        job,
        result: WorkerResult,
        input: WorkerInput,
    ) -> QualityReport:
        trace.worker_started(job, QUALITY_CHECKER_NAME, QUALITY_CHECKER_VERSION)
        started = time.perf_counter()
        quality = check_quality(result, input)
        status = "success" if quality.score >= 0.75 else "partial" if quality.score >= 0.45 else "failed"
        step_result = WorkerResult(
            status=status,
            worker_name=QUALITY_CHECKER_NAME,
            worker_version=QUALITY_CHECKER_VERSION,
            markdown=result.markdown,
            warnings=quality.warnings,
            metadata={"quality": quality.model_dump(mode="json")},
            confidence=quality.confidence,
        )
        trace.worker_finished(
            job,
            step_result,
            duration_ms=elapsed_ms(started),
            output_hash=hash_text(result.markdown),
        )
        trace.quality_checked(job, quality)
        return quality

    def _run_normalizer_step(
        self,
        trace: TraceWriter,
        job,
        result: WorkerResult,
    ) -> WorkerResult:
        trace.worker_started(job, MARKDOWN_NORMALIZER_NAME, MARKDOWN_NORMALIZER_VERSION)
        started = time.perf_counter()
        normalized = normalize_markdown_body(result.markdown or "")
        normalized_result = result.model_copy(
            update={
                "markdown": normalized,
                "worker_name": result.worker_name,
                "worker_version": result.worker_version,
            }
        )
        step_result = WorkerResult(
            status=result.status,
            worker_name=MARKDOWN_NORMALIZER_NAME,
            worker_version=MARKDOWN_NORMALIZER_VERSION,
            markdown=normalized,
            warnings=result.warnings,
            metadata={"normalized": True},
        )
        trace.worker_finished(
            job,
            step_result,
            duration_ms=elapsed_ms(started),
            output_hash=hash_text(normalized),
        )
        return normalized_result


def elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def run_worker_with_timeout(worker, input: WorkerInput, *, timeout_seconds: float | None) -> WorkerResult:
    if timeout_seconds is None:
        return worker.run(input)

    results: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=1)

    def target() -> None:
        try:
            results.put(("result", worker.run(input)))
        except Exception as error:
            results.put(("error", error))

    # Python cannot safely kill a running thread; worker-specific hard timeouts
    # still matter for external tools. This keeps the runner request bounded.
    thread = threading.Thread(target=target, name=f"swallow-worker-{worker.name}", daemon=True)
    thread.start()
    thread.join(timeout_seconds)
    if thread.is_alive():
        raise WorkerTimeoutError(
            f"Worker timed out after {timeout_seconds:g}s: {worker.name}",
            code="WORKER_TIMEOUT",
            retryable=True,
            fallback_allowed=True,
        )

    kind, payload = results.get_nowait()
    if kind == "error":
        raise payload
    return payload


def read_timeout_seconds(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def quality_status(quality: QualityReport) -> str:
    return "success" if quality.score >= 0.75 else "partial" if quality.score >= 0.45 else "failed"


def worker_result_error_code(result: WorkerResult) -> str:
    value = result.metadata.get("error_code")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return "WORKER_RESULT_FAILED"


def worker_exception_warning(error: Exception) -> str:
    payload = error_to_dict(error)
    value = payload.get("code") or payload.get("type") or "WORKER_FAILED"
    return str(value)


def hash_text(text: str | None) -> str | None:
    if text is None:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


SENSITIVE_PARAM_PARTS = ("token", "password", "secret", "credential", "cookie")
SENSITIVE_PARAM_KEYS = {"api_key", "apikey", "access_key", "private_key"}
REDACTED = "<redacted>"


def sanitized_worker_params(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {str(key): sanitize_param_value(str(key), item) for key, item in sorted(value.items(), key=lambda item: str(item[0]))}


def sanitize_param_value(key: str, value: Any) -> Any:
    lowered = key.lower()
    if is_sensitive_param_key(lowered):
        return REDACTED
    if isinstance(value, dict):
        return {str(child_key): sanitize_param_value(str(child_key), child) for child_key, child in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, list):
        return [sanitize_param_value(key, item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_param_value(key, item) for item in value]
    if isinstance(value, set):
        return sorted(sanitize_param_value(key, item) for item in value)
    if isinstance(value, Path):
        return str(value)
    return value


def is_sensitive_param_key(lowered_key: str) -> bool:
    if lowered_key.endswith("_env") or lowered_key.endswith("_env_var"):
        return False
    return lowered_key in SENSITIVE_PARAM_KEYS or any(part in lowered_key for part in SENSITIVE_PARAM_PARTS)


def hash_params(params: dict[str, Any]) -> str:
    payload = json.dumps(params, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def has_later_fallback(plan: list[str], current_index: int) -> bool:
    return any(step.startswith("fallback:") for step in plan[current_index + 1 :])


def select_size_limit(path: Path, config: IngestConfig) -> tuple[str, int]:
    mime_type, _ = mimetypes.guess_type(path.name)
    suffix = path.suffix.lower()
    if is_pdf_file(path, mime_type):
        return "pdf", config.limits.max_pdf_size_bytes
    if is_audio_video_file(path, mime_type):
        return "audio_video", config.limits.max_audio_video_size_bytes
    if suffix in {".htm", ".html"} or mime_type == "text/html":
        return "html", config.limits.max_html_size_bytes
    return "file", config.limits.max_file_size_bytes


def attach_job_context_to_error(error: Exception, job) -> None:
    setattr(error, "swallow_job_id", job.id)
    setattr(error, "swallow_trace_path", job.trace_path)
    setattr(error, "swallow_manifest_path", f"{job.job_dir}/manifest.json")
