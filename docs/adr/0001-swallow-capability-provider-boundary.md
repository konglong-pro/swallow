# Swallow Capability Provider Boundary

Swallow's Capability Provider is a thin adapter over ingest capabilities, not a generic agent runtime
or a new core backend model. Agents request Swallow ingest capabilities while provider profiles choose
local, CLI, HTTP, or queue execution; MCP remains an external adapter, HTTP is not the local-first
default, and provider outputs are conversion candidates rather than host truth. This preserves
Swallow's ingest-only boundary while giving agent runtimes one stable provider-shaped interface.
