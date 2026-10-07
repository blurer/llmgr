#!/usr/bin/env python3
"""Manage llama-server instances across GPU hosts (configured in host.json)."""

import argparse
import json
import re
import shlex
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen

ACTION_NAMES = ("host", "start", "stop", "restart", "status", "stats", "msg")

ROOT = Path(__file__).resolve().parent
HOSTS_FILE = ROOT / "host.json"
STATS_DIR = ROOT / "stats"
STATS_DB = STATS_DIR / "usage_stats.db"


def load_hosts():
    """Load host configs from host.json (keyed by CLI host name)."""
    try:
        hosts = json.loads(HOSTS_FILE.read_text())
    except FileNotFoundError:
        sys.exit(f"Config file not found: {HOSTS_FILE}")
    except json.JSONDecodeError as e:
        sys.exit(f"Invalid JSON in {HOSTS_FILE}: {e}")
    if not isinstance(hosts, dict) or not hosts:
        sys.exit(f"{HOSTS_FILE.name} must be a non-empty object of host configs")
    return hosts


def resolve_addr(ssh_host):
    """Resolve a host's address from ~/.ssh/config via `ssh -G`."""
    try:
        r = subprocess.run(
            ["ssh", "-G", ssh_host],
            capture_output=True,
            text=True,
            timeout=3,
        )
        for line in r.stdout.splitlines():
            if line.startswith("hostname "):
                return line.split(None, 1)[1].strip()
    except Exception:
        pass
    return None


def resolve_host_addrs(hosts):
    """Fill in each host's addr from ~/.ssh/config when ssh_host is set."""
    for h in hosts.values():
        if h.get("ssh_host"):
            addr = resolve_addr(h["ssh_host"])
            if addr:
                h["addr"] = addr


def ssh_run(host, cmd, check=False):
    return subprocess.run(
        ["ssh", host, cmd],
        capture_output=True,
        text=True,
        check=check,
    )


def run_cmd(host, cmd, check=False):
    if host["remote"]:
        return ssh_run(host.get("ssh_host", host["addr"]), cmd, check=check)
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, check=check)


def health_check(host):
    try:
        r = urlopen(f"http://{host['addr']}:{host['port']}/health", timeout=3)
        body = r.read()
        return r.status == 200 or b"ok" in body
    except Exception:
        return False


def running_model_name(host):
    """Query the server for the actually-loaded model and return its filename stem."""
    try:
        r = urlopen(f"http://{host['addr']}:{host['port']}/props", timeout=3)
        data = json.loads(r.read())
        model_path = data.get("model_path", "")
        if model_path:
            return model_name_from_filename(model_path)
    except Exception:
        pass
    return model_name_from_filename(host.get("default_model", "")) or host["name"]


def model_name_from_filename(filename):
    name = Path(filename).name
    idx = name.lower().find(".gguf")
    if idx >= 0:
        return name[:idx]
    return Path(name).stem


def discover_models(host):
    """List GGUF files in the host's model_dir, sorted alphabetically.

    Returns list of (filename, full_path) tuples. Empty if the dir is
    unreachable or contains no GGUFs. Accepts suffixes like .gguf.1 for
    browser-renamed downloads."""
    model_dir = host["model_dir"]
    quoted_model_dir = shlex.quote(model_dir)
    r = run_cmd(
        host, f"find {quoted_model_dir} -maxdepth 1 -type f -name '*.gguf*' | sort"
    )
    out = []
    for line in r.stdout.splitlines():
        line = line.strip()
        if line:
            out.append((Path(line).name, line))
    return out


def _normalize(s):
    return "".join(c for c in s.lower() if c.isalnum())


def _parse_ctx(raw):
    """Convert a ctx arg like '16k' to 16384, or pass through a plain integer."""
    if raw is None:
        return None
    upper = raw.upper()
    multipliers = {"K": 1024}
    if upper.endswith("K"):
        try:
            return int(upper[:-1]) * multipliers["K"]
        except ValueError:
            sys.exit(f"--ctx: invalid preset '{raw}' (expected e.g. 16k, 32k, 64k)")
    try:
        return int(raw)
    except ValueError:
        sys.exit(f"--ctx: invalid value '{raw}' (expected e.g. 16k or a plain integer)")


def _tokens(query):
    """Split a query into lowercased alphanumeric runs."""
    out, cur = [], []
    for c in query.lower():
        if c.isalnum():
            cur.append(c)
        elif cur:
            out.append("".join(cur))
            cur = []
    if cur:
        out.append("".join(cur))
    return out


def resolve_model(host, query):
    """Map a user-supplied model string to (filename, full_path).

    Resolution order:
      1. Exact alias hit in host['aliases']
      2. Exact filename or stem match (case-insensitive)
      3. Token match: every alphanumeric run in the query must appear in the
         normalized filename (any order, ignoring case/punctuation).

    Exits with a clear error if missing or ambiguous."""
    aliases = host.get("aliases", {})
    if query in aliases:
        fname = aliases[query]
        return fname, f"{host['model_dir']}/{fname}"

    files = discover_models(host)
    if not files:
        sys.exit(f"No .gguf files found in {host['model_dir']} on {host['name']}")

    q_lower = query.lower()
    for fname, fpath in files:
        if fname.lower() == q_lower or Path(fname).stem.lower() == q_lower:
            return fname, fpath

    toks = _tokens(query)
    matches = [
        (f, p) for f, p in files if toks and all(t in _normalize(f) for t in toks)
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        names = ", ".join(model_name_from_filename(f) for f, _ in matches)
        sys.exit(f"'{query}' is ambiguous on {host['name']}: matches {names}")

    alias_list = ", ".join(sorted(aliases)) or "(none)"
    available = ", ".join(model_name_from_filename(f) for f, _ in files)
    sys.exit(
        f"'{query}' not found on {host['name']}.\n"
        f"  Aliases: {alias_list}\n"
        f"  Files:   {available}"
    )


def fetch_metrics(host):
    """Fetch and parse Prometheus metrics from /metrics endpoint."""
    try:
        r = urlopen(f"http://{host['addr']}:{host['port']}/metrics", timeout=5)
        text = r.read().decode()
    except Exception:
        return None

    metrics = {}
    for line in text.splitlines():
        if line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            key = parts[0]
            # Handle labels like metric{le="0.5"}
            base = key.split("{")[0]
            metrics.setdefault(base, [])
            metrics[base].append((key, float(parts[1])))
    return metrics


def do_status(host, as_json=False):
    name = host["name"]
    up = health_check(host)
    model = running_model_name(host) if up else host["model_name"]
    temp = get_temp(host) if up else None
    if as_json:
        result = {
            "host": name,
            "model": model,
            "port": host["port"],
            "status": "up" if up else "down",
        }
        if temp is not None:
            result["temp_c"] = round(temp)
        return result
    if up:
        print(f"  {name}: {model} is UP on port {host['port']}{fmt_temp(temp)}")
    else:
        print(f"  {name}: DOWN")


def do_stop(host, as_json=False):
    name = host["name"]
    stopped = []
    r = run_cmd(host, "pkill -f llama-server")
    if r.returncode == 0:
        if not as_json:
            print(f"  {name}: Stopped llama-server")
        stopped.append("llama-server")
    if not stopped and not as_json:
        print(f"  {name}: No server was running")
    fan_script = host.get("fan_script")
    if fan_script:
        if not as_json:
            print(f"  {name}: Setting fan to 50%...")
        run_cmd(host, f"{fan_script} 50")
    time.sleep(2)
    if as_json:
        return {"host": name, "stopped": stopped, "was_running": len(stopped) > 0}


def do_start(host, as_json=False, ctx_size=None):
    name = host["name"]

    if health_check(host):
        if not as_json:
            print(f"  {name}: Already running on port {host['port']}")
        if as_json:
            return {"host": name, "status": "already_running", "port": host["port"]}
        return True

    fan_script = host.get("fan_script")
    if fan_script:
        if not as_json:
            print(f"  {name}: Setting fan to 75%...")
        run_cmd(host, f"{fan_script} 75")

    port = host["port"]
    log_file = "/tmp/llama-server.log"
    extra = host.get("model_args", {}).get(
        Path(host["model"]).name, host.get("extra_args", "")
    )
    if ctx_size is not None:
        extra = re.sub(r"-c\s+\d+", f"-c {ctx_size}", extra)
    cmd = (
        f"nohup {host['server']} -m {host['model']} {extra} "
        f"-ngl 99 --flash-attn on --metrics --jinja "
        f"--host 0.0.0.0 --port {port} "
        f"> {log_file} 2>&1 &"
    )

    if not as_json:
        print(f"  {name}: Starting {host['model_name']} on port {port}...")
    run_cmd(host, cmd)

    max_wait = 240
    if not as_json:
        print(f"  {name}: Waiting for server (up to {max_wait}s)...", end="", flush=True)
    for _ in range(max_wait // 2):
        if health_check(host):
            if not as_json:
                print(" UP!")
            if as_json:
                return {
                    "host": name,
                    "status": "started",
                    "model": host["model_name"],
                    "port": port,
                }
            return True
        if not as_json:
            print(".", end="", flush=True)
        time.sleep(2)

    if not as_json:
        print(f"\n  {name}: ERROR - did not start within {max_wait}s")
        if host["remote"]:
            print(f"  Check logs: ssh {host['addr']} cat {log_file}")
        else:
            print(f"  Check logs: cat {log_file}")
    if as_json:
        return {
            "host": name,
            "status": "error",
            "error": f"did not start within {max_wait}s",
        }
    return False


CTX_PRESETS = ("16k", "32k", "64k", "128k", "256k")


def _render_model_menu(ordered, fav_names, prompt):
    print(f"{prompt}:")
    if fav_names:
        shown_fav = shown_all = False
        for i, (fname, _) in enumerate(ordered, 1):
            if fname in fav_names and not shown_fav:
                print("Favorites:")
                shown_fav = True
            elif fname not in fav_names and not shown_all:
                print("All models:")
                shown_all = True
            print(f"  {i}) {Path(fname).stem}")
    else:
        for i, (fname, _) in enumerate(ordered, 1):
            print(f"  {i}) {Path(fname).stem}")
    print("  (N select  +N favorite  -N unfavorite)")


def prompt_model_menu(hosts, target, files, prompt="Select a model"):
    """Show an interactive menu of discovered GGUFs, then ctx size.

    The host's favorites (stored in host.json) are listed first. `+N`/`-N`
    toggle the Nth entry's favorite status and re-render; a bare `N` selects.

    `files` is a list of (filename, full_path) tuples.
    Returns (chosen_tuple, ctx_size) where ctx_size is an int or None."""
    favorites = hosts[target].setdefault("favorites", [])
    while True:
        known = {f for f, _ in files}
        favorites[:] = [f for f in favorites if f in known]
        fav_set = set(favorites)
        ordered = [t for t in files if t[0] in fav_set] + [
            t for t in files if t[0] not in fav_set
        ]
        _render_model_menu(ordered, fav_set, prompt)

        raw = input("> ").strip()
        if raw[:1] in ("+", "-"):
            try:
                idx = int(raw[1:]) - 1
            except ValueError:
                print("Invalid choice. Try again.")
                continue
            if not 0 <= idx < len(ordered):
                print("Invalid choice. Try again.")
                continue
            fname = ordered[idx][0]
            if raw[0] == "+" and fname not in fav_set:
                favorites.append(fname)
            elif raw[0] == "-" and fname in fav_set:
                favorites.remove(fname)
            save_hosts(hosts)
            continue

        try:
            idx = int(raw) - 1
            if 0 <= idx < len(ordered):
                break
        except ValueError:
            pass
        print("Invalid choice. Try again.")

    # Context-size picker
    ctx_menu = "\nContext size (override host default):" + "".join(
        f"  {i + 1}) {p}" for i, p in enumerate(CTX_PRESETS)
    )
    ctx_menu += "  0) keep host default\n> "
    while True:
        try:
            c = int(input(ctx_menu).strip())
            if c == 0:
                ctx_size = None
            elif 1 <= c <= len(CTX_PRESETS):
                ctx_size = _parse_ctx(CTX_PRESETS[c - 1])
            else:
                print("Invalid choice. Try again.")
                continue
            break
        except ValueError:
            print("Invalid choice. Try again.")

    return ordered[idx], ctx_size


def get_process_uptime(host):
    """Get uptime in seconds from the llama-server process start time."""
    r = run_cmd(host, "ps -o etimes= -C llama-server 2>/dev/null | head -1")
    if r.returncode == 0 and r.stdout.strip():
        try:
            return int(r.stdout.strip())
        except ValueError:
            pass
    return 0


def init_db():
    STATS_DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(STATS_DB)
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS samples (
            ts INTEGER NOT NULL,
            host TEXT NOT NULL,
            model TEXT,
            uptime_s INTEGER,
            prompt_tokens INTEGER,
            gen_tokens INTEGER
        )
        """
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_samples_host_ts ON samples(host, ts)"
    )
    _import_legacy_csv(con)
    con.commit()
    con.close()


def _import_legacy_csv(con):
    """One-time import of the pre-SQLite usage_stats.csv into samples.

    Renames the CSV after a successful import so we never re-run."""
    csv_path = STATS_DB.parent / "usage_stats.csv"
    if not csv_path.exists():
        return
    import csv as _csv
    from datetime import datetime as _dt

    imported = 0
    with open(csv_path, newline="") as f:
        for row in _csv.DictReader(f):
            try:
                ts = int(_dt.fromisoformat(row["timestamp"]).timestamp())
            except (KeyError, ValueError):
                continue
            con.execute(
                "INSERT INTO samples (ts, host, model, uptime_s, prompt_tokens, gen_tokens)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    ts,
                    row.get("host", ""),
                    row.get("model", ""),
                    int(row.get("uptime_s") or 0),
                    int(row.get("tokens_input") or 0),
                    int(row.get("tokens_output") or 0),
                ),
            )
            imported += 1
    csv_path.rename(csv_path.with_suffix(".csv.imported"))
    if imported:
        print(f"[usage_stats] imported {imported} rows from {csv_path.name}")


TOKEN_IN_KEYS = {
    "llamacpp:prompt_tokens_total",
    "llamacpp_prompt_tokens_total",
}
TOKEN_OUT_KEYS = {
    "llamacpp:tokens_predicted_total",
    "llamacpp_tokens_predicted_total",
}


def read_token_counters(metrics):
    p_in = g_out = 0
    for base, entries in metrics.items():
        if base in TOKEN_IN_KEYS:
            p_in = int(entries[0][1])
        elif base in TOKEN_OUT_KEYS:
            g_out = int(entries[0][1])
    return p_in, g_out


def metric_value(metrics, *bases):
    """First value among the given metric base names, or None."""
    for base in bases:
        entries = metrics.get(base)
        if entries:
            return entries[0][1]
    return None


def get_temp(host):
    """Best-effort GPU temp (°C) via the host's temp_cmd; None if unavailable."""
    cmd = host.get("temp_cmd")
    if not cmd:
        return None
    r = run_cmd(host, cmd)
    m = re.search(r"\d+(?:\.\d+)?", r.stdout)
    return float(m.group()) if m else None


def fmt_temp(temp):
    return f", {temp:.0f}°C" if temp is not None else ""


def record_sample(host):
    """Snapshot current cumulative token counters into the SQLite log.

    No-op if the server is unreachable or exposes no metrics."""
    if not health_check(host):
        return
    metrics = fetch_metrics(host)
    if not metrics:
        return
    p_in, g_out = read_token_counters(metrics)
    uptime = get_process_uptime(host)
    model = running_model_name(host)
    init_db()
    con = sqlite3.connect(STATS_DB)
    con.execute(
        "INSERT INTO samples (ts, host, model, uptime_s, prompt_tokens, gen_tokens)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (int(time.time()), host["name"], model, uptime, p_in, g_out),
    )
    con.commit()
    con.close()


SESSION_TOLERANCE_S = 60  # clock-skew slack when comparing uptime advance vs. ts gap


def compute_lifetime(host_name):
    """Sum cumulative tokens across all detected sessions for a host.

    Each sample stores cumulative counters since the server's process start. A
    boundary is detected when the uptime advance from one sample to the next
    is meaningfully less than the wall-clock gap — i.e., the process was not
    running for some of that gap (a stop+restart, or an imported snapshot from
    a different session). Within a single live session, uptime ticks 1:1 with
    wall clock, so the boundary check is naturally false."""
    if not STATS_DB.exists():
        return None
    con = sqlite3.connect(STATS_DB)
    rows = con.execute(
        "SELECT ts, uptime_s, prompt_tokens, gen_tokens FROM samples"
        " WHERE host = ? ORDER BY ts",
        (host_name,),
    ).fetchall()
    con.close()
    if not rows:
        return None

    total_in = total_out = total_uptime = sessions = 0
    last = None
    for ts, uptime, p_in, g_out in rows:
        if last is not None:
            prev_ts, prev_up, prev_in, prev_out = last
            if (uptime - prev_up) + SESSION_TOLERANCE_S < (ts - prev_ts):
                total_in += prev_in
                total_out += prev_out
                total_uptime += prev_up
                sessions += 1
        last = (ts, uptime, p_in, g_out)
    total_in += last[2]
    total_out += last[3]
    total_uptime += last[1]
    sessions += 1
    return {
        "tokens_input": total_in,
        "tokens_output": total_out,
        "uptime_s": total_uptime,
        "sessions": sessions,
    }


def do_stats(host, as_json=False):
    name = host["name"]
    lifetime = compute_lifetime(name)

    def fmt_lifetime(lt):
        h = round(lt["uptime_s"] / 3600, 1)
        total = lt["tokens_input"] + lt["tokens_output"]
        return (
            f"  Lifetime| input: {lt['tokens_input']:,}  output: {lt['tokens_output']:,}"
            f"  total: {total:,}  ({h}h across {lt['sessions']} sessions)"
        )

    if not health_check(host):
        if as_json:
            result = {"host": name, "status": "down"}
            if lifetime:
                result["lifetime"] = lifetime
            return result
        print(f"  {name}: DOWN")
        if lifetime and (lifetime["tokens_input"] + lifetime["tokens_output"]) > 0:
            print(fmt_lifetime(lifetime))
        else:
            print("  Lifetime| no history yet — start the server to begin sampling")
        return

    metrics = fetch_metrics(host)
    if not metrics:
        if as_json:
            return {"host": name, "status": "no_metrics"}
        print(f"  {name}: Server is up but --metrics not enabled. Restart with --restart.")
        return

    tokens_input, tokens_output = read_token_counters(metrics)
    uptime = get_process_uptime(host)

    pp_tps = metric_value(
        metrics, "llamacpp:prompt_tokens_seconds", "llamacpp_prompt_tokens_seconds"
    )
    tg_tps = metric_value(
        metrics,
        "llamacpp:predicted_tokens_seconds",
        "llamacpp_predicted_tokens_seconds",
    )
    pp_secs = metric_value(
        metrics,
        "llamacpp:prompt_seconds_total",
        "llamacpp:prompt_tokens_seconds_sum",
        "llamacpp_prompt_tokens_seconds_sum",
    )
    tg_secs = metric_value(
        metrics,
        "llamacpp:tokens_predicted_seconds_total",
        "llamacpp:predicted_tokens_seconds_sum",
        "llamacpp_predicted_tokens_seconds_sum",
    )
    pp_avg = tokens_input / pp_secs if pp_secs else None
    tg_avg = tokens_output / tg_secs if tg_secs else None
    temp = get_temp(host)
    model = running_model_name(host)
    result = {
        "host": name,
        "model": model,
        "status": "up",
        "session": {
            "uptime_s": uptime,
            "uptime_h": round(uptime / 3600, 1),
            "tokens_input": tokens_input,
            "tokens_output": tokens_output,
            "tokens_total": tokens_input + tokens_output,
            "pp_tps": round(pp_tps, 1) if pp_tps is not None else None,
            "tg_tps": round(tg_tps, 1) if tg_tps is not None else None,
            "pp_avg": round(pp_avg, 1) if pp_avg is not None else None,
            "tg_avg": round(tg_avg, 1) if tg_avg is not None else None,
        },
    }
    if temp is not None:
        result["temp_c"] = round(temp)
    if lifetime:
        result["lifetime"] = {
            "uptime_s": lifetime["uptime_s"],
            "uptime_h": round(lifetime["uptime_s"] / 3600, 1),
            "tokens_input": lifetime["tokens_input"],
            "tokens_output": lifetime["tokens_output"],
            "tokens_total": lifetime["tokens_input"] + lifetime["tokens_output"],
            "sessions": lifetime["sessions"],
        }

    if as_json:
        return result

    def fmt_tps(label, tps, avg):
        if tps is None:
            return f"  {label}  | no data yet"
        cur = "0.0 (idle)" if tps == 0 else f"{tps:.1f}"
        if avg is not None:
            cur += f"  avg {avg:.1f}"
        return f"  {label}  | {cur}"

    print(f"  {name}: {model} (uptime: {uptime / 3600:.1f}h{fmt_temp(temp)})")
    print(
        f"  Tokens  | input: {tokens_input:,}  output: {tokens_output:,}"
        f"  total: {tokens_input + tokens_output:,}"
    )
    print(fmt_tps("PP t/s", pp_tps, pp_avg))
    print(fmt_tps("TG t/s", tg_tps, tg_avg))
    if lifetime:
        print(fmt_lifetime(lifetime))


def do_msg(host, message, as_json=False):
    """Send a chat message to the host's OpenAI-compatible endpoint."""
    name = host["name"]
    if not health_check(host):
        if as_json:
            return {"host": name, "status": "down"}
        sys.exit(f"  {name}: DOWN — start it first (llm start)")

    url = f"http://{host['addr']}:{host['port']}/v1/chat/completions"
    payload = json.dumps(
        {
            "model": host.get("model_name", ""),
            "messages": [{"role": "user", "content": message}],
        }
    ).encode()
    req = Request(url, data=payload, headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    try:
        r = urlopen(req, timeout=600)
        data = json.loads(r.read())
        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        p_in = usage.get("prompt_tokens", 0)
        p_out = usage.get("completion_tokens", 0)
    except Exception as e:
        err = f"request failed: {e}"
        if as_json:
            return {"host": name, "status": "error", "error": err}
        sys.exit(f"  {name}: {err}")
    elapsed = time.perf_counter() - t0
    tps = p_out / elapsed if p_out and elapsed > 0 else 0.0

    if as_json:
        return {
            "host": name,
            "model": host.get("model_name", ""),
            "status": "ok",
            "reply": content,
            "elapsed_s": round(elapsed, 2),
            "prompt_tokens": p_in,
            "completion_tokens": p_out,
            "gen_tps": round(tps, 1),
        }

    print(f"  {content}")
    print(
        f"  --- {p_in:,} prompt / {p_out:,} gen tokens in {elapsed:.1f}s"
        f" ({tps:.1f} gen t/s)"
    )


HOST_FIELDS = (
    "name",
    "addr",
    "ssh_host",
    "model_dir",
    "default_model",
    "aliases",
    "favorites",
    "server",
    "port",
    "remote",
    "extra_args",
    "model_args",
    "fan_script",
    "temp_cmd",
)


def save_hosts(hosts):
    HOSTS_FILE.write_text(json.dumps(hosts, indent=2) + "\n")


def _prompt(label, default=""):
    suffix = f" [{default}]" if default else ""
    try:
        val = input(f"{label}{suffix}: ").strip()
    except EOFError:
        sys.exit("\nInput ended — aborting without changes")
    return val or default


def _prompt_int(label, default):
    while True:
        raw = _prompt(label, str(default))
        try:
            return int(raw)
        except ValueError:
            print("Must be an integer, try again.")


def _prompt_json_obj(label, default):
    while True:
        raw = _prompt(label, default)
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            print("Invalid JSON, try again.")
            continue
        if not isinstance(parsed, dict):
            print("Must be a JSON object, try again.")
            continue
        return parsed


def do_host_list(hosts):
    for key, h in hosts.items():
        where = f"{h.get('ssh_host', '')} (ssh)" if h.get("remote") else h.get("addr", "-")
        print(
            f"  {key:<12} {h.get('name', key):<16} {where:<18}"
            f" port {h.get('port', '-')!s:<6} {len(h.get('aliases', {}))} aliases"
            f"  default: {h.get('default_model', '-')}"
        )


def do_host_show(hosts, name):
    if name not in hosts:
        sys.exit(f"Host '{name}' not found in {HOSTS_FILE.name} (hosts: {', '.join(hosts)})")
    print(json.dumps(hosts[name], indent=2))


def do_host_add(hosts, name):
    if name in hosts:
        sys.exit(f"Host '{name}' already exists in {HOSTS_FILE.name}")
    print(f"Adding host '{name}' — press Enter to accept the [default]:")
    host = {
        "name": _prompt("Display name", name),
        "addr": _prompt("API address", "localhost"),
        "model_dir": _prompt("Model dir"),
        "default_model": _prompt("Default GGUF filename"),
        "aliases": _prompt_json_obj("Aliases (JSON)", "{}"),
        "server": _prompt("llama-server path"),
        "port": _prompt_int("Port", 8087),
        "extra_args": _prompt("Extra args"),
        "model_args": _prompt_json_obj("Per-model args (JSON)", "{}"),
    }
    ssh_host = _prompt("SSH host (blank if local)")
    if ssh_host:
        host["ssh_host"] = ssh_host
        host["remote"] = True
    fan_script = _prompt("Fan script (blank to omit)")
    if fan_script:
        host["fan_script"] = fan_script
    temp_cmd = _prompt("GPU temp command (blank to omit)")
    if temp_cmd:
        host["temp_cmd"] = temp_cmd
    hosts[name] = host
    save_hosts(hosts)
    print(f"Added host '{name}' to {HOSTS_FILE.name}")


def do_host_remove(hosts, name, assume_yes=False):
    if name not in hosts:
        sys.exit(f"Host '{name}' not found in {HOSTS_FILE.name} (hosts: {', '.join(hosts)})")
    if not assume_yes:
        answer = _prompt(f"Remove host '{name}' from {HOSTS_FILE.name}? [y/N]").lower()
        if answer not in ("y", "yes"):
            print("Aborted")
            return
    del hosts[name]
    save_hosts(hosts)
    print(f"Removed host '{name}' from {HOSTS_FILE.name}")


def do_host_set(hosts, name, field, value):
    if name not in hosts:
        sys.exit(f"Host '{name}' not found in {HOSTS_FILE.name} (hosts: {', '.join(hosts)})")
    if field not in HOST_FIELDS:
        sys.exit(f"Unknown field '{field}'. Known fields: {', '.join(HOST_FIELDS)}")
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        parsed = value
    if field in ("aliases", "model_args") and not isinstance(parsed, dict):
        sys.exit(f"'{field}' must be a JSON object, e.g. '{{\"gemma\": \"x.gguf\"}}'")
    if field == "favorites" and (
        not isinstance(parsed, list) or not all(isinstance(x, str) for x in parsed)
    ):
        sys.exit(f"'{field}' must be a JSON array of GGUF filenames")
    if field == "port" and not isinstance(parsed, int):
        sys.exit("'port' must be an integer")
    if field == "remote" and not isinstance(parsed, bool):
        parsed = str(parsed).lower() in ("true", "1", "yes")
    hosts[name][field] = parsed
    save_hosts(hosts)
    print(f"Set {name}.{field} = {json.dumps(parsed)}")


def do_host_unset(hosts, name, field):
    if name not in hosts:
        sys.exit(f"Host '{name}' not found in {HOSTS_FILE.name} (hosts: {', '.join(hosts)})")
    if field not in hosts[name]:
        sys.exit(f"'{field}' is not set on '{name}'")
    del hosts[name][field]
    save_hosts(hosts)
    print(f"Unset {name}.{field}")


def main():
    hosts = load_hosts()

    parser = argparse.ArgumentParser(
        description="Manage llama-server instances across GPU hosts"
    )
    sub = parser.add_subparsers(dest="action")

    host_p = sub.add_parser("host", help="Manage hosts in host.json")
    host_sub = host_p.add_subparsers(dest="host_action", required=True)
    host_sub.add_parser("list", help="List configured hosts")
    p = host_sub.add_parser("show", help="Show one host's full config")
    p.add_argument("name")
    p = host_sub.add_parser("add", help="Add a host interactively")
    p.add_argument("name")
    p = host_sub.add_parser("remove", help="Remove a host")
    p.add_argument("name")
    p.add_argument("--yes", action="store_true", help="Skip confirmation")
    p = host_sub.add_parser("set", help="Set a host field (value parsed as JSON when valid)")
    p.add_argument("name")
    p.add_argument("field")
    p.add_argument("value")
    p = host_sub.add_parser("unset", help="Remove a host field")
    p.add_argument("name")
    p.add_argument("field")

    host_names = [*hosts, "both"]
    for name, help_text in [
        ("start", "Start the server (skip if already running)"),
        ("stop", "Stop the server"),
        ("restart", "Stop and restart the server"),
        ("status", "Check if the server is running"),
        ("stats", "Show token counts and pp/tg performance stats"),
    ]:
        p = sub.add_parser(name, help=help_text)
        p.add_argument(
            "host",
            nargs="?",
            default="both",
            choices=host_names,
            help="Target host (default: both)",
        )
        p.add_argument(
            "--json",
            action="store_true",
            help="Output as JSON",
        )
        if name in ("start", "restart"):
            p.add_argument(
                "model",
                nargs="?",
                help="Model alias, filename, stem, or substring "
                "(see the host's 'aliases' in host.json and its model_dir). "
                "Default: host's default_model.",
            )
            p.add_argument(
                "--ctx",
                metavar="N{k}",
                help="Context length preset (overrides host default). "
                "Also offered interactively in the model menu.",
            )

    p = sub.add_parser("msg", help="Send a chat message to one host (smoke test)")
    p.add_argument("host", help="Target host (e.g. llm strix msg 'hello')")
    p.add_argument("message", nargs="+", help="Message text (quoted or bare words)")
    p.add_argument("--json", action="store_true", help="Output as JSON")

    argv = sys.argv[1:]
    if argv and argv[0] in hosts and argv[0] not in ACTION_NAMES:
        rest = argv[1:]
        if rest and rest[0] == "msg":
            rest = rest[1:]
        argv = ["msg", argv[0], *rest]
    args = parser.parse_args(argv)
    if not args.action:
        parser.print_help()
        sys.exit(1)

    if args.action == "host":
        if args.host_action == "list":
            do_host_list(hosts)
        elif args.host_action == "show":
            do_host_show(hosts, args.name)
        elif args.host_action == "add":
            do_host_add(hosts, args.name)
        elif args.host_action == "remove":
            do_host_remove(hosts, args.name, assume_yes=args.yes)
        elif args.host_action == "set":
            do_host_set(hosts, args.name, args.field, args.value)
        elif args.host_action == "unset":
            do_host_unset(hosts, args.name, args.field)
        return

    if args.action == "msg" and args.host not in hosts:
        sys.exit(f"Unknown host '{args.host}'. Known hosts: {', '.join(hosts)}")

    resolve_host_addrs(hosts)
    init_db()

    targets = list(hosts) if args.host == "both" else [args.host]
    json_output = getattr(args, "json", False)
    json_results = []

    if getattr(args, "model", None) and len(targets) > 1:
        sys.exit("model argument requires a single host (got 'both')")

    for target in targets:
        host = dict(hosts[target])
        host["model"] = f"{host['model_dir']}/{host['default_model']}"
        host["model_name"] = model_name_from_filename(host["default_model"])

        model_query = getattr(args, "model", None)
        ctx_raw = getattr(args, "ctx", None)
        host["ctx_size"] = _parse_ctx(ctx_raw) if ctx_raw else None
        if args.action in ("start", "restart"):
            if model_query:
                fname, fpath = resolve_model(host, model_query)
                host["model"] = fpath
                host["model_name"] = model_name_from_filename(fname)
            elif not json_output and len(targets) == 1:
                files = discover_models(host)
                if len(files) > 1:
                    (fname, fpath), ctx_size = prompt_model_menu(
                        hosts, target, files, f"[{host['name']}] Select model"
                    )
                    host["model"] = fpath
                    host["model_name"] = model_name_from_filename(fname)
                    # CLI --ctx takes precedence over menu selection
                    if host["ctx_size"] is None:
                        host["ctx_size"] = ctx_size

        if not json_output:
            print(f"[{host['name']}]")

        record_sample(host)

        if args.action == "status":
            result = do_status(host, as_json=json_output)
        elif args.action == "stop":
            result = do_stop(host, as_json=json_output)
        elif args.action == "start":
            result = do_start(host, as_json=json_output, ctx_size=host["ctx_size"])
            record_sample(host)
        elif args.action == "restart":
            stop_result = do_stop(host, as_json=json_output)
            start_result = do_start(host, as_json=json_output, ctx_size=host["ctx_size"])
            record_sample(host)
            if json_output:
                result = {
                    "host": host["name"],
                    "stop": stop_result,
                    "start": start_result,
                }
            else:
                result = None
        elif args.action == "stats":
            result = do_stats(host, as_json=json_output)
        elif args.action == "msg":
            result = do_msg(host, " ".join(args.message), as_json=json_output)

        if json_output and result is not None:
            json_results.append(result)

        if not json_output:
            print()

    if json_output:
        print(
            json.dumps(
                json_results if len(json_results) > 1 else json_results[0], indent=2
            )
        )


if __name__ == "__main__":
    main()
