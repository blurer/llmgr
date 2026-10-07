import json

import pytest

import main

CHAT_RESP = {
    "choices": [{"message": {"content": "Hello!"}}],
    "usage": {"prompt_tokens": 5, "completion_tokens": 3},
}

HOSTS = {"test": {"name": "Test Box", "addr": "localhost", "port": 1, "remote": False, "model_dir": "/m", "default_model": "test.gguf"}}


class FakeResp:
    status = 200

    def __init__(self, body):
        self._body = body

    def read(self):
        return self._body


def fake_urlopen(req, timeout=None):
    url = req.full_url if hasattr(req, "full_url") else req
    if "/metrics" in url:
        return FakeResp(b"")
    if "/props" in url:
        return FakeResp(json.dumps({"model_path": "/m/test.gguf"}).encode())
    if url.endswith("/health"):
        return FakeResp(b'{"status":"ok"}')
    main.last_payload = json.loads(req.data.decode())
    return FakeResp(json.dumps(CHAT_RESP).encode())


@pytest.fixture
def msg_env(tmp_path, monkeypatch):
    hosts_file = tmp_path / "host.json"
    hosts_file.write_text(json.dumps(HOSTS, indent=2) + "\n")
    monkeypatch.setattr(main, "HOSTS_FILE", hosts_file)
    monkeypatch.setattr(main, "STATS_DB", tmp_path / "stats.db")
    monkeypatch.setattr(main, "urlopen", fake_urlopen)
    return tmp_path


def run_main(monkeypatch, argv):
    monkeypatch.setattr("sys.argv", ["llm", *argv])
    main.main()


def test_msg_instance_first(capsys, msg_env, monkeypatch):
    run_main(monkeypatch, ["test", "msg", "hi", "there"])
    out = capsys.readouterr().out
    assert "Hello!" in out
    assert "5 prompt / 3 gen tokens" in out
    assert main.last_payload["messages"] == [{"role": "user", "content": "hi there"}]
    assert main.last_payload["model"] == "test"


def test_msg_action_first(capsys, msg_env, monkeypatch):
    run_main(monkeypatch, ["msg", "test", "hello"])
    out = capsys.readouterr().out
    assert "Hello!" in out


def test_msg_json(capsys, msg_env, monkeypatch):
    run_main(monkeypatch, ["msg", "test", "hi", "--json"])
    out = capsys.readouterr().out
    result = json.loads(out)
    assert result["status"] == "ok"
    assert result["reply"] == "Hello!"
    assert result["prompt_tokens"] == 5
    assert result["completion_tokens"] == 3
    assert result["host"] == "Test Box"


def test_msg_unknown_host(msg_env, monkeypatch):
    with pytest.raises(SystemExit):
        run_main(monkeypatch, ["msg", "nope", "hi"])


def test_msg_down_host(capsys, msg_env, monkeypatch):
    monkeypatch.setattr(main, "health_check", lambda h: False)
    with pytest.raises(SystemExit):
        run_main(monkeypatch, ["test", "msg", "hi"])
