"""Unit tests for bin/_vault.py."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from bin import _vault  # noqa: E402


@pytest.fixture
def passphrase_file(tmp_path):
    p = tmp_path / "vault-pass"
    p.write_text("test-passphrase-do-not-use-in-prod\n")
    p.chmod(0o600)
    return p


def test_generate_password_default_length():
    pw = _vault.generate_password()
    assert len(pw) == 24
    assert pw.isalnum()


def test_generate_password_custom_length():
    pw = _vault.generate_password(length=12)
    assert len(pw) == 12


def test_vault_exists_false_when_missing(tmp_path):
    assert _vault.vault_exists(tmp_path / "missing.yml") is False


def test_vault_write_then_read_roundtrip(tmp_path, passphrase_file):
    vault_path = tmp_path / "vault.yml"
    data = {"vault_patroni_superuser_password": "abc123XYZ"}
    _vault.vault_write(vault_path, data, passphrase_file)

    assert _vault.vault_exists(vault_path) is True
    raw = vault_path.read_text()
    assert raw.startswith("$ANSIBLE_VAULT;")
    assert "abc123XYZ" not in raw

    decrypted = _vault.vault_read(vault_path, passphrase_file)
    assert decrypted == data


def test_vault_write_overwrites(tmp_path, passphrase_file):
    vault_path = tmp_path / "vault.yml"
    _vault.vault_write(vault_path, {"key": "v1"}, passphrase_file)
    _vault.vault_write(vault_path, {"key": "v2"}, passphrase_file)
    assert _vault.vault_read(vault_path, passphrase_file) == {"key": "v2"}
