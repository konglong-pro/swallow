from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

import typer

from swallow.core import doctor as doctor_module
from swallow.core.batch import BatchRunner
from swallow.core.config import load_config
from swallow.core.errors import IngestError, format_error
from swallow.core.job_store import JobStore, read_trace_events, render_trace_jsonl
from swallow.core.registry import default_registry
from swallow.core.runner import IngestRunner, PreparedIngestJob
from swallow.sdk.result_mapper import map_job, map_result

app = typer.Typer(no_args_is_help=True, help="Swallow ingest toolkit.")
mcp_app = typer.Typer(no_args_is_help=True, help="MCP adapter commands.")
queue_app = typer.Typer(no_args_is_help=True, help="Async queue commands.")
app.add_typer(mcp_app, name="mcp")
app.add_typer(queue_app, name="queue")


@app.callback()
def callback(
    ctx: typer.Context,
    store: Path = typer.Option(Path("."), "--store", help="Store root for raw_store/ and jobs/."),
    config: Path | None = typer.Option(None, "--config", help="Path to swallow.config.yaml."),
) -> None:
    configure_stdio()
    try:
        ctx.obj = {"store": store, "config": load_config(config), "config_path": config}
    except (FileNotFoundError, ValueError) as error:
        typer.echo(f"Config failed: {error}", err=True)
        raise typer.Exit(code=2) from error


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


@app.command(name="file")
def file_command(
    ctx: typer.Context,
    path: Path,
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable IngestResult JSON."),
    jsonl_output: bool = typer.Option(False, "--jsonl", help="Print machine-readable JSONL job events."),
) -> None:
    run_ingest_command(
        ctx,
        lambda runner: runner.prepare_file_job(path),
        json_output=json_output,
        jsonl_output=jsonl_output,
    )


@app.command()
def url(
    ctx: typer.Context,
    url_value: str,
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable IngestResult JSON."),
    jsonl_output: bool = typer.Option(False, "--jsonl", help="Print machine-readable JSONL job events."),
) -> None:
    run_ingest_command(
        ctx,
        lambda runner: runner.prepare_url_job(url_value),
        json_output=json_output,
        jsonl_output=jsonl_output,
    )


@app.command(name="browser-capture")
def browser_capture(
    ctx: typer.Context,
    path: Path,
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable IngestResult JSON."),
    jsonl_output: bool = typer.Option(False, "--jsonl", help="Print machine-readable JSONL job events."),
) -> None:
    run_ingest_command(
        ctx,
        lambda runner: runner.prepare_browser_capture_job(path),
        json_output=json_output,
        jsonl_output=jsonl_output,
    )


@app.command()
def archive(
    ctx: typer.Context,
    path: Path,
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable IngestResult JSON."),
    jsonl_output: bool = typer.Option(False, "--jsonl", help="Print machine-readable JSONL job events."),
) -> None:
    run_ingest_command(
        ctx,
        lambda runner: runner.prepare_archive_job(path),
        json_output=json_output,
        jsonl_output=jsonl_output,
    )


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


@mcp_app.command(name="serve")
def mcp_serve(
    ctx: typer.Context,
    cwd: Path | None = typer.Option(None, "--cwd", help="Working directory for the wrapped swallow CLI."),
    allowed_roots: list[Path] | None = typer.Option(
        None,
        "--allowed-root",
        help="Allowed local input root. May be repeated. Defaults to cwd and store root.",
    ),
    command: str = typer.Option("swallow", "--command", help="Swallow CLI command used by the MCP adapter."),
    max_processes: int = typer.Option(2, "--max-processes", min=1, help="Maximum concurrent wrapped CLI processes."),
    enable_url_ingest: bool = typer.Option(False, "--enable-url-ingest", help="Allow http/https URL ingest tools."),
) -> None:
    """Run the Swallow MCP server over stdio."""
    try:
        from swallow.mcp import create_mcp_server
    except ImportError as error:
        if getattr(error, "name", "").split(".")[0] == "mcp":
            typer.echo("MCP support requires the mcp extra: install swallow[mcp].", err=True)
            raise typer.Exit(code=2) from error
        raise

    server = create_mcp_server(
        store_root=ctx.obj["store"],
        config_path=ctx.obj["config_path"],
        command=command,
        cwd=cwd,
        allowed_roots=allowed_roots,
        max_processes=max_processes,
        enable_url_ingest=enable_url_ingest,
    )
    server.run(transport="stdio")


@queue_app.command(name="worker")
def queue_worker_command(
    ctx: typer.Context,
    once: bool = typer.Option(False, "--once", help="Process at most one queued job and exit."),
    worker_id: str | None = typer.Option(None, "--worker-id", help="Stable worker id for queue leases."),
    lease_seconds: float = typer.Option(300.0, "--lease-seconds", min=1.0, help="Queue lease duration in seconds."),
    heartbeat_interval: float = typer.Option(30.0, "--heartbeat-interval", min=0.1, help="Queue heartbeat interval."),
    poll_interval: float = typer.Option(1.0, "--poll-interval", min=0.1, help="Idle poll interval for long-running workers."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    from swallow.core.queue_worker import QueueWorker

    worker = QueueWorker(
        store_root=ctx.obj["store"],
        config=ctx.obj["config"],
        worker_id=worker_id,
        lease_seconds=lease_seconds,
        heartbeat_interval=heartbeat_interval,
        poll_interval=poll_interval,
    )
    if once:
        result = worker.run_once()
        if json_output:
            typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
            return
        if result["status"] == "idle":
            typer.echo("No queued jobs.")
            return
        typer.echo(f"Job: {result['job_id']}")
        typer.echo(f"Status: {result['status']}")
        return
    worker.run_forever()


@queue_app.command(name="stats")
def queue_stats_command(
    ctx: typer.Context,
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    from swallow.core.queue_store import QueueStore

    stats = QueueStore(ctx.obj["store"]).stats()
    if json_output:
        typer.echo(json.dumps(stats, ensure_ascii=False, indent=2))
        return
    typer.echo(f"Queue: {stats['queue_path']}")
    for status, count in sorted(stats["jobs"].items()):
        typer.echo(f"{status}: {count}")


@queue_app.command(name="cancel")
def queue_cancel_command(
    ctx: typer.Context,
    identifier: str,
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    from swallow.sdk import QueueIngestClient

    client = QueueIngestClient(store_root=ctx.obj["store"])
    if identifier.startswith("batch_"):
        result = client.cancel_batch(identifier).model_dump(mode="json")
    else:
        result = client.cancel_job(identifier).model_dump(mode="json")
    if json_output:
        typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
        return
    typer.echo(f"Canceled: {identifier}")


def raise_not_implemented(scope: str) -> None:
    typer.echo(f"Not implemented in this phase: {scope}", err=True)
    raise typer.Exit(code=2)


def run_ingest_command(
    ctx: typer.Context,
    prepare: Callable[[IngestRunner], PreparedIngestJob],
    *,
    json_output: bool,
    jsonl_output: bool,
) -> None:
    if json_output and jsonl_output:
        raise typer.BadParameter("Use only one of --json or --jsonl")

    machine_output = json_output or jsonl_output
    runner = make_runner(ctx)
    try:
        prepared = prepare(runner)
    except (IngestError, FileNotFoundError, ValueError) as error:
        typer.echo(f"Ingest failed: {format_error(error)}", err=True)
        raise typer.Exit(code=1) from error

    if jsonl_output:
        emit_jsonl_event("job_submitted", {"job": map_job(ctx.obj["store"], prepared.job.id).model_dump(mode="json")})

    try:
        result = runner.run_prepared_job(prepared)
    except (IngestError, FileNotFoundError, ValueError) as error:
        if machine_output:
            sdk_result = map_result(ctx.obj["store"], prepared.job.id, content="preview")
            emit_machine_result(sdk_result, jsonl_output=jsonl_output)
            raise typer.Exit(code=exit_code_for_status(sdk_result.status)) from error
        typer.echo(f"Ingest failed: {format_error(error)}", err=True)
        raise typer.Exit(code=1) from error

    if machine_output:
        sdk_result = map_result(ctx.obj["store"], result.job.id, content="preview")
        emit_machine_result(sdk_result, jsonl_output=jsonl_output)
        raise typer.Exit(code=exit_code_for_status(sdk_result.status))

    print_success(result)


def emit_machine_result(result, *, jsonl_output: bool) -> None:
    payload = result.model_dump(mode="json")
    if jsonl_output:
        emit_jsonl_event(event_name_for_status(result.status), {"result": payload})
        return
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))


def emit_jsonl_event(event: str, payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps({"event": event, **payload}, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def event_name_for_status(status: str) -> str:
    if status == "success":
        return "job_finished"
    if status == "partial":
        return "job_partial"
    if status == "failed":
        return "job_failed"
    return "job_updated"


def exit_code_for_status(status: str) -> int:
    if status == "success":
        return 0
    if status == "partial":
        return 2
    return 1


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
