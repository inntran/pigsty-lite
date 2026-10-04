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


def test_releasever_minor_shim_runs_before_any_pgdg_metadata_fetch():
    """PGDG baseurls need $releasever_minor; Oracle Linux leaves it empty.

    The shim must land before the first task that makes dnf read PGDG
    metadata, and must defer to a $releasever_minor dnf already resolves
    (an EUS host pinned to an older minor).
    """
    tasks = _load_yaml("roles/repos/tasks/main.yml")
    names = [task.get("name") for task in tasks]

    assert "Set up PGDG $releasever_minor shim" in names
    # Nothing may touch a PGDG baseurl before the var exists. "Enable PGDG
    # repos" is the first task that does.
    assert names.index("Set up PGDG $releasever_minor shim") < names.index(
        "Enable PGDG repos (extras + selected major)"
    )

    shim = tasks[names.index("Set up PGDG $releasever_minor shim")]
    seed = next(
        task
        for task in shim["block"]
        if task.get("name") == "Seed /etc/dnf/vars/releasever_minor from the OS version"
    )
    assert seed["ansible.builtin.copy"]["dest"] == "/etc/dnf/vars/releasever_minor"
    assert any("releasever_minor" in str(cond) for cond in seed["when"])


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


def test_epel_makecache_does_not_refresh_unrelated_repos():
    """A third-party repo with broken metadata must not fail the OS baseline.

    `dnf --enablerepo=epel makecache` refreshes every *enabled* repo, so one
    bad repomd signature anywhere (Grafana shipped one on 2026-10-01) takes
    down roles/repos for every host. Scope the refresh to EPEL, which is the
    only thing the task is priming.
    """
    tasks = _load_yaml("roles/repos/tasks/main.yml")
    refresh = next(task for task in tasks if task.get("name") == "Refresh EPEL metadata")

    cmd = refresh["ansible.builtin.command"]["cmd"]
    assert "--disablerepo='*'" in cmd
    assert "--enablerepo={{ epel_repo_id }}" in cmd
