from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

from swallow.core.config import IngestConfig, load_config
from swallow.core.models import WorkerInput, WorkerResult
from swallow.core.runner import IngestRunner
from swallow.workers.crawl4ai_worker import Crawl4AIWorker
from swallow.workers.firecrawl_worker import FirecrawlWorker
from swallow.workers.playwright_profile_worker import PlaywrightProfileWorker
from swallow.workers.playwright_worker import PlaywrightWorker

DEFAULT_URL = "https://example.com/"
DEFAULT_TOKENS = ("Example",)
TARGETS = ("router", "firecrawl", "crawl4ai", "playwright", "profile")


def main() -> int:
    configure_stdio()
    parser = argparse.ArgumentParser(description="Run layered real URL ingest runtime smoke tests.")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--store", type=Path, help="Store root. Defaults to a temporary directory.")
    parser.add_argument("--config", type=Path, help="Optional swallow config path.")
    parser.add_argument("--token", action="append", dest="tokens", help="Required token in output Markdown.")
    parser.add_argument(
        "--target",
        action="append",
        choices=[*TARGETS, "all"],
        help="Smoke target to run. Can be repeated. Defaults to all targets.",
    )
    parser.add_argument(
        "--profile-dir",
        type=Path,
        help="Profile directory for the profile target. Defaults to a temporary profile under --store.",
    )
    parser.add_argument(
        "--require-firecrawl",
        action="store_true",
        help="Fail when the Firecrawl API key is missing instead of skipping the firecrawl target.",
    )
    args = parser.parse_args()

    store = args.store or Path(tempfile.mkdtemp(prefix="swallow-web-smoke-"))
    store.mkdir(parents=True, exist_ok=True)
    config = load_config(args.config) if args.config else IngestConfig()
    required_tokens = tuple(args.tokens or DEFAULT_TOKENS)

    print(f"Store: {store}")
    print(f"URL: {args.url}")
    for target in resolve_targets(args.target):
        outcome = run_target(target, args.url, store, config, required_tokens, args)
        print(outcome)

    print("Web runtime smoke: success")
    return 0


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def resolve_targets(selected: list[str] | None) -> list[str]:
    if not selected:
        return list(TARGETS)

    resolved: list[str] = []
    for value in selected:
        candidates = TARGETS if value == "all" else (value,)
        for candidate in candidates:
            if candidate not in resolved:
                resolved.append(candidate)
    return resolved


def run_target(
    target: str,
    url: str,
    store: Path,
    config: IngestConfig,
    required_tokens: tuple[str, ...],
    args: argparse.Namespace,
) -> str:
    if target == "router":
        return run_router_smoke(url, store / "router", config, required_tokens)
    if target == "firecrawl":
        return run_firecrawl_smoke(url, store / "firecrawl", config, required_tokens, require=args.require_firecrawl)
    if target == "crawl4ai":
        return run_worker_smoke("crawl4ai", Crawl4AIWorker(), url, store / "crawl4ai", config, required_tokens)
    if target == "playwright":
        return run_worker_smoke(
            "playwright",
            PlaywrightWorker(),
            url,
            store / "playwright",
            config,
            required_tokens,
            overrides={"headless": True, "screenshot": True},
        )
    if target == "profile":
        profile_dir = args.profile_dir or store / "profile" / "browser-profile"
        return run_worker_smoke(
            "profile",
            PlaywrightProfileWorker(),
            url,
            store / "profile",
            config,
            required_tokens,
            overrides={"profile_dir": str(profile_dir), "headless": True, "screenshot": True},
        )
    raise AssertionError(f"Unknown web smoke target: {target}")


def run_router_smoke(url: str, store: Path, config: IngestConfig, required_tokens: tuple[str, ...]) -> str:
    store.mkdir(parents=True, exist_ok=True)
    result = IngestRunner(store_root=store, config=config).ingest_url(url)
    assert_web_result(result.document.content.markdown, required_tokens)
    return f"[success] router: job={result.job.id} document={store / result.document_path}"


def run_firecrawl_smoke(
    url: str,
    store: Path,
    config: IngestConfig,
    required_tokens: tuple[str, ...],
    *,
    require: bool,
) -> str:
    api_key_env = str(config.worker_params("firecrawl_worker").get("api_key_env") or "FIRECRAWL_API_KEY")
    if not os.getenv(api_key_env):
        message = f"[skipped] firecrawl: {api_key_env} is not set"
        if require:
            raise AssertionError(message)
        return message
    return run_worker_smoke("firecrawl", FirecrawlWorker(), url, store, config, required_tokens)


def run_worker_smoke(
    target: str,
    worker,
    url: str,
    store: Path,
    config: IngestConfig,
    required_tokens: tuple[str, ...],
    *,
    overrides: dict[str, object] | None = None,
) -> str:
    job_dir = store / "job"
    job_dir.mkdir(parents=True, exist_ok=True)
    metadata = {
        "original_filename": f"{target}.url",
        "job_dir": str(job_dir),
        **config.worker_params(worker.name),
    }
    if overrides:
        metadata.update(overrides)
    result = worker.run(
        WorkerInput(
            job_id=f"ing_{target}_smoke",
            raw_id="raw_web_smoke",
            input_path=str(store / f"{target}.url.json"),
            mime_type="application/json",
            source_type="url",
            source_url=url,
            metadata=metadata,
        )
    )
    assert_worker_result(target, result, required_tokens)
    artifacts = ", ".join(str(artifact.get("path")) for artifact in result.artifacts) or "none"
    return f"[success] {target}: worker={result.worker_name} artifacts={artifacts}"


def assert_worker_result(target: str, result: WorkerResult, required_tokens: tuple[str, ...]) -> None:
    if result.status != "success":
        errors = "; ".join(result.errors) or "no errors"
        raise AssertionError(f"{target} smoke failed: {errors}")
    assert_web_result(result.markdown or "", required_tokens)


def assert_web_result(markdown: str, required_tokens: tuple[str, ...] = DEFAULT_TOKENS) -> None:
    missing = [token for token in required_tokens if token not in markdown]
    if missing:
        raise AssertionError(f"Web output missing tokens {missing}. Markdown was:\n{markdown}")


if __name__ == "__main__":
    raise SystemExit(main())
