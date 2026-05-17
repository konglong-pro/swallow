from __future__ import annotations

import asyncio
from typing import Any

from swallow.core.models import WorkerCapability, WorkerInput, WorkerResult
from swallow.workers.base import BaseWorker
from swallow.workers.web_common import get_job_dir, get_url, metadata_from_result, read_value, save_text_artifact, short_error


class Crawl4AIWorker(BaseWorker):
    name = "crawl4ai_worker"
    version = "0.1.0"
    capability = WorkerCapability(
        input_mime_types=[],
        source_types=["url"],
        strengths=["self_hosted_web", "dynamic_page", "rag_markdown"],
        cost_level="medium",
        requires_gpu=False,
        requires_network=True,
        supports_batch=False,
    )

    def can_handle(self, input: WorkerInput) -> bool:
        return input.source_type == "url" and bool(input.source_url)

    def run(self, input: WorkerInput) -> WorkerResult:
        url = get_url(input)
        if not url:
            return WorkerResult(
                status="failed",
                worker_name=self.name,
                worker_version=self.version,
                errors=["missing_worker_input: source_url"],
            )

        timeout_seconds = to_positive_int(input.metadata.get("timeout_seconds"), default=180)
        try:
            result = crawl4ai_fetch(url, timeout_seconds=timeout_seconds)
        except ImportError:
            return WorkerResult(
                status="failed",
                worker_name=self.name,
                worker_version=self.version,
                errors=["missing_optional_dependency: install with `uv sync --extra web`"],
                metadata={"crawler": "crawl4ai", "source_url": url, "timeout_seconds": timeout_seconds},
            )
        except Exception as error:
            return WorkerResult(
                status="failed",
                worker_name=self.name,
                worker_version=self.version,
                errors=[f"crawl4ai_fetch_failed: {type(error).__name__}: {short_error(error)}"],
                metadata={"crawler": "crawl4ai", "source_url": url, "timeout_seconds": timeout_seconds},
            )

        markdown = read_value(result, "markdown")
        if not isinstance(markdown, str) or not markdown.strip():
            return WorkerResult(
                status="failed",
                worker_name=self.name,
                worker_version=self.version,
                errors=["crawl4ai_empty_markdown"],
                metadata={"crawler": "crawl4ai", "source_url": url, "timeout_seconds": timeout_seconds},
            )

        metadata = metadata_from_result(result)
        html = read_value(result, "html")
        artifacts: list[dict[str, Any]] = []
        job_dir = get_job_dir(input)
        if job_dir is not None and isinstance(html, str) and html.strip():
            artifact = save_text_artifact(job_dir, "intermediate/crawl4ai/page.html", html)
            artifact["type"] = "html"
            artifacts.append(artifact)

        return WorkerResult(
            status="success",
            worker_name=self.name,
            worker_version=self.version,
            markdown=markdown,
            title=metadata.get("title"),
            artifacts=artifacts,
            metadata={
                "crawler": "crawl4ai",
                "source_url": url,
                "timeout_seconds": timeout_seconds,
                "status_code": metadata.get("statusCode") or metadata.get("status_code"),
                "html_length": len(html) if isinstance(html, str) else None,
                "crawl4ai_metadata": metadata,
            },
        )


def crawl4ai_fetch(url: str, *, timeout_seconds: int = 180) -> Any:
    return asyncio.run(crawl4ai_fetch_with_timeout(url, timeout_seconds=timeout_seconds))


async def crawl4ai_fetch_async(url: str) -> Any:
    from crawl4ai import AsyncWebCrawler

    async with AsyncWebCrawler() as crawler:
        return await crawler.arun(url=url)


async def crawl4ai_fetch_with_timeout(url: str, *, timeout_seconds: int) -> Any:
    return await asyncio.wait_for(crawl4ai_fetch_async(url), timeout=timeout_seconds)


def to_positive_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default
