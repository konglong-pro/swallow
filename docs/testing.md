# Testing

## Default Local Gate

Run this before handing off ordinary code changes:

```bash
uv run --no-sync pytest -q -m "not network and not performance and not heavy"
uv run --no-sync python -m compileall -q src tests scripts
```

## Focused Marker Gates

```bash
uv run --no-sync pytest -q -m batch
uv run --no-sync pytest -q -m api
uv run --no-sync pytest -q -m security
uv run --no-sync pytest -q -m concurrency
uv run --no-sync pytest -q -m integration
```

SDK-focused gates:

```bash
uv run pytest -m contract -q
uv run pytest -m transport -q
uv run pytest -m security -q
uv run pytest -m adapter -q
uv run pytest -m concurrency -q
```

## SDK Release Gate

```bash
uv run python scripts/sdk_release_gate.py
```

This gate verifies contract, transport, security, adapter, concurrency, and the full default suite.
The latest local accepted results are recorded in `docs/sdk-final-acceptance.md`.

## Optional Heavy And Network Gates

Performance thresholds:

```powershell
$env:RUN_PERFORMANCE_TESTS = "1"
uv run --no-sync pytest -q -m performance
```

OCR and ASR performance checks:

```powershell
$env:RUN_PERFORMANCE_TESTS = "1"
$env:RUN_OCR_TESTS = "1"
$env:RUN_ASR_TESTS = "1"
uv run --no-sync pytest -q -m performance
```

Firecrawl real API:

```powershell
$env:RUN_FIRECRAWL_TESTS = "1"
$env:FIRECRAWL_API_KEY = "..."
uv run --no-sync pytest -q -m network
```

Without those variables, network and heavy performance checks should skip explicitly rather than fail
the default gate.

## Runtime Smoke

See `docs/development.md` for runtime smoke script usage. The full local runtime smoke entry point is:

```bash
uv run --no-sync python scripts/runtime_smoke_all.py
```

## CI

GitHub Actions lives in `.github/workflows/ci.yml` and includes:

- Windows, Linux, and macOS default test matrix on Python 3.11.
- Docker image build and smoke run.
- Workflow-dispatch performance job.
- Workflow-dispatch Firecrawl real API job when `FIRECRAWL_API_KEY` is configured.

CI default commands:

```bash
uv sync --dev --extra ci
uv run python scripts/generate_capability_artifacts.py --check
uv run pytest -q -m "not network and not performance and not heavy"
uv run python -m compileall -q src tests scripts
```

## Reporting Rule

Do not claim a check passed unless it was run in the current session. If optional gates were skipped,
state the reason, such as missing heavy dependencies, missing environment variables, or not needing a
runtime worker for the change.
