import json

import pytest

import main

FILES = [
    ("a.gguf", "/x/a.gguf"),
    ("gemma.gguf", "/x/g.gguf"),
    ("b.gguf", "/x/b.gguf"),
]


@pytest.fixture
def tmp_hosts_file(tmp_path, monkeypatch):
    path = tmp_path / "host.json"
    path.write_text("{}\n")
    monkeypatch.setattr(main, "HOSTS_FILE", path)
    return path


def run_menu(monkeypatch, hosts, answers):
    it = iter(answers)
    monkeypatch.setattr("builtins.input", lambda *_: next(it))
    return main.prompt_model_menu(hosts, "strix", FILES, "Pick")


def test_selects_from_reordered_list(monkeypatch, tmp_hosts_file):
    hosts = {"strix": {"favorites": ["gemma.gguf"]}}
    chosen, ctx = run_menu(monkeypatch, hosts, ["3", "0"])
    assert chosen == ("b.gguf", "/x/b.gguf")
    assert ctx is None


def test_toggle_favorites_persists(monkeypatch, tmp_hosts_file):
    hosts = {"strix": {"favorites": ["gone.gguf", "gemma.gguf"]}}
    chosen, ctx = run_menu(monkeypatch, hosts, ["+3", "-1", "2", "0"])
    assert chosen == ("a.gguf", "/x/a.gguf")
    assert hosts["strix"]["favorites"] == ["b.gguf"]
    saved = json.loads(tmp_hosts_file.read_text())
    assert saved["strix"]["favorites"] == ["b.gguf"]


def test_invalid_inputs_reprompt(monkeypatch, tmp_hosts_file):
    hosts = {"strix": {}}
    chosen, ctx = run_menu(monkeypatch, hosts, ["+0", "+99", "x", "99", "3", "2"])
    assert chosen == ("b.gguf", "/x/b.gguf")
    assert ctx == 32768


def test_no_favorites_still_selects(monkeypatch, tmp_hosts_file):
    chosen, _ = run_menu(monkeypatch, {"strix": {}}, ["1", "0"])
    assert chosen == ("a.gguf", "/x/a.gguf")
