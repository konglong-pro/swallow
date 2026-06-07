# Current Active Work

Last updated: 2026-06-07
Source of current phase: `docs/phase-manifest.yaml`

## Current State

- Shipped/frozen: ingest core, built-in workers, CLI, FastAPI service, SDK clients, queue backend,
  hybrid facade, provider-backed MCP adapter, and Capability Provider P1 are implemented in this
  working tree.
- Active: no approved implementation phase after Capability Provider P1 closeout.
- Next but not approved: versioned release artifact/tag and package publishing decision.

## Required Reading For Current Work

- `docs/capability-provider-p1.md`: completed execution plan for Capability Provider P1.
- `docs/closeout/capability-provider-p1-closeout.md`: closeout evidence for Capability Provider P1.
- `docs/contracts/ingest-contract.md`: durable scope, artifact, SDK, trace, and error rules.
- `docs/adr/0001-swallow-capability-provider-boundary.md`: accepted provider boundary decision.
- `docs/project-status.md`: compressed shipped/current/open status.
- `docs/sdk-final-acceptance.md`: SDK release gate and accepted final decisions.
- `docs/sdk-agent-project-plan.md`: detailed SDK implementation history and remaining open decisions.
- `docs/testing.md`: executable gates and optional heavy/network checks.

## Explicitly Out Of Scope

- Agent framework behavior in core Swallow.
- A public cross-repository capability framework.
- Direct Agent selection of Swallow SDK backends or internal workers.
- MCP as a Capability Provider backend.
- HTTP as the default local-first provider path.
- Treating provider artifacts as host truth.
- RAG, vector databases, embeddings, memory, chat UI, or knowledge-base management.
- Summarization, translation, rewriting, or LLM cleanup during ingest.
- Default chunking that replaces `document.md`.
- Returning large Markdown bodies by default through SDK or adapters.

## Current Gates

- `uv run --no-sync pytest -q -m "not network and not performance and not heavy"`: default local CI gate.
- `uv run --no-sync python scripts/generate_capability_artifacts.py --check`: provider artifact drift gate.
- `uv run --no-sync python -m compileall -q src tests scripts`: syntax/import compile gate.
- `uv run python scripts/sdk_release_gate.py`: SDK final acceptance gate.
- `uv run --no-sync python scripts/runtime_smoke_all.py`: optional runtime worker smoke suite.
- Documentation-only provider planning edits: verify Markdown links, parse `docs/phase-manifest.yaml`,
  and run `git diff --check`.

## Notes For Implementation Agents

- Resolve current scope from `docs/phase-manifest.yaml`, not from old phase wording in README history.
- Treat `docs/capability-provider-p1.md` as the canonical spec for Capability Provider P1.
- Treat `docs/sdk-final-acceptance.md` as the source of truth for SDK release gates.
- Treat `docs/sdk-agent-project-plan.md` as the detailed implementation plan/history, not as a place
  to broaden product scope.
- Keep root `README.md` and `AGENTS.md` compact; add deeper detail to the appropriate `docs/` file.
