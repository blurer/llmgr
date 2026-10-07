# Changelog

## [Unreleased]
### Fixed
- `stats` PP/TG display: the server's throughput gauges are rolling averages that reset to `0` when idle, which used to render as "no data yet"; zero now renders as `0.0 (idle)` and "no data yet" only when the gauge key is absent

### Added
- `temp_cmd` host config: `status`/`stats` show GPU temp (°C) via a per-host command (nvidia-smi default on titan, hwmon on strix); included in JSON output as `temp_c`
- Session-average PP/TG (`avg`) computed from the cumulative `prompt_seconds_total` / `tokens_predicted_seconds_total` counters — stable unlike the decaying rolling gauges
- `msg` action: send a chat message to a running instance and print the reply with token/throughput info (`llm <host> msg <message>` or `llm msg <host> <message>`, `--json` supported)
- README shell-alias section: `llm` alias for `uv run --directory ~/dev/llmgr main.py`
- Test suite (`tests/`, pytest) covering config loading, host management, the model menu/favorites, model resolution, and stats lifetime computation
- Ruff lint config and dev dependency group (`uv run ruff check .`); ruff and pytest pinned under `[dependency-groups] dev`
- Per-host `favorites` in `host.json`: listed first in the interactive model menu, with inline `+N`/`-N` add/remove from the menu
- `host` subcommand group for managing `host.json` from the CLI: `list`, `show`, `add` (interactive), `remove [--yes]`, `set`, `unset`
- `host.json` for all host configuration
- `pyproject.toml` with `llm` script entry point

### Changed
- Project flattened: `llm-manager/` folder removed, code now lives at the repo root
- Host definitions moved from hardcoded `HOSTS` dict in `main.py` to `host.json` (host names, aliases, and `both` order all derive from it)
- Stats db relocated to `stats/usage_stats.db` (was `/home/bl/models/usage_stats.db`); legacy CSV import now looks in `stats/`
- Tool now runs only via `uv` (`uv run llm ...`), with a console entry point defined in `pyproject.toml`
