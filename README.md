# llmgr

CLI for managing `llama-server` instances across multiple GPU hosts. Hosts are
configured in `host.json` — no code edits needed to add, remove, or re-point a host.

## Setup

Copy the example config and edit it for your hosts (`host.json` is personal
runtime state and is gitignored):

```bash
cp host.example.json host.json
```

## Usage

Prefer `uv`:

```bash
uv run llm <action> [host] [model]
```

`uv run main.py <action> ...` also works. Since `main.py` is stdlib-only
(no third-party deps) it can even be run directly as `./main.py <action> ...`
— but prefer `uv run` so the Python version stays pinned. Host is optional —
defaults to `both` (every host in `host.json`) when omitted.

### Shell alias

Add to `~/.zshrc` (adjust the path if the repo lives elsewhere):

```zsh
alias llm='uv run --directory ~/dev/llmgr main.py'
```

Then `source ~/.zshrc` and run from anywhere:

```bash
llm status
llm start strix
llm stats
```

### Actions

| Command | Description |
|---------|-------------|
| `start` | Start the server (skips if already running) |
| `stop` | Stop the server |
| `restart` | Stop then start the server |
| `status` | Check if the server is running |
| `stats` | Show token counts and PP/TG throughput |
| `msg` | Send a chat message to one host and show the reply (smoke test) |

### Examples

```bash
uv run llm status             # status of all hosts
uv run llm start titan        # start just titan (host default_model)
uv run llm start strix gemma  # start strix with an alias
uv run llm stop strix         # stop just strix
uv run llm restart            # restart both
uv run llm stats              # view stats (also samples into stats/)
```

### Quick chat test

Send a message to a running instance and see the reply plus token/throughput
info — handy for checking a server actually works. Both word orders work:

```bash
llm strix msg what is 2+2?     # instance first
llm msg strix "what is 2+2?"   # action first
llm msg titan hi --json        # machine-readable output
```

If the server is down, it exits with a hint instead of failing silently.

## Host Management

The `host` subcommand edits `host.json` directly:

| Command | Description |
|---------|-------------|
| `host list` | List configured hosts |
| `host show <name>` | Dump one host's full config as JSON |
| `host add <name>` | Add a host (interactive prompts, blank = default) |
| `host remove <name>` | Remove a host (`--yes` skips confirmation) |
| `host set <name> <field> <value>` | Set a field (value parsed as JSON when valid) |
| `host unset <name> <field>` | Remove a field |

```bash
uv run llm host add mini
uv run llm host set mini port 8088
uv run llm host set mini aliases '{"q4b": "Qwen3.5-4B-UD-Q4_K_XL.gguf"}'
uv run llm host unset mini fan_script
uv run llm host remove mini --yes
```

Structured fields (`aliases`, `model_args`) must be JSON objects; `favorites`
must be a JSON array; `port` must be an integer; anything else that isn't
valid JSON is stored as a plain string.

## Favorites

`start`/`restart` menus list each host's favorites first, then everything
else. Inside the menu:

- `+N` — add entry N to favorites (saved to `host.json` immediately)
- `-N` — remove entry N from favorites
- `N` — select entry N

```bash
uv run llm host set strix favorites '["Qwen3.6-35B-A3B-UD-Q4_K_M.gguf", "gemma-4-26B-A4B-it-UD-Q4_K_M.gguf"]'
```

## Configuration (`host.json`)

Each top-level key is the CLI host name. Per-host fields:

| Field | Meaning |
|-------|---------|
| `name` | Display name |
| `addr` / `ssh_host` | API address; remote hosts resolve `addr` via `~/.ssh/config` |
| `model_dir` | Directory holding `*.gguf` files (discovered at runtime) |
| `default_model` | Filename used when no model argument is given |
| `aliases` | Memorable shortcuts (e.g. `gemma` → filename); win over discovery |
| `favorites` | GGUF filenames listed first in the interactive model menu |
| `server` | Path to `llama-server` on that host |
| `port` | API port |
| `remote` | `true` = run commands over SSH |
| `extra_args` | Flags passed to every launch |
| `model_args` | Per-model flag overrides (keyed by GGUF filename) |
| `fan_script` | Optional fan-control script (set 75% on start, 50% on stop) |
| `temp_cmd` | Command printing the GPU temp (°C); first number in stdout is used |

## Model Selection

Models are discovered at runtime by listing `*.gguf` in the host's `model_dir`.
The `model` positional resolves in this order:

1. Exact alias key in the host's `aliases`
2. Exact filename or stem (case-insensitive)
3. Token match: every alphanumeric run in the query must appear (in any order) in the normalized filename

Ambiguous queries print all matches; missing ones print available aliases and files.

## Stats (`stats/`)

Every invocation against an up server samples cumulative token counters into
`stats/usage_stats.db` (SQLite). `stats` shows the current session plus
lifetime totals across sessions. A legacy `stats/usage_stats.csv` is imported
once automatically.

PP/TG t/s come from the server's Prometheus gauges — rolling averages that
reset to `0` shortly after the last request (shown as `0.0 (idle)`). Alongside
each, `avg` is the session average: cumulative tokens ÷ cumulative processing
seconds since the server started, which doesn't decay. The header also shows
GPU temp when the host's `temp_cmd` produces a number. Run a quick `msg` chat
first to see live (non-idle) values.

## Development

Tests and lint run through `uv` (dev dependencies are installed automatically):

```bash
uv run pytest        # 34 tests (config, host mgmt, menu/favorites, model resolution, stats)
uv run ruff check .  # lint
```

## Server Configuration

All servers are started with `-ngl 99 --flash-attn on --metrics --jinja --host 0.0.0.0`.
Per-host/per-model flags come from `extra_args` and `model_args` in `host.json`.
