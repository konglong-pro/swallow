# CONTEXT.md

## Project

Swallow is an ingest-only toolkit that turns raw inputs into traceable Markdown artifacts.

## Glossary Index

Detailed Capability Provider terms live in `docs/glossary/capability-provider.md`.

## Core Terms

**Capability Provider**: A thin Swallow adapter layer that registers, describes, validates, executes,
tracks, and exposes artifacts for Swallow ingest capabilities.
_Avoid_: using this term for internal workers, agent orchestration, or cross-project framework code.

**Ingest Capability**: A provider-declared Swallow action, such as file, URL, browser capture,
archive, or batch ingest.
_Avoid_: exposing internal workers as ingest capabilities.

**Artifact Reference**: A provider-scoped opaque reference to a Swallow artifact. Agents may store and
pass it back to the provider, but must not parse it as a filesystem path.
_Avoid_: treating `swallow://...` references as direct local paths.

**Host Truth**: A trusted revision or source of record owned by a host system outside Swallow.
Provider outputs are never host truth.
_Avoid_: treating Swallow `document.md` or other provider artifacts as trusted host revisions.

**Provider Profile**: A named provider execution policy that selects allowed source types, backend
order, network allowance, heavy-runtime allowance, and size limits.
_Avoid_: making agents choose raw SDK backends directly.

**MCP Adapter**: An external ecosystem adapter for Swallow tools. It wraps the Capability Provider,
but it is not a provider backend.
_Avoid_: routing provider execution through MCP.
