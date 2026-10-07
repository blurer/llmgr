# Architecture Decisions

## ADR-001: Flatten repo, config-driven hosts, stats folder, uv-only
**Date:** 2026-10-03
**Status:** Accepted

### Context
The tool lived in a nested `llm-manager/` folder with hosts hardcoded in `main.py` and stats written to `/home/bl/models/usage_stats.db`. Hardcoded hosts forced code edits for every host/alias change, and the layout made `uv` invocation awkward.

### Decision
- Flatten to the repo root; drop the `llm-manager/` folder.
- Hosts live in `host.json`; `main.py` validates and loads it at startup, deriving CLI host choices and `both` order from it.
- Stats db + usage log live in `stats/` next to the code.
- `pyproject.toml` exposes `llm = "main:main"`; the tool is invoked only via `uv run llm ...`.

### Consequences
- Existing stats history must be migrated manually into `stats/` on the deployment host.
- Host changes require no code edits or redeploys beyond editing `host.json`.
