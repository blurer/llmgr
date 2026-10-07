# AGENTS.md

Guidance for AI coding agents working in this repository.

## Project Overview

Single-file Python CLI tool (`main.py`) for managing `llama-server` (llama.cpp) instances across GPU hosts. Hosts are configured in `host.json`, not code.

## Usage

Only run via `uv`:

```bash
# CLI: <action> [host] [model]
uv run llm <action> [host] [model]
# actions: start, stop, restart, status, stats, msg
# msg: llm <host> msg <message...> or llm msg <host> <message...> — chat smoke test

# Examples — recommended zsh alias: alias llm='uv run --directory ~/dev/llmgr main.py'
llm start <host> <alias>              # alias hit
llm start <host> <partial query>      # token match on filename
llm start <host>                      # interactive menu of every GGUF in model_dir
llm start                             # all hosts, each uses its default_model
llm stats                             # token counts + lifetime, all hosts
```

There is no build step. Tests and lint run via `uv` (dev deps auto-installed):

```bash
uv run pytest        # tests in tests/ (testpaths configured in pyproject.toml)
uv run ruff check .  # lint; rule set in [tool.ruff.lint] (defaults + isort)
```

## Configuration

`host.json` at the repo root defines all hosts (gitignored; see
`host.example.json` for the shape). Each top-level key is the CLI host name
(`both` targets every key in file order). Per-host fields: `name`, `addr`,
`ssh_host` (remote hosts resolve `addr` via `ssh -G ~/.ssh/config`),
`model_dir`, `default_model`, `aliases`, `favorites` (GGUF filenames shown
first in the interactive menu), `server`, `port`, `remote`, `extra_args`,
`model_args` (per-GGUF flag overrides), `fan_script`.

The `host` subcommand edits `host.json` from the CLI: `list`, `show`, `add`
(interactive), `remove [--yes]`, `set <field> <value>`, `unset <field>`.
`set` parses the value as JSON when valid (so `aliases`/`model_args` take
JSON objects); unknown fields are rejected against `HOST_FIELDS`. Host
commands do not touch servers or the stats db.

Adding a host or alias means editing `host.json` (or using the `host`
subcommand) — no code edit.

## Model Selection

Models are discovered at runtime by listing `*.gguf` in each host's `model_dir`. Drop a new GGUF in the directory and it's selectable immediately — no config edit needed.

Resolution order for the `model` positional:
1. Exact alias key in the host's `aliases`
2. Exact filename or stem (case-insensitive)
3. Token match: every alphanumeric run in the query must appear (in any order) in the normalized filename. Ignores case and punctuation.

Ambiguous queries print all matching stems; missing queries print the alias keys plus every discovered stem.

For `start`/`restart` against a single host with no model arg and no `--json`, an interactive menu lists the host's `favorites` first, then all GGUFs in the dir. Inside the menu: bare `N` selects, `+N`/`-N` toggle favorites (persisted to `host.json` immediately); stale favorites (file gone) are pruned.

The `model` positional is rejected when host is `both` — pick one host.

## Architecture

All logic lives in `main.py`. `load_hosts()` reads `host.json`; everything else takes a resolved host dict.

**Execution model:** Local hosts use `subprocess.run()`; remote hosts use `ssh_run()` which shells out to SSH.

**Server management:** llama-server starts via `nohup` with `-ngl 99 --flash-attn on --metrics --jinja --host 0.0.0.0`. Health checked by polling `/health`.

**Metrics:** Fetches Prometheus metrics from `/metrics`. Token counters: `llamacpp:prompt_tokens_total`, `llamacpp:tokens_predicted_total`. PP/TG t/s from `llamacpp:prompt_tokens_seconds` / `llamacpp:predicted_tokens_seconds` gauges — rolling averages on current llama.cpp builds that reset to `0` shortly after the last request, so `stats` renders `0.0 (idle)` ("no data yet" means the gauge key is absent). The `avg` figures are session averages from the cumulative `llamacpp:prompt_seconds_total` / `llamacpp:tokens_predicted_seconds_total` counters (tokens ÷ seconds) and don't decay. GPU temp comes from the host's `temp_cmd` (first number in stdout), best-effort.

**Persistent stats:** Every CLI invocation against an up server appends a row to SQLite (`stats/usage_stats.db`, table `samples`) capturing cumulative `prompt_tokens`/`gen_tokens` plus uptime/model. The `stats/` folder holds the tg stats db and the usage log. Lifetime totals are derived by detecting session boundaries (uptime advance lagging wall-clock gap). `stats` shows current session + lifetime across all detected sessions.

## Code Style

Procedural — flat functions, no classes. Snake_case throughout.

## Behavioral Guidelines

These guidelines bias toward caution over speed. For trivial tasks, use judgment.

### 1. Think Before Coding

Don't assume. Don't hide confusion. Surface tradeoffs.

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them — don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### 2. Simplicity First

Minimum code that solves the problem. Nothing speculative.

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

### 3. Surgical Changes

Touch only what you must. Clean up only your own mess.

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it — don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that *your* changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: every changed line should trace directly to the user's request.

### 4. Goal-Driven Execution

Define success criteria. Loop until verified.

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan with a verification step per item. Strong success criteria let you loop independently; weak criteria ("make it work") require constant clarification.
