from __future__ import annotations

import json
from pathlib import Path

from swallow.core.models import WorkerInput
from swallow.workers import platform_article_worker as module
from swallow.workers.platform_article_worker import WeChatArticleWorker
from swallow.workers.platform_common import PlatformPage

FIXTURES = Path(__file__).parent / "fixtures" / "web"


def make_input(url: str, job_dir: Path) -> WorkerInput:
    return WorkerInput(
        job_id="ing_test",
        raw_id="raw_test",
        input_path=str(job_dir / "url.json"),
        mime_type="application/json",
        source_type="url",
        source_url=url,
        metadata={"job_dir": str(job_dir), "original_filename": "article.url"},
    )


def test_wechat_article_worker_extracts_article_and_lazy_image(monkeypatch, tmp_path):
    html = (FIXTURES / "wechat_article_success.html").read_text(encoding="utf-8")
    monkeypatch.setattr(
        module,
        "render_platform_page",
        lambda url, **_: PlatformPage(html=html, title="WeChat", final_url=url, status_code=200),
    )

    result = WeChatArticleWorker().run(make_input("https://mp.weixin.qq.com/s/abc", tmp_path / "job"))

    assert result.status == "success"
    assert result.metadata["platform"] == "wechat"
    assert result.metadata["url_kind"] == "wechat_article"
    assert result.metadata["text_char_count"] >= 80
    assert "Swallow 平台导入计划" in result.markdown
    extraction = json.loads((tmp_path / "job" / "intermediate" / "wechat" / "platform_extraction.json").read_text(encoding="utf-8"))
    assert extraction["assets"] == [{"type": "image", "url": "https://example.com/image.png", "role": "inline"}]


def test_wechat_article_worker_rejects_deleted_shell(monkeypatch, tmp_path):
    html = (FIXTURES / "wechat_article_deleted.html").read_text(encoding="utf-8")
    monkeypatch.setattr(
        module,
        "render_platform_page",
        lambda url, **_: PlatformPage(html=html, title="Deleted", final_url=url, status_code=200),
    )

    result = WeChatArticleWorker().run(make_input("https://mp.weixin.qq.com/s/abc", tmp_path / "job"))

    assert result.status == "failed"
    assert result.metadata["error_code"] == "PLATFORM_CONTENT_UNAVAILABLE"
    assert "platform_content_unavailable" in result.warnings


def test_wechat_article_worker_reports_open_in_client_shell(monkeypatch, tmp_path):
    html = (FIXTURES / "wechat_article_open_client.html").read_text(encoding="utf-8")
    monkeypatch.setattr(
        module,
        "render_platform_page",
        lambda url, **_: PlatformPage(html=html, title="Open in WeChat", final_url=url, status_code=200),
    )

    result = WeChatArticleWorker().run(make_input("https://mp.weixin.qq.com/s/abc", tmp_path / "job"))

    assert result.status == "failed"
    assert result.metadata["error_code"] == "PLATFORM_CONTENT_UNAVAILABLE"
    assert result.metadata["page_state"] == "deleted_or_unavailable"
    assert "platform_content_unavailable" in result.warnings

