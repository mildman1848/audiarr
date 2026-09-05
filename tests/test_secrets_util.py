from app.secrets_util import mask, resolve_secret


def test_resolve_secret_prefers_file(tmp_path, monkeypatch):
    secret_file = tmp_path / "secret"
    secret_file.write_text("file-value\n", encoding="utf-8")
    monkeypatch.setenv("FILE__MY_SECRET", str(secret_file))
    monkeypatch.setenv("MY_SECRET", "plain-value")

    assert resolve_secret("MY_SECRET") == "file-value"


def test_resolve_secret_falls_back_to_plain_env(monkeypatch):
    monkeypatch.delenv("FILE__MY_SECRET", raising=False)
    monkeypatch.setenv("MY_SECRET", "plain-value")

    assert resolve_secret("MY_SECRET") == "plain-value"


def test_resolve_secret_falls_back_to_default(monkeypatch):
    monkeypatch.delenv("FILE__MY_SECRET", raising=False)
    monkeypatch.delenv("MY_SECRET", raising=False)

    assert resolve_secret("MY_SECRET", default="fallback") == "fallback"


def test_mask_never_reveals_full_value():
    assert mask("supersecret") == "***et"
    assert mask(None) == "(not set)"
    assert mask("ab") == "***"
