import json

import pytest

import main


@pytest.fixture
def tmp_hosts_file(tmp_path, monkeypatch):
    path = tmp_path / "host.json"
    path.write_text("{}\n")
    monkeypatch.setattr(main, "HOSTS_FILE", path)
    return path


def make_hosts():
    return {
        "strix": {"name": "Strix Halo", "port": 8087},
        "titan": {"name": "Titan RTX"},
    }


def test_set_string_field(tmp_hosts_file):
    hosts = make_hosts()
    main.do_host_set(hosts, "strix", "extra_args", "-c 4096")
    assert hosts["strix"]["extra_args"] == "-c 4096"
    saved = json.loads(tmp_hosts_file.read_text())
    assert saved["strix"]["extra_args"] == "-c 4096"


def test_set_port_parses_int(tmp_hosts_file):
    hosts = make_hosts()
    main.do_host_set(hosts, "titan", "port", "9999")
    assert hosts["titan"]["port"] == 9999


def test_set_remote_coerced_bool(tmp_hosts_file):
    hosts = make_hosts()
    main.do_host_set(hosts, "titan", "remote", "yes")
    assert hosts["titan"]["remote"] is True
    main.do_host_set(hosts, "titan", "remote", "false")
    assert hosts["titan"]["remote"] is False


def test_set_unknown_field(tmp_hosts_file):
    with pytest.raises(SystemExit):
        main.do_host_set(make_hosts(), "strix", "bogus", "x")


def test_set_missing_host(tmp_hosts_file):
    with pytest.raises(SystemExit):
        main.do_host_set(make_hosts(), "nope", "port", "1")


def test_set_aliases_requires_object(tmp_hosts_file):
    with pytest.raises(SystemExit):
        main.do_host_set(make_hosts(), "strix", "aliases", '["x"]')


def test_set_port_requires_int(tmp_hosts_file):
    with pytest.raises(SystemExit):
        main.do_host_set(make_hosts(), "strix", "port", "abc")


def test_set_favorites_requires_array_of_strings(tmp_hosts_file):
    with pytest.raises(SystemExit):
        main.do_host_set(make_hosts(), "strix", "favorites", '{"a": 1}')
    with pytest.raises(SystemExit):
        main.do_host_set(make_hosts(), "strix", "favorites", "[1]")


def test_unset_field(tmp_hosts_file):
    hosts = make_hosts()
    main.do_host_unset(hosts, "strix", "name")
    assert "name" not in hosts["strix"]
    with pytest.raises(SystemExit):
        main.do_host_unset(hosts, "strix", "name")


def test_remove_confirmed(monkeypatch, tmp_hosts_file):
    hosts = make_hosts()
    monkeypatch.setattr("builtins.input", lambda *_: "y")
    main.do_host_remove(hosts, "strix")
    assert list(hosts) == ["titan"]


def test_remove_aborted(monkeypatch, tmp_hosts_file):
    hosts = make_hosts()
    monkeypatch.setattr("builtins.input", lambda *_: "n")
    main.do_host_remove(hosts, "strix")
    assert list(hosts) == ["strix", "titan"]


def test_add_interactive(tmp_hosts_file, monkeypatch):
    answers = iter(
        [
            "My Host",
            "1.2.3.4",
            "/m",
            "x.gguf",
            '{"g": "g.gguf"}',
            "~/ls",
            "9000",
            "-c 4096",
            "{}",
            "sshbox",
            "",
            "",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda *_: next(answers))
    hosts = {}
    main.do_host_add(hosts, "mini")
    h = hosts["mini"]
    assert h["name"] == "My Host"
    assert h["port"] == 9000
    assert h["remote"] is True and h["ssh_host"] == "sshbox"
    assert h["aliases"] == {"g": "g.gguf"}
    assert "fan_script" not in h


def test_add_rejects_duplicate(tmp_hosts_file):
    with pytest.raises(SystemExit):
        main.do_host_add(make_hosts(), "strix")
