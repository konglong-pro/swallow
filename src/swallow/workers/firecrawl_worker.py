from __future__ import annotations

import importlib
import os
from typing import Any

from swallow.core.models import WorkerCapability, WorkerInput, WorkerResult
from swallow.workers.base import BaseWorker
from swallow.workers.web_common import get_job_dir, get_url, metadata_from_result, read_value, save_text_artifact, short_error


class FirecrawlWorker(BaseWorker):
    name = "firecrawl_worker"
    version = "0.1.0"
    capability = WorkerCapability(
        input_mime_types=[],
        source_types=["url"],
        strengths=["public_web", "clean_markdown", "external_api"],
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

        api_key_env = str(input.metadata.get("api_key_env") or "FIRECRAWL_API_KEY")
        try:
            result = firecrawl_scrape(url, api_key_env=api_key_env)
        except ImportError:
            return WorkerResult(
                status="failed",
                worker_name=self.name,
                worker_version=self.version,
                errors=["missing_optional_dependency: install with `uv sync --extra web`"],
                metadata={"crawler": "firecrawl", "source_url": url, "api_key_env": api_key_env},
            )
        except Exception as error:
            return WorkerResult(
                status="failed",
                worker_name=self.name,
                worker_version=self.version,
                errors=[f"firecrawl_scrape_failed: {type(error).__name__}: {short_error(error)}"],
                metadata={"crawler": "firecrawl", "source_url": url, "api_key_env": api_key_env},
            )

        markdown = read_value(result, "markdown")
        if not isinstance(markdown, str) or not markdown.strip():
            return WorkerResult(
                status="failed",
                worker_name=self.name,
                worker_version=self.version,
                errors=["firecrawl_empty_markdown"],
                metadata={"crawler": "firecrawl", "source_url": url, "api_key_env": api_key_env},
            )

        metadata = metadata_from_result(result)
        html = read_value(result, "html")
        artifacts: list[dict[str, Any]] = []
        job_dir = get_job_dir(input)
        if job_dir is not None and isinstance(html, str) and html.strip():
            artifact = save_text_artifact(job_dir, "intermediate/firecrawl/page.html", html)
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
                "crawler": "firecrawl",
                "source_url": url,
                "api_key_env": api_key_env,
                "status_code": metadata.get("statusCode") or metadata.get("status_code"),
                "html_length": len(html) if isinstance(html, str) else None,
                "firecrawl_metadata": metadata,
            },
        )


def firecrawl_scrape(url: str, *, api_key_env: str = "FIRECRAWL_API_KEY") -> Any:
    firecrawl = importlib.import_module("firecrawl")
    Firecrawl = getattr(firecrawl, "Firecrawl", None)
    FirecrawlApp = getattr(firecrawl, "FirecrawlApp", None)

    api_key = os.getenv(api_key_env) if api_key_env else None
    if Firecrawl is not None:
        client = Firecrawl(api_key=api_key) if api_key else Firecrawl()
        return client.scrape(url, formats=["markdown", "html"])

    if FirecrawlApp is None:
        raise ImportError("firecrawl client class not found")

    client = FirecrawlApp(api_key=api_key)
    try:
        return client.scrape_url(url, params={"formats": ["markdown", "html"]})
    except TypeError:
        return client.scrape_url(url, formats=["markdown", "html"])
