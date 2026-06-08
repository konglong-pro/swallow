from __future__ import annotations

import pytest

from swallow.detectors.url_classifier import UrlKind, classify_url


@pytest.mark.parametrize(
    ("url", "kind", "platform", "needs_redirect"),
    [
        ("https://chatgpt.com/share/abc", UrlKind.CHATGPT_SHARE, "chatgpt", False),
        ("https://chat.openai.com/share/abc", UrlKind.CHATGPT_SHARE, "chatgpt", False),
        ("https://gemini.google.com/share/abc", UrlKind.GEMINI_SHARE, "gemini", False),
        ("https://g.co/gemini/share/abc", UrlKind.GEMINI_SHARE, "gemini", True),
        ("https://claude.ai/share/abc", UrlKind.CLAUDE_SHARE, "claude", False),
        ("https://chat.deepseek.com/share/abc", UrlKind.DEEPSEEK_SHARE, "deepseek", False),
        ("https://mp.weixin.qq.com/s/abc", UrlKind.WECHAT_ARTICLE, "wechat", False),
        ("https://www.youtube.com/watch?v=abc", UrlKind.YOUTUBE_VIDEO, "youtube", False),
        ("https://youtu.be/abc", UrlKind.YOUTUBE_VIDEO, "youtube", False),
        ("https://www.youtube.com/shorts/abc", UrlKind.YOUTUBE_VIDEO, "youtube", False),
        ("https://x.com/example/status/123", UrlKind.GENERIC_WEB, None, False),
        ("https://twitter.com/example/status/123", UrlKind.GENERIC_WEB, None, False),
        ("https://x.com/i/article/2061850535708483585", UrlKind.GENERIC_WEB, None, False),
        ("https://www.xiaohongshu.com/explore/abc", UrlKind.GENERIC_WEB, None, False),
        ("https://xhslink.com/a/b", UrlKind.GENERIC_WEB, None, False),
        ("https://zhuanlan.zhihu.com/p/123", UrlKind.GENERIC_WEB, None, False),
        ("https://www.zhihu.com/question/1/answer/2", UrlKind.GENERIC_WEB, None, False),
        ("https://example.com/article", UrlKind.GENERIC_WEB, None, False),
    ],
)
def test_classifies_platform_url_matrix(url: str, kind: UrlKind, platform: str | None, needs_redirect: bool):
    classification = classify_url(url)

    assert classification.kind == kind
    assert classification.platform == platform
    assert classification.source_url == url
    assert classification.normalized_url.startswith("https://")
    assert classification.needs_redirect_resolution is needs_redirect
