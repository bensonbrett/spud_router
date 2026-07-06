# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""
Tests for the API key authentication system.
"""
import pytest
import subprocess

import backend.api_keys as api_keys_module
import backend.auth as auth_module
import backend.state as state_module


def _ok_run(*a, **k):
    return subprocess.CompletedProcess(a, 0, "", "")


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    conf_dir = tmp_path / "spud-router"
    state_file = conf_dir / "state.json"
    auth_file = conf_dir / "auth.json"
    api_keys_file = conf_dir / "api-keys.json"

    monkeypatch.setattr(state_module, "SPUD_CONF", conf_dir)
    monkeypatch.setattr(state_module, "STATE_FILE", state_file)
    monkeypatch.setattr(auth_module, "AUTH_FILE", auth_file)
    monkeypatch.setattr(auth_module, "SPUD_CONF", conf_dir)
    monkeypatch.setattr(auth_module, "CLI_TOKEN_FILE", conf_dir / "cli-token")
    monkeypatch.setattr(auth_module, "TOKEN_SECRET_FILE", conf_dir / "token-secret")
    monkeypatch.setattr(api_keys_module, "SPUD_CONF", conf_dir)
    monkeypatch.setattr(api_keys_module, "API_KEYS_FILE", api_keys_file)
    monkeypatch.setattr(api_keys_module, "_validation_failures", {})

    return {"conf_dir": conf_dir, "api_keys_file": api_keys_file}


class TestApiKeyCreation:
    def test_create_key_returns_plaintext_and_stores_hash(self, isolated_env):
        plaintext, stored = api_keys_module.create_key(
            name="test-key",
            scopes=["read", "write"],
        )
        assert plaintext.startswith("spud_")
        assert len(plaintext) == 45
        assert stored["name"] == "test-key"
        assert stored["scopes"] == ["read", "write"]
        assert stored["key_hash"].startswith("sha256:")

    def test_create_key_plaintext_not_stored(self, isolated_env):
        plaintext, stored = api_keys_module.create_key(name="test", scopes=["read"])
        assert plaintext not in stored["key_hash"]

    def test_create_key_invalid_scope_raises(self, isolated_env):
        with pytest.raises(ValueError, match="Invalid scope"):
            api_keys_module.create_key(name="test", scopes=["invalid_scope"])

    def test_create_key_id_is_unique(self, isolated_env):
        _, s1 = api_keys_module.create_key(name="k1", scopes=["read"])
        _, s2 = api_keys_module.create_key(name="k2", scopes=["read"])
        assert s1["id"] != s2["id"]


class TestApiKeyValidation:
    def test_validate_valid_key(self, isolated_env):
        plaintext, _ = api_keys_module.create_key(name="test", scopes=["read"])
        ctx = api_keys_module.validate_key(plaintext)
        assert ctx is not None
        assert ctx.name == "test"
        assert ctx.scopes == frozenset({"read"})

    def test_validate_invalid_key_returns_none(self, isolated_env):
        ctx = api_keys_module.validate_key("spud_invalidkey12345678901234567890")
        assert ctx is None

    def test_validate_key_not_spud_prefix_returns_none(self, isolated_env):
        ctx = api_keys_module.validate_key("not_a_spud_key_12345678901234")
        assert ctx is None

    def test_validate_key_updates_last_used(self, isolated_env):
        plaintext, stored = api_keys_module.create_key(name="test", scopes=["read"])
        api_keys_module.validate_key(plaintext)
        api_keys_module.validate_key(plaintext)
        keys = api_keys_module.list_keys()
        assert keys[0]["last_used"] is not None


class TestApiKeyListing:
    def test_list_keys_no_hashes(self, isolated_env):
        api_keys_module.create_key(name="k1", scopes=["read"])
        api_keys_module.create_key(name="k2", scopes=["write"])
        keys = api_keys_module.list_keys()
        assert len(keys) == 2
        for k in keys:
            assert "key_hash" not in k
            assert "id" in k
            assert "name" in k


class TestApiKeyRevocation:
    def test_revoke_key(self, isolated_env):
        _, stored = api_keys_module.create_key(name="test", scopes=["read"])
        assert api_keys_module.revoke_key(stored["id"]) is True
        keys = api_keys_module.list_keys()
        assert len(keys) == 0

    def test_revoke_nonexistent_returns_false(self, isolated_env):
        assert api_keys_module.revoke_key("ak_nonexistent") is False


class TestApiKeyRateLimiting:
    def test_record_failure_and_check(self, isolated_env):
        assert api_keys_module.check_rate_limit("192.168.1.1") is False
        for _ in range(9):
            api_keys_module.record_failure("192.168.1.1")
        assert api_keys_module.check_rate_limit("192.168.1.1") is False
        api_keys_module.record_failure("192.168.1.1")
        assert api_keys_module.check_rate_limit("192.168.1.1") is True

    def test_rate_limit_prunes_old_failures(self, isolated_env, monkeypatch):
        import time
        now = time.time()
        monkeypatch.setattr(time, "time", lambda: now)
        for _ in range(9):
            api_keys_module.record_failure("192.168.1.2")
        monkeypatch.setattr(time, "time", lambda: now + 61)
        assert api_keys_module.check_rate_limit("192.168.1.2") is False
