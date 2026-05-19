"""Thin wrapper around `ansible-vault` CLI for keyed secret storage.

The vault file is a YAML mapping of {key: value} encrypted as a single
ansible-vault blob. We never hold plaintext on disk; reads decrypt to
memory, writes encrypt from memory.
"""

from __future__ import annotations

import secrets
import string
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import yaml

PASSWORD_ALPHABET = string.ascii_letters + string.digits


def generate_password(length: int = 24) -> str:
    """Cryptographically random alphanumeric password."""
    return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(length))


def vault_exists(path: Path) -> bool:
    return Path(path).is_file()


_VAULT_ID_LABEL = "pigsty"


def vault_read(path: Path, passphrase_file: Path) -> dict[str, Any]:
    """Decrypt path with the passphrase file and parse as YAML mapping."""
    result = subprocess.run(
        [
            "ansible-vault",
            "view",
            "--vault-id",
            f"{_VAULT_ID_LABEL}@{passphrase_file}",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    data = yaml.safe_load(result.stdout) or {}
    if not isinstance(data, dict):
        raise ValueError(f"vault contents at {path} must be a YAML mapping")
    return data


def vault_write(path: Path, data: dict[str, Any], passphrase_file: Path) -> None:
    """Encrypt data as YAML and write to path, overwriting if present."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False, dir=path.parent) as tmp:
        yaml.safe_dump(data, tmp, sort_keys=True, default_flow_style=False)
        tmp_path = Path(tmp.name)

    try:
        subprocess.run(
            [
                "ansible-vault",
                "encrypt",
                "--vault-id",
                f"{_VAULT_ID_LABEL}@{passphrase_file}",
                "--encrypt-vault-id",
                _VAULT_ID_LABEL,
                "--output",
                str(path),
                str(tmp_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        path.chmod(0o600)
    finally:
        tmp_path.unlink(missing_ok=True)
