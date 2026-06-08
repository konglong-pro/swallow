# Project Status

Last updated: 2026-06-08

## Current Label

Swallow is an ingest-only toolkit with a locally passing SDK final acceptance gate. Capability
Provider P1 is completed in this working tree as a thin provider adapter over existing ingest
capabilities. Platform URL Ingest is also completed in this working tree. A versioned release
artifact or tag has not been cut.

## Shipped And Frozen Baseline

- Raw inputs are stored immutably under `raw_store/<sha256>/`.
- Successful jobs write `document.md`, `ingest_document.json`, `trace.jsonl`, `job.json`, and
  `manifest.json`.
- Built-in workers cover plain text, MarkItDown-backed file conversion, PaddleOCR OCR,
  faster-whisper ASR, Firecrawl/Crawl4AI/Playwright URL ingest, browser capture JSON, local
  Playwright profile capture, and ChatGPT export archives.
- CLI surfaces cover file, URL, browser capture, archive, batch, rerun, job inspection, raw metadata,
  artifacts, trace, workers, doctor, queue, and MCP commands.
- FastAPI service exposes async-first `/v1` ingest, job, result, document, manifest, and trace routes.
- SDK clients cover local, CLI, HTTP, queue, and the hybrid Python facade.
- Capability Provider P1 covers discovery, planning, doctor, job/batch lifecycle, bounded artifacts,
  cancellation, profiles, policies, and generated manifest/schema artifacts.
- MCP adapter exposes tools only, backed by `SwallowCapabilityProvider`, with URL ingest disabled by
  default.
- Platform URL Ingest covers ChatGPT, Gemini, Claude, and DeepSeek share URLs; WeChat public article
  URLs; YouTube caption transcript extraction; and an opt-in, default-disabled YouTube ASR policy
  gate. Zhihu, X, and Xiaohongshu have no dedicated platform workers and route as generic web.

## Active

- No active implementation phase is currently approved after Platform URL Ingest closeout.
- Keep SDK/provider surfaces as URL ingest; core routing chooses internal platform workers.
- Keep backend and worker choices behind provider profiles and policy for follow-up work.
- Keep HTTP out of the local-first default path and MCP out of provider backends.

## Next But Not Approved

- Cut a versioned release artifact or tag.
- Decide whether to publish a package version immediately or keep the current repository as a
  verified source-tree acceptance point.
- Decide longer-term retention policy for reserved `ephemeral` mode.
- Decide non-local HTTP authentication/authorization policy before treating the service as a remote
  deployment target.

## Evidence

- `docs/capability-provider-p1.md`: canonical Capability Provider P1 execution plan.
- `docs/closeout/capability-provider-p1-closeout.md`: Capability Provider P1 closeout and local gate
  evidence.
- `docs/adr/0001-swallow-capability-provider-boundary.md`: accepted provider boundary decision.
- `docs/sdk-final-acceptance.md`: current SDK acceptance checklist and latest local gate results.
- `docs/sdk-agent-project-plan.md`: SDK implementation plan, accepted decisions, and open decisions.
- `docs/closeout/platform-url-ingest-closeout.md`: Platform URL Ingest closeout and local gate
  evidence.
- `docs/planning/archive/platform-url-ingest.md`: archived execution plan for platform-aware URL
  ingest.
- `docs/final-qa-2026-05-17.md`: local Windows/Python 3.11 QA evidence for the ingest-core release
  candidate.
- `.github/workflows/ci.yml`: cross-platform default test matrix, Docker smoke, optional performance,
  and optional Firecrawl real API workflow dispatch jobs.

## Known Conditional Gates

- Performance thresholds are opt-in through `RUN_PERFORMANCE_TESTS=1`.
- OCR/ASR performance checks are additionally gated by `RUN_OCR_TESTS=1` and `RUN_ASR_TESTS=1`.
- Firecrawl real API tests require `RUN_FIRECRAWL_TESTS=1` and `FIRECRAWL_API_KEY`.
- Docker smoke is configured in CI; it was not executed in the 2026-05-17 local Windows QA run because
  Docker CLI was not installed on that machine.
