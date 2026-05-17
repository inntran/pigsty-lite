"""Static checks for repository role defaults."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _load_yaml(path: str):
    with (ROOT / path).open() as fh:
        return yaml.safe_load(fh)


def test_pigsty_repo_gpg_key_uses_current_key_endpoint():
    defaults = _load_yaml("roles/repos/defaults/main.yml")

    assert defaults["repos_pigsty_gpgkey"] == "https://repo.pigsty.io/key"
