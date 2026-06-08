# Platform URL Ingest Closeout

## What Shipped

- Platform-aware URL classification and routing for:
  - ChatGPT, Gemini, Claude, and DeepSeek share URLs.
  - WeChat public article URLs.
  - YouTube video caption transcript extraction.
- Structured platform extraction artifacts under `intermediate/<platform>/platform_extraction.json`
  with `platform_extraction.v1` metadata rendered into `document.md`.
- Platform quality gates for required content:
  - LLM shares require `message_count >= 1`.
  - WeChat articles require article body text.
  - YouTube videos require subtitle or ASR transcript segments.
- Explicit platform failure states for auth walls, deleted/unavailable content, rate limits, region
  blocks, empty shells, missing messages, short content, and no transcript.
- `playwright_profile_worker` can emit platform extraction for LLM share URLs when a user-local
  browser profile renders visible shared messages.
- `youtube_asr_worker` exists as a default-disabled opt-in policy gate. It refuses to run unless
  media download consent is explicit.

## Frozen Behavior

- SDK, provider, CLI, and service callers continue to use generic URL ingest. They do not select
  internal platform workers directly.
- LLM share routes do not fall through to generic Playwright after platform/profile failure. Generic
  browser output cannot satisfy platform-required `message_count` gates.
- YouTube title and description alone are not a successful transcript ingest.
- Swallow does not bypass platform access controls. Login walls, bot challenges, rate limits, and
  region blocks fail with explicit platform errors.
- Zhihu, X, and Xiaohongshu have no dedicated platform workers in this closeout. They route as
  generic web URLs.
- Live platform URL checks are not retained as repository tests. Parser regression coverage uses
  deterministic fixtures.

## Contracts Or Docs Updated

- `docs/contracts/ingest-contract.md`: existing ingest-only and artifact rules remain authoritative.
- `docs/adr/0001-swallow-capability-provider-boundary.md`: existing provider boundary remains
  authoritative.
- `docs/architecture.md`: platform routing and worker behavior.
- `docs/development.md`: worker configuration and runtime notes.
- `docs/testing.md`: focused platform URL gates and removal of live platform smoke tests.
- `docs/planning/archive/platform-url-ingest.md`: completed phase plan.

## Tests And Gates

Local gates run on 2026-06-08:

- `uv run --no-sync pytest -q tests/test_url_classifier.py tests/test_routing.py tests/test_config.py tests/test_quality.py tests/test_platform_article_worker.py tests/test_conversation_share_worker.py tests/test_youtube_transcript_worker.py tests/test_youtube_asr_worker.py`: 85 passed, 1 skipped.
- `uv run --no-sync pytest -q tests/test_routing.py tests/test_web_workers.py tests/test_conversation_share_worker.py tests/test_quality.py`: 69 passed.
- `uv run --no-sync pytest -q tests/test_conversation_share_worker.py tests/test_web_workers.py`: 32 passed.
- `uv run --no-sync pytest -q -m "not network and not performance and not heavy"`: 317 passed,
  2 skipped, 3 deselected.
- `uv run --no-sync python -m compileall -q src tests scripts`: passed.
- `git diff --check`: passed with CRLF working-copy warnings only.

## Manual Live Evidence

Manual live URL checks were run during implementation, but no live platform test files remain in the
repository.

- DeepSeek share URL `https://chat.deepseek.com/share/c9s0gxkgallc76ep0y`: success through
  `deepseek_share_worker`, `message_count=2`, `text_char_count=1725`.
- Gemini share URL `https://gemini.google.com/share/cf12ac0991c4`: success through
  `gemini_share_worker`, `message_count=22`, `text_char_count=23016`.
- Claude share URL `https://claude.ai/share/a68cc4a1-5d1b-4987-a62a-25475628fda6`: parser succeeds
  against captured visible DOM, but current live access in this environment lands on a platform
  challenge page and returns `PLATFORM_BROWSER_RENDER_REQUIRED`.

## Evidence Retained

- URL classifier and routing tests: `tests/test_url_classifier.py`, `tests/test_routing.py`.
- Platform quality tests: `tests/test_quality.py`.
- LLM share worker tests and fixtures: `tests/test_conversation_share_worker.py`,
  `tests/fixtures/web/*_share_*.html`.
- WeChat article worker tests and fixtures: `tests/test_platform_article_worker.py`,
  `tests/fixtures/web/wechat_article_*.html`.
- YouTube transcript and ASR policy tests: `tests/test_youtube_transcript_worker.py`,
  `tests/test_youtube_asr_worker.py`, `tests/fixtures/web/youtube_*.html`.

## Known Limitations

- Platform DOM and hydration data can drift. Fixture updates are expected when public page structures
  change.
- Claude live share ingest can fail when Claude serves a challenge page to the local browser/profile.
- Public links that require login, are deleted, are rate-limited, or are region-blocked are not
  bypassed.
- YouTube videos without captions fail by default unless `youtube_asr_worker` is explicitly enabled
  with media-download consent.
- Versioned release artifacts and package publishing remain outside this closeout.
