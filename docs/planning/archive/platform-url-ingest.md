---
doc_type: phase_plan
phase_id: platform-url-ingest
title: Platform URL Ingest
status: completed
canonical: true
read_by_default: false
supersedes: []
superseded_by: null
related_contracts:
  - docs/contracts/ingest-contract.md
related_adrs:
  - docs/adr/0001-swallow-capability-provider-boundary.md
release_gate: uv run --no-sync pytest -q -m "not network and not performance and not heavy"
---

# Platform URL Ingest

## Goal

Add platform-aware URL ingest for high-value share, article, post, and transcript sources while
preserving Swallow's ingest-only boundary. The phase upgrades current generic URL handling into
auditable platform extraction pipelines that either produce a traceable `document.md` candidate or
fail/return partial with explicit evidence.

This is the completed implementation plan for the Platform URL Ingest phase. Read the closeout first
for shipped behavior and remaining risks.

## Assumptions

- SDK and provider surfaces continue exposing URL ingest as one capability. External agents must not
  select internal platform workers directly.
- Heavy and network integrations remain optional. New dependencies require explicit approval and must
  stay behind extras or config.
- Platform pages can change without notice. Manual live smoke evidence is useful, but live platform
  checks are not retained as repository tests.
- Public share links are treated as user-supplied source URLs. Swallow converts visible/shared
  content to Markdown; it does not bypass access controls or retrieve private conversations.
- `document.md` remains a conversion candidate, not host truth.

## Non-Goals

- No agent orchestration, chat UI, memory, RAG, embeddings, summarization, translation, or LLM cleanup.
- No default media download for YouTube or other platforms.
- No guarantee that login-walled, deleted, rate-limited, region-blocked, or bot-challenged pages can
  be ingested.
- No SDK API that exposes `chatgpt_share_worker`, `wechat_article_worker`, or other internal worker
  names as public selection knobs.
- No broad scraping framework or plugin marketplace.

## Pre-Phase Baseline

- Generic URL ingest routes through Firecrawl, Crawl4AI, and Playwright fallback.
- Login-required and restricted domains route to `playwright_profile_worker` and then
  `playwright_worker`.
- Browser capture JSON and ChatGPT export archives already produce conversation Markdown.
- Quality checks catch some generic URL failures, auth walls, short output, and blocked responses.
- Platform-specific share pages such as ChatGPT, Gemini, WeChat, and YouTube did not yet have
  dedicated extraction schemas or quality gates before this phase.

## Current Risk Mitigations

- Platform article extraction now supports ID selectors, CSS selectors, meta tags, and structured
  JSON/JSON-LD fallbacks for WeChat, but live DOM drift remains a network smoke risk.
- YouTube transcript extraction supports both XML timedtext and JSON3 caption payloads. It still does
  not download media or run ASR by default.
- Focused no-network fixtures now cover WeChat article extraction, YouTube XML captions, YouTube
  JSON3 captions, and YouTube no-caption failure.
- LLM share fixtures now cover ChatGPT direct messages, ChatGPT mapping hydration, Gemini structured
  messages, Claude DOM messages, Claude/Gemini/DeepSeek visible-text snapshots, DeepSeek structured
  messages, and empty-shell failure.
- LLM share routes do not fall through to generic Playwright after platform/profile failure; generic
  browser output must not satisfy platform-required `message_count` gates.
- `playwright_profile_worker` can emit platform extraction for LLM share URLs when a user-local
  profile renders visible shared messages; challenge/auth shells still fail with explicit platform
  errors.
- Platform failure classification now distinguishes auth walls, deleted/unavailable content, rate
  limits, region blocks, and empty shells. Workers return explicit error codes such as
  `PLATFORM_AUTH_REQUIRED`, `PLATFORM_CONTENT_UNAVAILABLE`, `PLATFORM_RATE_LIMITED`,
  `PLATFORM_REGION_BLOCKED`, and `PLATFORM_EMPTY_SHELL`.
- `youtube_asr_worker` exists as a default-disabled policy gate. It refuses to run without explicit
  media-download consent and still does not download or transcribe media in this phase.

## Design Principles

- Classify URLs narrowly, but do not overclaim semantics. URL kind identifies routing; extraction
  fields record only content actually observed by the worker.
- Separate rendering/fetching from parsing. Parsers must be testable from deterministic fixture HTML
  or JSON without Playwright.
- Prefer structured platform extraction JSON as the canonical intermediate artifact. Markdown is
  rendered from that structured extraction.
- Treat login walls, deleted pages, empty shells, missing captions, and bot challenges as first-class
  failed or partial states.
- Keep generic web fallback available, but do not let generic page text count as platform success
  when the platform requires messages, article body, post content, or transcript segments.

## Target Interfaces

### URL Classification

Replace the current minimal URL classification object with a typed shape:

```python
@dataclass(frozen=True)
class UrlClassification:
    kind: UrlKind
    platform: str | None
    source_url: str
    normalized_url: str
    confidence: float
    needs_redirect_resolution: bool = False
    needs_browser_profile: bool = False
```

Initial URL kinds:

```python
class UrlKind(str, Enum):
    CHATGPT_SHARE = "chatgpt_share"
    GEMINI_SHARE = "gemini_share"
    CLAUDE_SHARE = "claude_share"
    DEEPSEEK_SHARE = "deepseek_share"
    WECHAT_ARTICLE = "wechat_article"
    YOUTUBE_VIDEO = "youtube_video"
    SHORT_URL = "short_url"
    DYNAMIC_WEB = "dynamic_web"
    LOGIN_REQUIRED_WEB = "login_required_web"
    RESTRICTED_WEB = "restricted_web"
    GENERIC_WEB = "generic_web"
```

The classifier starts with no-network rules. Redirect resolution is optional and must preserve both
`source_url` and `final_url`.

Known patterns for P0 tests:

| Kind | Candidate patterns |
| --- | --- |
| `chatgpt_share` | `chatgpt.com/share/*`, `chat.openai.com/share/*` |
| `gemini_share` | `gemini.google.com/share/*`, `g.co/gemini/share/*` |
| `claude_share` | Platform-specific share URL patterns, to confirm with fixtures before implementation |
| `deepseek_share` | Platform-specific share URL patterns, to confirm with fixtures before implementation |
| `wechat_article` | `mp.weixin.qq.com/s/*`, `mp.weixin.qq.com/s?__biz=...` |
| `youtube_video` | `youtube.com/watch?v=*`, `youtu.be/*`, `youtube.com/shorts/*` |
| `short_url` | Recognized unresolved redirectors |

### Platform Extraction Artifact

Every platform worker that succeeds or returns partial should write:

```json
{
  "schema_version": "platform_extraction.v1",
  "platform": "wechat",
  "url_kind": "wechat_article",
  "source_url": "https://...",
  "final_url": "https://...",
  "canonical_url": "https://...",
  "title": "Title",
  "authors": ["Account or author"],
  "published_at": "2026-06-07T00:00:00+08:00",
  "language": "zh-CN",
    "content": {
      "type": "article",
      "text_char_count": 12345,
      "message_count": null,
      "subtitle_segment_count": null
    },
  "assets": [
    {"type": "image", "url": "https://...", "role": "cover"}
  ],
  "extraction": {
    "worker": "wechat_article_worker",
    "method": "playwright_dom",
    "auth_mode": "none",
    "quality_flags": []
  }
}
```

Every successful or partial job still writes the standard Swallow artifacts: `document.md`,
`ingest_document.json`, `trace.jsonl`, and `manifest.json`. Platform workers additionally write
`intermediate/<platform>/platform_extraction.json` and, when browser rendering was used,
`intermediate/<platform>/rendered.html`. Screenshots should default to failure/partial-only or be
controlled by config; do not make screenshots unconditional for successful runs.

### Quality Rules

Extend the existing quality layer with platform-aware checks. A separate worker is optional; the first
implementation may keep the current `quality_checker` name and make it platform-aware through
`WorkerResult.metadata`.

Minimum quality signals:

| Kind | Required success signal | Reject or partial markers |
| --- | --- | --- |
| `chatgpt_share` | `message_count >= 1` | login wall, deleted/unavailable conversation, empty shell |
| `gemini_share` | `message_count >= 1` | sign-in-only page, missing shared chat, empty shell |
| `claude_share` | `message_count >= 1` | sign-in wall, unavailable share |
| `deepseek_share` | `message_count >= 1` | sign-in wall, unavailable share |
| `wechat_article` | title and article text body | deleted article, open-in-WeChat-only shell, complaint/blocked page |
| `youtube_video` | transcript or subtitle segments | title/description-only page, no captions |

YouTube-specific rule: page title and description alone are not a successful video transcript ingest.
The worker must produce transcript segments, return partial metadata, or fail with a clear reason.

### Trace Events

Existing `worker_started`, `worker_finished`, `quality_checked`, and `document_written` events remain.
Add or enrich events so platform decisions are auditable:

```json
{"event": "url_classified", "kind": "gemini_share", "confidence": 1.0}
{"event": "url_redirect_resolved", "source_url": "...", "final_url": "..."}
{"event": "platform_extracted", "platform": "gemini", "message_count": 8}
{"event": "fallback_selected", "from": "youtube_transcript_worker", "to": "playwright_worker", "reason": "no_caption_tracks"}
```

Do not duplicate large extraction payloads inside `trace.jsonl`; trace should point to artifact paths.

## Implementation Plan

### P0: Platform Plumbing

Scope:

- Add typed `UrlClassification` and expanded `UrlKind`.
- Add no-network classifier matrix for supported platform URL families.
- Add optional redirect-resolution hook, but keep the default regression tests no-network.
- Route URL kinds to platform pipelines while preserving generic URL fallback.
- Add platform extraction schema helpers in `src/swallow/workers/platform_common.py`.
- Add platform-aware quality checks for structured metadata.
- Add config controls for screenshots and optional URL media download flags.

Files likely touched:

- `src/swallow/detectors/url_classifier.py`
- `src/swallow/core/router.py`
- `src/swallow/core/runner.py`
- `src/swallow/core/quality.py`
- `src/swallow/core/config.py`
- `src/swallow/workers/platform_common.py`
- `tests/test_url_classifier.py`
- `tests/test_routing.py`
- `tests/test_quality.py`

Acceptance criteria:

- Unknown URLs still route to the generic web pipeline.
- Platform URLs route to specific pipelines without exposing worker selection through SDK/provider APIs.
- `g.co/gemini/share/*` classifies as `gemini_share` and marks redirect resolution as needed.
- Platform quality checks reject login-wall and empty-shell fixtures.
- No new runtime dependency is required for the default test gate.

Focused tests:

```bash
uv run --no-sync pytest -q tests/test_url_classifier.py tests/test_routing.py tests/test_quality.py
```

### P1: Gemini Share And WeChat Article

Scope:

- Implement `gemini_share_worker`.
- Implement `wechat_article_worker`.
- Parse structured extraction JSON and render Markdown from extraction data.
- Preserve rendered HTML and platform extraction artifacts.
- Keep parsers fixture-testable without Playwright.

Gemini extraction:

- Support `gemini.google.com/share/*`.
- Support `g.co/gemini/share/*` through redirect-aware classification.
- Preserve share snapshot metadata when visible.
- Detect artifacts such as canvas/image/video as metadata assets rather than forcing binary download.

WeChat extraction:

- Use rendered HTML when needed.
- Prefer selectors such as `#activity-name`, `#js_name`, and `#js_content`, with metadata fallbacks from
  scripts and meta tags.
- Normalize lazy-loaded images by promoting `data-src` or `data-original` to Markdown image URLs.
- Detect deleted, blocked, complaint, open-in-client-only, and empty article states.

Files likely touched:

- `src/swallow/workers/conversation_share_worker.py`
- `src/swallow/workers/platform_article_worker.py`
- `src/swallow/workers/platform_common.py`
- `src/swallow/core/registry.py`
- `tests/test_conversation_share_worker.py`
- `tests/test_platform_article_worker.py`
- `tests/fixtures/web/gemini_share_success.html`
- `tests/fixtures/web/wechat_article_success.html`
- `tests/fixtures/web/wechat_article_deleted.html`

Acceptance criteria:

- Fixture success pages produce `document.md`, `platform_extraction.json`, and stable Markdown.
- Deleted or login-wall fixtures fail or return partial; they do not pass as successful articles.
- Generated Markdown includes source URL, title, visible author/account metadata, and content body.
- Default gate remains no-network.

Focused tests:

```bash
uv run --no-sync pytest -q tests/test_conversation_share_worker.py tests/test_platform_article_worker.py
```

### P2: ChatGPT Share

Scope:

- Implement `chatgpt_share_worker`.
- Route `chatgpt.com/share/*` and `chat.openai.com/share/*` to the share worker before login-profile
  fallback.
- Extract conversation snapshots without assuming the share always contains the full conversation.

Extraction strategy:

- Try hydration or embedded structured data first.
- Fall back to semantic DOM extraction.
- Fall back to visual text extraction only as partial evidence.
- Preserve role, order, code blocks, links, tables, and visible attachments/tool results when possible.
- Record `share_scope` as `conversation_snapshot`, `assistant_response`, or `unknown`.

Files likely touched:

- `src/swallow/workers/conversation_share_worker.py`
- `src/swallow/core/router.py`
- `src/swallow/core/registry.py`
- `tests/test_conversation_share_worker.py`
- `tests/fixtures/web/chatgpt_share_success.html`
- `tests/fixtures/web/chatgpt_share_response_scoped.html`
- `tests/fixtures/web/chatgpt_share_deleted.html`

Acceptance criteria:

- A public share fixture produces message-based Markdown and `message_count >= 1`.
- Deleted/unavailable/share-login fixtures fail or return partial with explicit error metadata.
- ChatGPT share URLs no longer default directly to `playwright_profile_worker`.

### P2.5: Claude And DeepSeek Share

Scope:

- Implement separate `claude_share_worker` and `deepseek_share_worker` only after collecting stable
  share fixtures.
- Reuse conversation extraction schema and Markdown rendering conventions from ChatGPT/Gemini.
- Keep platform-specific selectors and hydration parsing isolated per worker.

Likely files: `src/swallow/workers/conversation_share_worker.py`,
`tests/test_conversation_share_worker.py`, and matching fixture HTML files.

Acceptance criteria:

- Each worker can parse fixture HTML without Playwright.
- Fixture shares produce conversation Markdown and structured extraction artifacts.
- Unknown or unstable URL shapes remain generic or login-required until confirmed by tests.

### P3: YouTube Transcript

Scope:

- Implement `youtube_transcript_worker`.
- Parse video IDs from `youtube.com/watch`, `youtu.be`, and `youtube.com/shorts`.
- Discover and fetch available timed text/transcript segments where available.
- Do not download media or run ASR by default.

Optional future fallback:

- `youtube_asr_worker` may be added only behind explicit config and ASR extra.
- Audio/video download requires separate approval and must not be added to `web` extra by default.
- Current implementation includes the default-disabled worker and policy gate only; media download
  and ASR execution remain unimplemented pending explicit approval.

Likely files: `src/swallow/workers/youtube_transcript_worker.py`,
`tests/test_youtube_transcript_worker.py`, and caption/no-caption fixtures.

Acceptance criteria:

- Caption fixture produces timestamped Markdown and `subtitle_segment_count > 0`.
- No-caption fixture fails or returns partial metadata with `failed_no_transcript`.
- Title/description-only generic page text is not accepted as transcript success.

## Testing Matrix

Default no-network tests:

- URL classification matrix for every supported platform and recognized short-link family.
- Parser-only tests from fixture HTML/JSON for every platform worker.
- Worker tests with mocked render/fetch helpers.
- Router tests for URL kind to pipeline order.
- Quality tests for success, partial, login-wall, deleted, blocked, and empty-shell states.
- SDK/provider tests proving URL ingest remains the public surface and internal workers are not exposed
  as selectable capabilities.

Optional network/runtime tests:

- Do not add live platform URL tests to the repository default or optional pytest gates.
- Capture manual live smoke evidence in closeout/status notes when needed.
- Run Playwright smoke only when `web` extra and browser runtime are installed.
- Run YouTube transcript smoke only for videos with stable public captions.

Release gate for each implementation slice:

```bash
uv run --no-sync pytest -q -m "not network and not performance and not heavy"
uv run --no-sync python -m compileall -q src tests scripts
```

Focused gates should be added to `docs/testing.md` only after the tests exist.

## Documentation Updates Required During Implementation

- Update `docs/architecture.md` when platform routing is implemented.
- Update `docs/development.md` when new worker config keys or optional extras are added.
- Update `docs/api.md` only if public CLI/service/SDK/provider surfaces change.
- Update `docs/testing.md` when focused test commands change.
- Update `docs/contracts/ingest-contract.md` only if artifact or failure contracts change.
- Add an ADR before introducing default media download or auth-token based platform APIs.

## Closeout Requirements

For each completed slice:

- Record what shipped and what remains partial/unsupported.
- List changed worker capabilities and optional dependency requirements.
- List default gates and focused gates run.
- Include fixture coverage and any manual live smoke evidence.
- State known platform risks such as DOM drift, auth walls, deleted shares, rate limits, and regional
  blocking.

## External Facts To Reverify Before Implementation

- ChatGPT shared links are documented as `https://chatgpt.com/share/<conversation-ID>` and may be
  full conversation snapshots or response-scoped shares:
  <https://help.openai.com/en/articles/7925741-chatgpt-shared-links>
- Gemini public chat links may use `g.co/gemini/share/...`:
  <https://support.google.com/gemini/answer/13743730?hl=en-GB>
- YouTube Data API caption download requires authorization and edit permission, so it is not a
  default route for arbitrary public video transcript ingest:
  <https://developers.google.com/youtube/v3/docs/captions/download>
