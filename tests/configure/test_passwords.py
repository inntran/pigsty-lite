"""Unit tests for bin/_passwords.py orchestration."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from bin import _passwords  # noqa: E402


def test_machine_secrets_includes_patroni_trio():
    keys = {s.key for s in _passwords.MACHINE_SECRETS}
    assert "vault_patroni_superuser_password" in keys
    assert "vault_patroni_replication_password" in keys
    assert "vault_patroni_rewind_password" in keys


def test_ensure_machine_secrets_fills_missing():
    existing = {"vault_patroni_superuser_password": "preexisting"}
    updated = _passwords.ensure_machine_secrets(existing)
    assert updated["vault_patroni_superuser_password"] == "preexisting"
    assert "vault_patroni_replication_password" in updated
    assert len(updated["vault_patroni_replication_password"]) == 24
    assert updated["vault_patroni_replication_password"].isalnum()


def test_ensure_machine_secrets_idempotent():
    first = _passwords.ensure_machine_secrets({})
    second = _passwords.ensure_machine_secrets(dict(first))
    assert first == second


def test_human_secrets_includes_grafana():
    keys = {s.key for s in _passwords.HUMAN_SECRETS}
    assert "vault_grafana_admin_password" in keys


def test_ensure_human_secrets_calls_prompter_for_missing():
    prompted: list[str] = []

    def fake_prompt(label: str) -> str:
        prompted.append(label)
        return f"typed-{label}"

    updated = _passwords.ensure_human_secrets({}, prompter=fake_prompt)
    assert len(prompted) == len(_passwords.HUMAN_SECRETS)
    for secret in _passwords.HUMAN_SECRETS:
        assert updated[secret.key] == f"typed-{secret.prompt}"


def test_ensure_human_secrets_skips_existing():
    prompted: list[str] = []

    def fake_prompt(label: str) -> str:
        prompted.append(label)
        return "should-not-be-used"

    existing = {s.key: f"value-{s.key}" for s in _passwords.HUMAN_SECRETS}
    updated = _passwords.ensure_human_secrets(dict(existing), prompter=fake_prompt)
    assert prompted == []
    assert updated == existing


def test_rotate_replaces_named_key():
    existing = {
        "vault_patroni_superuser_password": "old",
        "vault_patroni_replication_password": "other",
    }
    updated = _passwords.rotate(existing, "vault_patroni_superuser_password")
    assert updated["vault_patroni_superuser_password"] != "old"
    assert len(updated["vault_patroni_superuser_password"]) == 24
    assert updated["vault_patroni_replication_password"] == "other"


def test_rotate_unknown_key_raises():
    with pytest.raises(ValueError, match="unknown secret key"):
        _passwords.rotate({}, "vault_does_not_exist")
