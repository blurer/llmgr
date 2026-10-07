import json

import pytest

import main


@pytest.fixture
def tmp_hosts_file(tmp_path, monkeypatch):
    path = tmp_path / "host.json"
    path.write_text("{}\n")
    monkeypatch.setattr(main, "HOSTS_FILE", path)
    return path


def write_hosts(path, hosts):
    path.write_text(json.dumps(hosts, indent=2) + "\n")


def test_load_hosts_valid(tmp_path, monkeypatch):
    p = tmp_path / "host.json"
    write_hosts(p, {"strix": {"name": "Strix Halo"}})
    monkeypatch.setattr(main, "HOSTS_FILE", p)
    hosts = main.load_hosts()
    assert list(hosts) == ["strix"]


def test_load_hosts_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "HOSTS_FILE", tmp_path / "nope.json")
    with pytest.raises(SystemExit):
        main.load_hosts()


def test_load_hosts_bad_json(tmp_path, monkeypatch):
    p = tmp_path / "host.json"
    p.write_text("{oops")
    monkeypatch.setattr(main, "HOSTS_FILE", p)
    with pytest.raises(SystemExit):
        main.load_hosts()


def test_load_hosts_not_an_object(tmp_path, monkeypatch):
    p = tmp_path / "host.json"
    p.write_text("[]")
    monkeypatch.setattr(main, "HOSTS_FILE", p)
    with pytest.raises(SystemExit):
        main.load_hosts()


def test_save_hosts_roundtrip(tmp_path, monkeypatch):
    p = tmp_path / "host.json"
    monkeypatch.setattr(main, "HOSTS_FILE", p)
    hosts = {"strix": {"name": "Strix Halo", "favorites": ["a.gguf"]}}
    main.save_hosts(hosts)
    assert json.loads(p.read_text()) == hosts
