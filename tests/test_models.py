import pytest

import main


def test_model_name_from_filename():
    assert (
        main.model_name_from_filename("Qwen3.6-35B-A3B-UD-Q4_K_M.gguf")
        == "Qwen3.6-35B-A3B-UD-Q4_K_M"
    )
    assert main.model_name_from_filename("/a/b/foo.gguf.1") == "foo"
    assert main.model_name_from_filename("model.safetensors") == "model"


def test_parse_ctx():
    assert main._parse_ctx("16k") == 16384
    assert main._parse_ctx("8K") == 8192
    assert main._parse_ctx("4096") == 4096
    assert main._parse_ctx(None) is None
    with pytest.raises(SystemExit):
        main._parse_ctx("abc")


def test_tokens_and_normalize():
    assert main._tokens("Qwen 3.6 IQ4!") == ["qwen", "3", "6", "iq4"]
    assert main._tokens("") == []
    assert main._normalize("Qwen3.6-35B_A") == "qwen3635ba"


def make_host():
    return {"name": "x", "model_dir": "/m", "aliases": {"gemma": "g.gguf"}}


def test_resolve_alias(monkeypatch):
    monkeypatch.setattr(main, "discover_models", lambda h: [])
    assert main.resolve_model(make_host(), "gemma") == ("g.gguf", "/m/g.gguf")


def test_resolve_exact_and_tokens(monkeypatch):
    files = [
        ("Foo.gguf", "/m/Foo.gguf"),
        ("bar-baz.gguf", "/m/bar-baz.gguf"),
    ]
    monkeypatch.setattr(main, "discover_models", lambda h: files)
    assert main.resolve_model(make_host(), "foo") == files[0]
    assert main.resolve_model(make_host(), "bar baz") == files[1]


def test_resolve_ambiguous(monkeypatch):
    files = [("a1-b.gguf", "/m/a1-b.gguf"), ("a2-b.gguf", "/m/a2-b.gguf")]
    monkeypatch.setattr(main, "discover_models", lambda h: files)
    with pytest.raises(SystemExit):
        main.resolve_model(make_host(), "b")


def test_resolve_missing(monkeypatch):
    monkeypatch.setattr(main, "discover_models", lambda h: [("a.gguf", "/m/a.gguf")])
    with pytest.raises(SystemExit):
        main.resolve_model(make_host(), "zzz")
