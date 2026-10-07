import sqlite3

import main


def init_tmp_db(monkeypatch, tmp_path):
    db = tmp_path / "stats.db"
    monkeypatch.setattr(main, "STATS_DB", db)
    main.init_db()
    return db


def seed(con, host, rows):
    con.executemany(
        "INSERT INTO samples (ts, host, model, uptime_s, prompt_tokens, gen_tokens)"
        " VALUES (?, ?, 'm', ?, ?, ?)",
        [(ts, host, up, pin, gout) for ts, up, pin, gout in rows],
    )
    con.commit()


def test_init_db_creates_stats_dir(tmp_path, monkeypatch):
    db = init_tmp_db(monkeypatch, tmp_path / "nested")
    assert db.exists()
    con = sqlite3.connect(db)
    cols = [r[1] for r in con.execute("PRAGMA table_info(samples)")]
    con.close()
    assert cols == [
        "ts",
        "host",
        "model",
        "uptime_s",
        "prompt_tokens",
        "gen_tokens",
    ]


def test_compute_lifetime_sessions(tmp_path, monkeypatch):
    init_tmp_db(monkeypatch, tmp_path)
    con = sqlite3.connect(main.STATS_DB)
    seed(con, "h", [(1000, 100, 10, 5), (1100, 200, 20, 10), (5000, 50, 100, 50)])
    con.close()
    lifetime = main.compute_lifetime("h")
    assert lifetime == {
        "tokens_input": 120,
        "tokens_output": 60,
        "uptime_s": 250,
        "sessions": 2,
    }


def test_compute_lifetime_single_session(tmp_path, monkeypatch):
    init_tmp_db(monkeypatch, tmp_path)
    con = sqlite3.connect(main.STATS_DB)
    seed(con, "h", [(1000, 100, 10, 5), (1100, 200, 20, 10)])
    con.close()
    lifetime = main.compute_lifetime("h")
    assert lifetime == {
        "tokens_input": 20,
        "tokens_output": 10,
        "uptime_s": 200,
        "sessions": 1,
    }


def test_compute_lifetime_empty_db(tmp_path, monkeypatch):
    init_tmp_db(monkeypatch, tmp_path)
    assert main.compute_lifetime("ghost") is None


def test_read_token_counters():
    metrics = {
        "llamacpp:prompt_tokens_total": [("k", 100.0)],
        "llamacpp:tokens_predicted_total": [("k", 25.0)],
        "other": [("k", 1.0)],
    }
    assert main.read_token_counters(metrics) == (100, 25)
    assert main.read_token_counters({}) == (0, 0)


def seed_metrics(pp, tg):
    return {
        "llamacpp:prompt_tokens_total": [("k", 12.0)],
        "llamacpp:tokens_predicted_total": [("k", 142.0)],
        "llamacpp:prompt_tokens_seconds": [("k", pp)],
        "llamacpp:predicted_tokens_seconds": [("k", tg)],
    }


def run_stats(monkeypatch, tmp_path, metrics):
    monkeypatch.setattr(main, "STATS_DB", tmp_path / "nope.db")
    monkeypatch.setattr(main, "health_check", lambda h: True)
    monkeypatch.setattr(main, "get_process_uptime", lambda h: 60)
    monkeypatch.setattr(main, "running_model_name", lambda h: "m")
    monkeypatch.setattr(main, "fetch_metrics", lambda h: metrics)
    host = {"name": "Test", "addr": "x", "port": 1}
    out = {"text": "", "json": None}
    out["json"] = main.do_stats(host, as_json=True)
    main.do_stats(host, as_json=False)
    return out


def test_stats_zero_gauges_shown_as_idle(tmp_path, monkeypatch, capsys):
    run_stats(monkeypatch, tmp_path, seed_metrics(0.0, 0.0))
    out = capsys.readouterr().out
    assert "0.0 (idle)" in out
    assert "no data yet" not in out


def test_stats_real_gauges(tmp_path, monkeypatch, capsys):
    result = run_stats(monkeypatch, tmp_path, seed_metrics(460.989, 219.521))
    out = capsys.readouterr().out
    assert "PP t/s  | 461.0" in out
    assert "TG t/s  | 219.5" in out
    assert result["json"]["session"]["pp_tps"] == 461.0
    assert result["json"]["session"]["tg_tps"] == 219.5


def test_stats_missing_gauges_no_data(tmp_path, monkeypatch, capsys):
    metrics = {
        "llamacpp:prompt_tokens_total": [("k", 12.0)],
        "llamacpp:tokens_predicted_total": [("k", 142.0)],
    }
    run_stats(monkeypatch, tmp_path, metrics)
    out = capsys.readouterr().out
    assert "no data yet" in out


def test_stats_session_avg_from_totals(tmp_path, monkeypatch, capsys):
    metrics = seed_metrics(0.0, 0.0)
    metrics["llamacpp:prompt_seconds_total"] = [("k", 0.169642)]
    metrics["llamacpp:tokens_predicted_seconds_total"] = [("k", 4.18103)]
    result = run_stats(monkeypatch, tmp_path, metrics)
    out = capsys.readouterr().out
    assert "avg 70.7" in out
    assert "avg 34.0" in out
    assert result["json"]["session"]["pp_avg"] == 70.7
    assert result["json"]["session"]["tg_avg"] == 34.0


def test_get_temp(tmp_path, monkeypatch):
    host = {"name": "t", "addr": "x", "port": 1, "remote": False, "temp_cmd": "cmd"}
    monkeypatch.setattr(
        main, "run_cmd", lambda h, cmd, check=False: type("R", (), {"stdout": "52\n"})
    )
    assert main.get_temp(host) == 52.0
    monkeypatch.setattr(
        main, "run_cmd", lambda h, cmd, check=False: type("R", (), {"stdout": ""})
    )
    assert main.get_temp(host) is None
    del host["temp_cmd"]
    assert main.get_temp(host) is None


def test_status_shows_temp(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(main, "health_check", lambda h: True)
    monkeypatch.setattr(main, "running_model_name", lambda h: "m")
    monkeypatch.setattr(main, "get_temp", lambda h: 52.0)
    main.do_status({"name": "Test", "addr": "x", "port": 1}, as_json=False)
    out = capsys.readouterr().out
    assert "UP on port 1, 52°C" in out
    result = main.do_status({"name": "Test", "addr": "x", "port": 1}, as_json=True)
    assert result["temp_c"] == 52
