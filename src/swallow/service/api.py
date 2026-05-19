from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Callable

from fastapi import Body, FastAPI, File, HTTPException, Query, Response, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from swallow.core.config import IngestConfig, load_config
from swallow.core.errors import IngestError, InputError, error_to_dict
from swallow.core.job_store import JobStore, read_trace_events, render_trace_jsonl
from swallow.core.models import IngestRunResult
from swallow.core.registry import default_registry
from swallow.core.runner import IngestRunner, PreparedIngestJob
from swallow.sdk.errors import IngestSandboxError, IngestSdkConfigError, IngestSdkJobNotFound, IngestSdkProtocolError
from swallow.sdk.result_mapper import map_job, map_result
from swallow.sdk.security import validate_ingest_url


class UrlIngestRequest(BaseModel):
    url: str


class RerunRequest(BaseModel):
    worker: str | None = None


def create_app(
    store_root: str | Path | None = None,
    *,
    config: IngestConfig | None = None,
    config_path: str | Path | None = None,
) -> FastAPI:
    root = Path(store_root or os.getenv("SWALLOW_STORE", "."))
    ingest_config = config or load_config(config_path)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            yield
        finally:
            app.state.executor.shutdown(wait=False)

    app = FastAPI(title="Swallow Ingest API", version="0.1.0", lifespan=lifespan)
    app.state.store_root = root
    app.state.config = ingest_config
    app.state.executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="swallow-service")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/workers")
    def list_workers() -> list[dict[str, Any]]:
        return default_registry(app.state.config).describe()

    @app.post("/v1/ingest/file")
    async def ingest_file_v1(wait: bool = Query(default=False), file: UploadFile = File(...)) -> dict[str, Any]:
        return await submit_file_impl(app, file, wait=wait)

    @app.post("/ingest/file")
    async def ingest_file_legacy(response: Response, file: UploadFile = File(...)) -> dict[str, Any]:
        mark_deprecated(response, "/v1/ingest/file")
        return await ingest_file_impl(app, file)

    @app.post("/v1/ingest/url")
    def ingest_url_v1(request: UrlIngestRequest, wait: bool = Query(default=False)) -> dict[str, Any]:
        return submit_url_impl(app, request, wait=wait)

    @app.post("/ingest/url")
    def ingest_url_legacy(request: UrlIngestRequest, response: Response) -> dict[str, Any]:
        mark_deprecated(response, "/v1/ingest/url")
        return ingest_url_impl(app, request)

    @app.post("/v1/ingest/browser-capture")
    def ingest_browser_capture_v1(payload: dict[str, Any], wait: bool = Query(default=False)) -> dict[str, Any]:
        return submit_browser_capture_impl(app, payload, wait=wait)

    @app.post("/ingest/browser-capture")
    def ingest_browser_capture_legacy(payload: dict[str, Any], response: Response) -> dict[str, Any]:
        mark_deprecated(response, "/v1/ingest/browser-capture")
        return ingest_browser_capture_impl(app, payload)

    @app.post("/v1/ingest/archive")
    async def ingest_archive_v1(wait: bool = Query(default=False), file: UploadFile = File(...)) -> dict[str, Any]:
        return await submit_archive_impl(app, file, wait=wait)

    @app.post("/ingest/archive")
    async def ingest_archive_legacy(response: Response, file: UploadFile = File(...)) -> dict[str, Any]:
        mark_deprecated(response, "/v1/ingest/archive")
        return await ingest_archive_impl(app, file)

    @app.post("/v1/jobs/{job_id}/rerun")
    def rerun_job_v1(job_id: str, request: RerunRequest | None = Body(default=None)) -> dict[str, Any]:
        return rerun_job_impl(app, job_id, request)

    @app.post("/jobs/{job_id}/rerun")
    def rerun_job_legacy(job_id: str, response: Response, request: RerunRequest | None = Body(default=None)) -> dict[str, Any]:
        mark_deprecated(response, f"/v1/jobs/{job_id}/rerun")
        return rerun_job_impl(app, job_id, request)

    @app.get("/jobs")
    def list_jobs(limit: int = 20) -> list[dict[str, Any]]:
        summaries = JobStore(root).list_jobs(limit=max(limit, 1))
        return [summary.model_dump(mode="json") for summary in summaries]

    @app.get("/v1/jobs")
    def list_jobs_v1(limit: int = 20) -> list[dict[str, Any]]:
        summaries = JobStore(root).list_jobs(limit=max(limit, 1))
        return [summary.model_dump(mode="json") for summary in summaries]

    @app.get("/v1/jobs/{job_id}")
    def get_job_v1(job_id: str) -> dict[str, Any]:
        return render_sdk_call(lambda: map_job(root, job_id))

    @app.get("/v1/jobs/{job_id}/result")
    def get_job_result_v1(
        job_id: str,
        content: str = Query(default="preview", pattern="^(none|preview|full)$"),
    ) -> dict[str, Any]:
        return render_sdk_call(lambda: map_result(root, job_id, content=content))

    @app.get("/jobs/{job_id}")
    def inspect_job_legacy(
        job_id: str,
        response: Response,
        raw: bool = Query(default=False),
        artifacts: bool = Query(default=False),
    ) -> dict[str, Any]:
        mark_deprecated(response, f"/v1/jobs/{job_id}")
        return inspect_job_impl(root, job_id, raw=raw, artifacts=artifacts)

    @app.get("/v1/jobs/{job_id}/document")
    def read_document_v1(job_id: str) -> PlainTextResponse:
        return read_job_text_file(root, job_id, filename="document.md", label="Document")

    @app.get("/v1/jobs/{job_id}/manifest")
    def read_manifest_v1(job_id: str) -> dict[str, Any]:
        path = root / "jobs" / job_id / "manifest.json"
        if not path.exists():
            raise not_found(f"Manifest not found for job: {job_id}", code="MANIFEST_NOT_FOUND")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise HTTPException(status_code=500, detail=error_to_dict(error)) from error
        return payload if isinstance(payload, dict) else {}

    @app.get("/v1/jobs/{job_id}/trace")
    def read_trace_v1(
        job_id: str,
        tail: int | None = Query(default=None, ge=0),
        json_output: bool = Query(default=False, alias="json"),
    ) -> Any:
        return read_trace_impl(root, job_id, tail=tail, json_output=json_output)

    @app.get("/jobs/{job_id}/trace")
    def read_trace_legacy(
        job_id: str,
        response: Response,
        tail: int | None = Query(default=None, ge=0),
        json_output: bool = Query(default=False, alias="json"),
    ) -> Any:
        mark_deprecated(response, f"/v1/jobs/{job_id}/trace")
        return read_trace_impl(root, job_id, tail=tail, json_output=json_output)

    return app


async def ingest_file_impl(app: FastAPI, file: UploadFile) -> dict[str, Any]:
    path = await write_upload_to_temp(file)
    try:
        return run_and_render(app, lambda runner: runner.ingest_file(path))
    finally:
        cleanup_temp_upload(path)


async def submit_file_impl(app: FastAPI, file: UploadFile, *, wait: bool) -> dict[str, Any]:
    path = await write_upload_to_temp(file)
    try:
        return submit_and_render(app, lambda runner: runner.prepare_file_job(path), wait=wait)
    finally:
        cleanup_temp_upload(path)


def ingest_url_impl(app: FastAPI, request: UrlIngestRequest) -> dict[str, Any]:
    url = validate_service_url(request.url)
    return run_and_render(app, lambda runner: runner.ingest_url(url))


def submit_url_impl(app: FastAPI, request: UrlIngestRequest, *, wait: bool) -> dict[str, Any]:
    url = validate_service_url(request.url)
    return submit_and_render(app, lambda runner: runner.prepare_url_job(url), wait=wait)


def ingest_browser_capture_impl(app: FastAPI, payload: dict[str, Any]) -> dict[str, Any]:
    path = write_json_to_temp(payload, suffix=".json")
    try:
        return run_and_render(app, lambda runner: runner.ingest_browser_capture(path))
    finally:
        path.unlink(missing_ok=True)


def submit_browser_capture_impl(app: FastAPI, payload: dict[str, Any], *, wait: bool) -> dict[str, Any]:
    path = write_json_to_temp(payload, suffix=".json")
    try:
        return submit_and_render(app, lambda runner: runner.prepare_browser_capture_job(path), wait=wait)
    finally:
        path.unlink(missing_ok=True)


async def ingest_archive_impl(app: FastAPI, file: UploadFile) -> dict[str, Any]:
    path = await write_upload_to_temp(file)
    try:
        return run_and_render(app, lambda runner: runner.ingest_archive(path))
    finally:
        cleanup_temp_upload(path)


async def submit_archive_impl(app: FastAPI, file: UploadFile, *, wait: bool) -> dict[str, Any]:
    path = await write_upload_to_temp(file)
    try:
        return submit_and_render(app, lambda runner: runner.prepare_archive_job(path), wait=wait)
    finally:
        cleanup_temp_upload(path)


def rerun_job_impl(app: FastAPI, job_id: str, request: RerunRequest | None) -> dict[str, Any]:
    worker = request.worker if request is not None else None
    return run_and_render(app, lambda runner: runner.rerun_job(job_id, worker_name=worker))


def inspect_job_impl(root: Path, job_id: str, *, raw: bool, artifacts: bool) -> dict[str, Any]:
    payload = JobStore(root).inspect_job(job_id, include_raw=raw, include_artifacts=artifacts)
    if payload is None:
        raise not_found(f"Job not found: {job_id}", code="JOB_NOT_FOUND")
    return payload


def read_job_text_file(root: Path, job_id: str, *, filename: str, label: str) -> PlainTextResponse:
    path = root / "jobs" / job_id / filename
    if not path.exists():
        raise not_found(f"{label} not found for job: {job_id}", code=f"{label.upper()}_NOT_FOUND")
    return PlainTextResponse(path.read_text(encoding="utf-8"))


def read_trace_impl(root: Path, job_id: str, *, tail: int | None, json_output: bool) -> Any:
    path = root / "jobs" / job_id / "trace.jsonl"
    if not path.exists():
        raise not_found(f"Trace not found for job: {job_id}", code="TRACE_NOT_FOUND")
    if json_output:
        return read_trace_events(path, limit=tail)
    if tail is not None:
        return PlainTextResponse(render_trace_jsonl(path, limit=tail))
    return PlainTextResponse(path.read_text(encoding="utf-8"))


def validate_service_url(url: str) -> str:
    try:
        return validate_ingest_url(url)
    except IngestSandboxError as error:
        raise HTTPException(
            status_code=400,
            detail=error_to_dict(InputError(str(error), code="SDK_SANDBOX_REJECTED")),
        ) from error


def mark_deprecated(response: Response, successor_path: str) -> None:
    response.headers["Deprecation"] = "true"
    response.headers["Link"] = f"<{successor_path}>; rel=\"successor-version\""


def not_found(message: str, *, code: str) -> HTTPException:
    return HTTPException(status_code=404, detail=error_to_dict(InputError(message, code=code)))


def run_and_render(app: FastAPI, run: Any) -> dict[str, Any]:
    runner = IngestRunner(store_root=app.state.store_root, config=app.state.config)
    try:
        result = run(runner)
    except (IngestError, FileNotFoundError, ValueError) as error:
        raise http_exception_from_error(error) from error
    return render_run_result(result)


def submit_and_render(
    app: FastAPI,
    prepare: Callable[[IngestRunner], PreparedIngestJob],
    *,
    wait: bool,
) -> dict[str, Any]:
    runner = IngestRunner(store_root=app.state.store_root, config=app.state.config)
    try:
        prepared = prepare(runner)
    except (IngestError, FileNotFoundError, ValueError) as error:
        raise http_exception_from_error(error) from error

    if wait:
        return run_prepared_and_render_sdk(app, prepared)

    app.state.executor.submit(run_prepared_background, app, prepared)
    return map_job(app.state.store_root, prepared.job.id).model_dump(mode="json")


def run_prepared_and_render_sdk(app: FastAPI, prepared: PreparedIngestJob) -> dict[str, Any]:
    try:
        IngestRunner(store_root=app.state.store_root, config=app.state.config).run_prepared_job(prepared)
    except (IngestError, FileNotFoundError, ValueError):
        pass
    return map_result(app.state.store_root, prepared.job.id, content="preview").model_dump(mode="json")


def run_prepared_background(app: FastAPI, prepared: PreparedIngestJob) -> None:
    try:
        IngestRunner(store_root=app.state.store_root, config=app.state.config).run_prepared_job(prepared)
    except Exception:
        pass


def render_sdk_call(call: Callable[[], Any]) -> dict[str, Any]:
    try:
        value = call()
    except IngestSdkJobNotFound as error:
        raise not_found(str(error), code="JOB_NOT_FOUND") from error
    except (IngestSdkConfigError, IngestSdkProtocolError) as error:
        raise HTTPException(status_code=400, detail=error_to_dict(error)) from error
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


def http_exception_from_error(error: Exception) -> HTTPException:
    message = str(error)
    if message.startswith("Job not found:"):
        return HTTPException(status_code=404, detail=error_to_dict(error))
    return HTTPException(status_code=400, detail=error_to_dict(error))


def render_run_result(result: IngestRunResult) -> dict[str, Any]:
    return {
        "job": result.job.id,
        "status": "success",
        "raw_id": result.raw.raw_id,
        "document": result.document_path,
        "ingest_document": result.ingest_document_path,
        "trace": result.trace_path,
        "manifest": result.manifest_path,
        "ingest_document_id": result.document.id,
    }


async def write_upload_to_temp(file: UploadFile) -> Path:
    filename = safe_upload_filename(file.filename)
    temp_dir = Path(tempfile.mkdtemp(prefix="swallow-upload-"))
    path = temp_dir / filename
    with path.open("wb") as temp:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            temp.write(chunk)
    return path


def safe_upload_filename(filename: str | None) -> str:
    value = str(filename or "upload.bin").replace("\x00", "")
    name = PurePosixPath(PureWindowsPath(value).name).name
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip(" .")
    if not name or name in {".", ".."}:
        return "upload.bin"
    return name


def cleanup_temp_upload(path: Path) -> None:
    shutil.rmtree(path.parent, ignore_errors=True)


def write_json_to_temp(payload: dict[str, Any], *, suffix: str) -> Path:
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=suffix, encoding="utf-8") as temp:
        json.dump(payload, temp, ensure_ascii=False)
        temp.write("\n")
        return Path(temp.name)


app = create_app()
