# Capability Provider P1 Closeout

## What Shipped

- `SwallowCapabilityProvider` as a thin adapter over existing Swallow SDK clients.
- Provider discovery, doctor, planning, single-job submit/get/wait/result/artifact/cancel lifecycle.
- Queue-backed batch lifecycle through `submit_batch`, `get_batch`, `get_batch_result`,
  `wait_batch`, and `cancel_batch`.
- Provider profiles that keep backend selection behind policy:
  - default `local_isolated` for CLI-isolated local ingest;
  - `local_light` for local in-process file ingest;
  - `service` for explicit HTTP service use;
  - `heavy_queue` for queue-backed heavy and batch work.
- Provider artifact references for job, raw, and batch evidence.
- Provider-backed MCP adapter that keeps existing single-job MCP tool result shapes compatible while
  exposing provider-shaped batch and management tools.
- Generated provider artifacts:
  - `schemas/capability-manifest.schema.json`
  - `schemas/capability-result.schema.json`
  - `schemas/artifact-ref.schema.json`
  - `swallow.capabilities.json`

## Frozen Behavior

- Provider outputs are conversion candidates and evidence, never host-trusted revisions.
- Agents request ingest capabilities; they do not select SDK backends or internal workers.
- MCP is an adapter over the provider, not a provider backend.
- HTTP is not the local-first default path.
- `swallow.ingest.batch` uses `batch_id` and batch lifecycle methods, not ordinary `job_id` APIs.
- Full content, raw artifact reads, and local path exposure require explicit provider policy.

## Contracts Created Or Updated

- `docs/contracts/ingest-contract.md`
- `docs/adr/0001-swallow-capability-provider-boundary.md`
- `docs/glossary/capability-provider.md`
- `docs/api.md`
- `AGENTS.md`
- `CONTEXT.md`

## Tests And Gates

Local gates run on 2026-06-07:

- `uv run --no-sync pytest tests/test_capability_provider.py -q`: 11 passed.
- `uv run --no-sync pytest tests/test_mcp_server.py -q`: 10 passed, 1 skipped.
- `uv run --no-sync pytest -m contract -q`: 35 passed, 1 skipped, 224 deselected.
- `uv run --no-sync pytest -m adapter -q`: 10 passed, 2 skipped, 248 deselected.
- `uv run --no-sync pytest -q -m "not network and not performance and not heavy"`: 256 passed,
  2 skipped, 2 deselected.
- `uv run --no-sync python -m compileall -q src tests scripts`: passed.
- `uv run --no-sync python scripts/generate_capability_artifacts.py --check`: passed.
- `uv run --no-sync python -c "<phase-manifest yaml parse>"`: passed.
- Markdown local link check: passed.
- `git diff --check`: passed with CRLF working-copy warnings only.

## Evidence Retained

- Provider implementation: `src/swallow/capability/`.
- MCP adapter implementation: `src/swallow/mcp/server.py`.
- Provider conformance tests: `tests/test_capability_provider.py`.
- MCP adapter tests: `tests/test_mcp_server.py`.
- CI drift check: `.github/workflows/ci.yml`.

## Known Limitations

- Cross-repository public capability framework extraction remains out of scope.
- Provider profile configuration through `swallow.config.yaml` remains future work.
- Remote HTTP authentication and authorization policy remains future work.
- Provider-owned trace artifacts remain deferred; P1 references Swallow job and batch trace files.
- A versioned release artifact or package publish has not been cut in this working tree.
