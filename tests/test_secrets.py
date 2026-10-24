from __future__ import annotations

import pytest

from hexgate.secrets import SecretError, SecretStore, mask_key


def test_set_get_remove(tmp_path):
    store = SecretStore(tmp_path / "secrets.json")
    assert store.load() == {}
    store.set_keys("groq", ["sk-one", "sk-two"])
    assert store.keys("groq") == ["sk-one", "sk-two"]
    store.set_keys("groq", ["sk-three"])
    assert store.keys("groq") == ["sk-three"]
    store.remove("groq")
    assert store.keys("groq") == []


def test_file_is_encrypted(tmp_path):
    path = tmp_path / "secrets.json"
    store = SecretStore(path)
    store.set_keys("groq", ["sk-super-secret-value"])
    raw = path.read_text(encoding="utf-8")
    assert "sk-super-secret-value" not in raw
    assert '"alg": "AES-256-GCM"' in raw


def test_wrong_password_fails_loudly(tmp_path):
    path = tmp_path / "secrets.json"
    writer = SecretStore(path, password="correct horse")
    writer.set_keys("groq", ["sk-one"])
    reader = SecretStore(path, password="wrong horse")
    with pytest.raises(SecretError):
        reader.load()


def test_env_keys_are_merged_first(tmp_path, monkeypatch):
    store = SecretStore(tmp_path / "secrets.json")
    store.set_keys("groq", ["sk-stored"])
    monkeypatch.setenv("TEST_GROQ_KEY", "sk-env")
    assert store.keys("groq", ["TEST_GROQ_KEY"]) == ["sk-env", "sk-stored"]
    assert store.keys("groq", ["MISSING_KEY"]) == ["sk-stored"]


def test_masking():
    assert mask_key("sk-1234567890abcd").startswith("sk-123")
    assert mask_key("sk-1234567890abcd").endswith("abcd")
    assert "•" not in mask_key("sk-1234567890abcd")
    assert mask_key("short") == "sh•••"


def test_multiple_providers_isolated(tmp_path):
    store = SecretStore(tmp_path / "secrets.json")
    store.set_keys("groq", ["g"])
    store.set_keys("cerebras", ["c"])
    assert set(store.load()) == {"groq", "cerebras"}
    store.remove("groq")
    assert store.load() == {"cerebras": ["c"]}
