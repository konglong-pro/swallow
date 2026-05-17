from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import typer

from swallow.core import doctor as doctor_module
from swallow.core.batch import BatchRunner
from swallow.core.config import load_config
from swallow.core.errors import IngestError, format_error
from swallow.core.job_store import JobStore, read_trace_events, render_trace_jsonl
from swallow.core.registry import default_registry
from swallow.core.runner import IngestRunner

app = typer.Typer(no_args_is_help=True, help="Swallow ingest toolkit.")


@app.callback()
def callback(
    ctx: typer.Context,
    store: Path = typer.Option(Path("."), "--store", help="Store root for raw_store/ and jobs/."),
    config: Path | None = typer.Option(None, "--config", help="Path to swallow.config.yaml."),
) -> None:
    configure_stdio()
    try:
        ctx.obj = {"store": store, "config": load_config(config)}
    except (FileNotFoundError, ValueError) as error:
        typer.echo(f"Config failed: {error}", err=True)
        raise typer.Exit(code=2) from error


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


@app.command(name="file")
def file_command(ctx: typer.Context, path: Path) -> None:
    runner = make_runner(ctx)
    try:
        result = runner.ingest_file(path)
    except IngestError as error:
        typer.echo(f"Ingest failed: {format_error(error)}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f"Job: {result.job.id}")
    typer.echo("Status: success")
    typer.echo(f"Document: {result.document_path}")
    typer.echo(f"Trace: {result.trace_path}")
    if result.manifest_path:
        typer.echo(f"Manifest: {result.manifest_path}")


@app.command()
def url(ctx: typer.Context, url_value: str) -> None:
    runner = make_runner(ctx)
    try:
        result = runner.ingest_url(url_value)
    except (IngestError, ValueError) as error:
        typer.echo(f"Ingest failed: {format_error(error)}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f"Job: {result.job.id}")
    typer.echo("Status: success")
    typer.echo(f"Document: {result.document_path}")
    typer.echo(f"Trace: {result.trace_path}")
    if result.manifest_path:
        typer.echo(f"Manifest: {result.manifest_path}")


@app.command(name="browser-capture")
def browser_capture(ctx: typer.Context, path: Path) -> None:
    runner = make_runner(ctx)
    try:
        result = runner.ingest_browser_capture(path)
    except (IngestError, FileNotFoundError, ValueError) as error:
        typer.echo(f"Ingest failed: {format_error(error)}", err=True)
        raise typer.Exit(code=1) from error
    print_success(result)


@app.command()
def archive(ctx: typer.Context, path: Path) -> None:
    runner = make_runner(ctx)
    try:
        result = runner.ingest_archive(path)
    except (IngestError, FileNotFoundError, ValueError) as error:
        typer.echo(f"Ingest failed: {format_error(error)}", err=True)
        raise typer.Exit(code=1) from error
    print_success(result)


@app.command(name="batch")
def batch_command(
    ctx: typer.Context,
    patterns: list[str] = typer.Argument(..., help="File paths, directories, or glob patterns to ingest."),
    workers: int = typer.Option(1, "--workers", "-j", min=1, help="Number of files to ingest concurrently."),
    json_output: bool = typer.Option(False, "--json", help="Print the batch summary as JSON."),
) -> None:
    summary = BatchRunner(store_root=ctx.obj["store"], config=ctx.obj["config"]).run(patterns, max_workers=workers)
    if json_output:
        typer.echo(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print_batch_summary(summary)
    if summary["status"] == "failed":
        raise typer.Exit(code=1)
    if summary["status"] == "partial":
        raise typer.Exit(code=2)


@app.command(name="jobs")
def jobs_command(
    ctx: typer.Context,
    limit: int = typer.Option(20, "--limit", min=1, help="Maximum number of jobs to list."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    summaries = JobStore(ctx.obj["store"]).list_jobs(limit=limit)
    if json_output:
        typer.echo(json.dumps([summary.model_dump(mode="json") for summary in summaries], ensure_ascii=False, indent=2))
        return
    print_job_summaries(summaries)


@app.command(name="inspect")
def inspect_command(
    ctx: typer.Context,
    job_id: str,
    include_raw: bool = typer.Option(False, "--raw", help="Include raw_store metadata and resolved raw path."),
    include_artifacts: bool = typer.Option(False, "--artifacts", help="Include declared and discovered job artifacts."),
) -> None:
    payload = JobStore(ctx.obj["store"]).inspect_job(job_id, include_raw=include_raw, include_artifacts=include_artifacts)
    if payload is None:
        raise typer.BadParameter(f"Job not found: {job_id}")
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))


@app.command(name="raw")
def raw_command(
    ctx: typer.Context,
    job_id: str,
    json_output: bool = typer.Option(False, "--json", help="Print raw metadata as JSON."),
) -> None:
    payload = load_inspection_payload(ctx, job_id, include_raw=True)
    raw = payload.get("raw")
    if not isinstance(raw, dict):
        typer.echo(f"No raw metadata found for job: {job_id}", err=True)
        raise typer.Exit(code=1)
    if json_output:
        typer.echo(json.dumps(raw, ensure_ascii=False, indent=2))
        return
    print_raw_info(raw)


@app.command(name="artifacts")
def artifacts_command(
    ctx: typer.Context,
    job_id: str,
    json_output: bool = typer.Option(False, "--json", help="Print artifacts as JSON."),
) -> None:
    payload = load_inspection_payload(ctx, job_id, include_artifacts=True)
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list):
        artifacts = []
    if json_output:
        typer.echo(json.dumps(artifacts, ensure_ascii=False, indent=2))
        return
    print_artifacts(artifacts)


@app.command(name="open")
def open_command(
    ctx: typer.Context,
    job_id: str,
    target: str = typer.Option(
        "document",
        "--target",
        "-t",
        help="Target: document, ingest-document, trace, job, raw, or artifact.",
    ),
    artifact: str | None = typer.Option(None, "--artifact", help="Artifact path or 1-based index for artifact target."),
    launch: bool = typer.Option(False, "--launch", help="Open the resolved path with the operating system."),
    json_output: bool = typer.Option(False, "--json", help="Print resolved path as JSON."),
) -> None:
    payload = load_inspection_payload(ctx, job_id, include_raw=True, include_artifacts=True)
    resolved = resolve_open_target(Path(ctx.obj["store"]), job_id, payload, target=target, artifact=artifact)
    if json_output:
        typer.echo(json.dumps(resolved, ensure_ascii=False, indent=2))
    else:
        typer.echo(f"Target: {resolved['target']}")
        typer.echo(f"Path: {resolved['path']}")
        typer.echo(f"Exists: {resolved['exists']}")
    if launch:
        if not resolved["exists"]:
            typer.echo(f"Cannot open missing path: {resolved['path']}", err=True)
            raise typer.Exit(code=1)
        launch_local_path(Path(str(resolved["path"])))


@app.command(name="trace")
def trace_command(
    ctx: typer.Context,
    job_id: str,
    tail: int | None = typer.Option(None, "--tail", help="Only print the last N trace events."),
    json_output: bool = typer.Option(False, "--json", help="Print trace events as a JSON array."),
) -> None:
    if tail is not None and tail < 0:
        raise typer.BadParameter("--tail must be greater than or equal to 0")
    trace_path = Path(ctx.obj["store"]) / "jobs" / job_id / "trace.jsonl"
    if not trace_path.exists():
        raise typer.BadParameter(f"Trace not found for job: {job_id}")
    if json_output:
        typer.echo(json.dumps(read_trace_events(trace_path, limit=tail), ensure_ascii=False, indent=2))
        return
    if tail is not None:
        typer.echo(render_trace_jsonl(trace_path, limit=tail), nl=False)
        return
    typer.echo(trace_path.read_text(encoding="utf-8"), nl=False)


@app.command(name="rerun")
def rerun_command(
    ctx: typer.Context,
    job_id: str,
    worker: str | None = typer.Option(None, "--worker", help="Force a specific worker for this rerun."),
) -> None:
    runner = make_runner(ctx)
    try:
        result = runner.rerun_job(job_id, worker_name=worker)
    except IngestError as error:
        typer.echo(f"Rerun failed: {format_error(error)}", err=True)
        raise typer.Exit(code=1) from error
    print_success(result)


@app.command(name="workers")
def workers_command(ctx: typer.Context) -> None:
    registry = default_registry(ctx.obj["config"])
    typer.echo(json.dumps(registry.describe(), ensure_ascii=False, indent=2))


@app.command(name="doctor")
def doctor_command(
    ctx: typer.Context,
    deep: bool = typer.Option(False, "--deep", help="Run checks that may start local runtimes such as Chromium."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
    fail_on_missing: bool = typer.Option(False, "--fail-on-missing", help="Exit with code 1 if any check is missing."),
) -> None:
    report = doctor_module.run_doctor(ctx.obj["config"], deep=deep)
    if json_output:
        typer.echo(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    else:
        print_doctor_report(report)
    if fail_on_missing and report.has_missing:
        raise typer.Exit(code=1)


def raise_not_implemented(scope: str) -> None:
    typer.echo(f"Not implemented in this phase: {scope}", err=True)
    raise typer.Exit(code=2)


def print_success(result) -> None:
    typer.echo(f"Job: {result.job.id}")
    typer.echo("Status: success")
    typer.echo(f"Document: {result.document_path}")
    typer.echo(f"Trace: {result.trace_path}")
    if result.manifest_path:
        typer.echo(f"Manifest: {result.manifest_path}")


def print_job_summaries(summaries) -> None:
    if not summaries:
        typer.echo("No jobs found.")
        return
    typer.echo("Job\tStatus\tSource\tWorker\tQuality\tDocument")
    for summary in summaries:
        quality = "" if summary.quality_score is None else f"{summary.quality_score:.2f}"
        worker = summary.primary_worker or ""
        source = summary.source_type or ""
        document = summary.document_path or ""
        typer.echo(f"{summary.job_id}\t{summary.status}\t{source}\t{worker}\t{quality}\t{document}")


def print_batch_summary(summary: dict[str, Any]) -> None:
    typer.echo(f"Batch: {summary['batch_id']}")
    typer.echo(f"Status: {summary['status']}")
    typer.echo(f"Total: {summary['total']}")
    typer.echo(f"Success: {summary['success']}")
    typer.echo(f"Partial: {summary['partial']}")
    typer.echo(f"Failed: {summary['failed']}")
    typer.echo(f"Summary: {summary['summary_path']}")
    typer.echo(f"Trace: {summary['trace_path']}")


def load_inspection_payload(ctx: typer.Context, job_id: str, **options: Any) -> dict[str, Any]:
    payload = JobStore(ctx.obj["store"]).inspect_job(job_id, **options)
    if payload is None:
        raise typer.BadParameter(f"Job not found: {job_id}")
    return payload


def print_raw_info(raw: dict[str, Any]) -> None:
    typer.echo(f"Raw ID: {raw.get('raw_id', '')}")
    typer.echo(f"SHA256: {raw.get('sha256', '')}")
    typer.echo(f"Original: {raw.get('original_filename', '')}")
    typer.echo(f"MIME: {raw.get('mime_type', '')}")
    typer.echo(f"Size: {raw.get('size_bytes', '')}")
    typer.echo(f"Path: {raw.get('path', '')}")
    typer.echo(f"Absolute: {raw.get('absolute_path', '')}")
    typer.echo(f"Exists: {raw.get('exists', False)}")
    typer.echo(f"Metadata: {raw.get('meta_path', '')}")


def print_artifacts(artifacts: list[Any]) -> None:
    if not artifacts:
        typer.echo("No artifacts found.")
        return
    typer.echo("Type\tSource\tExists\tSize\tPath")
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        size = artifact.get("size_bytes", "")
        typer.echo(
            f"{artifact.get('type', '')}\t"
            f"{artifact.get('source', '')}\t"
            f"{artifact.get('exists', False)}\t"
            f"{size}\t"
            f"{artifact.get('path', '')}"
        )


OPEN_TARGETS = {"document", "ingest-document", "trace", "job", "raw", "artifact"}


def resolve_open_target(
    store_root: Path,
    job_id: str,
    payload: dict[str, Any],
    *,
    target: str,
    artifact: str | None,
) -> dict[str, Any]:
    normalized_target = target.strip().lower().replace("_", "-")
    if normalized_target not in OPEN_TARGETS:
        raise typer.BadParameter(f"--target must be one of: {', '.join(sorted(OPEN_TARGETS))}")

    job_dir = store_root / "jobs" / job_id
    if normalized_target == "document":
        return path_result("document", job_dir / "document.md")
    if normalized_target == "ingest-document":
        return path_result("ingest-document", job_dir / "ingest_document.json")
    if normalized_target == "trace":
        return path_result("trace", job_dir / "trace.jsonl")
    if normalized_target == "job":
        return path_result("job", job_dir / "job.json")
    if normalized_target == "raw":
        raw = payload.get("raw")
        if not isinstance(raw, dict) or not raw.get("absolute_path"):
            raise typer.BadParameter(f"Raw path is not available for job: {job_id}")
        return path_result("raw", Path(str(raw["absolute_path"])))

    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise typer.BadParameter(f"No artifacts found for job: {job_id}")
    selected = select_artifact(artifacts, artifact)
    return path_result("artifact", Path(str(selected["absolute_path"])), artifact=selected)


def select_artifact(artifacts: list[Any], selector: str | None) -> dict[str, Any]:
    normalized = [artifact for artifact in artifacts if isinstance(artifact, dict)]
    if not normalized:
        raise typer.BadParameter("No artifacts found.")
    if selector is None:
        if len(normalized) == 1:
            return normalized[0]
        raise typer.BadParameter("Multiple artifacts found; pass --artifact with a 1-based index or artifact path.")
    if selector.isdigit():
        index = int(selector)
        if index < 1 or index > len(normalized):
            raise typer.BadParameter(f"Artifact index out of range: {selector}")
        return normalized[index - 1]
    selector_path = selector.replace("\\", "/").strip("/")
    for artifact in normalized:
        path = str(artifact.get("path", "")).replace("\\", "/").strip("/")
        if path == selector_path:
            return artifact
    raise typer.BadParameter(f"Artifact not found: {selector}")


def path_result(target: str, path: Path, *, artifact: dict[str, Any] | None = None) -> dict[str, Any]:
    resolved = path.resolve()
    result: dict[str, Any] = {
        "target": target,
        "path": str(resolved),
        "exists": resolved.exists(),
    }
    if artifact is not None:
        result["artifact"] = artifact
    return result


def launch_local_path(path: Path) -> None:
    if sys.platform.startswith("win"):
        os.startfile(str(path))  # type: ignore[attr-defined]
        return
    if sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=True)
        return
    subprocess.run(["xdg-open", str(path)], check=True)


def make_runner(ctx: typer.Context) -> IngestRunner:
    return IngestRunner(store_root=ctx.obj["store"], config=ctx.obj["config"])


def print_doctor_report(report) -> None:
    typer.echo(f"Python: {report.python_version}")
    typer.echo(f"Executable: {report.executable}")
    typer.echo(f"Platform: {report.platform}")
    typer.echo("")
    for check in report.checks:
        typer.echo(f"[{check.status}] {check.name}: {check.detail}")
        if check.install_hint and check.status != "ok":
            typer.echo(f"  hint: {check.install_hint}")


if __name__ == "__main__":
    app()
