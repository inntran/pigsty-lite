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


def test_epel_is_installed_by_default_but_repo_is_disabled():
    defaults = _load_yaml("roles/repos/defaults/main.yml")
    tasks = _load_yaml("roles/repos/tasks/main.yml")

    # epel-release is always installed; repos_epel_enabled selects EPEL's
    # terminal state, and the safe default is disabled for normal resolution.
    assert defaults["repos_epel_enabled"] is False
    assert defaults["epel_repo_id"] == "epel"

    disable_tasks = [
        task
        for task in tasks
        if task.get("name") == "Disable EPEL for normal dependency resolution"
    ]
    assert disable_tasks

    # The task disables EPEL via `dnf config-manager --set-disabled`.
    cmd = disable_tasks[0]["ansible.builtin.command"]["cmd"]
    assert "config-manager --set-disabled" in cmd
    assert "{{ epel_repo_id }}" in cmd
