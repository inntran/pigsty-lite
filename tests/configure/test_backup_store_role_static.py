"""Static checks for backup_store role behavior."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _load_yaml(path: str):
    with (ROOT / path).open() as fh:
        return yaml.safe_load(fh)


def _iter_tasks(tasks):
    for task in tasks:
        yield task
        for key in ("block", "rescue", "always"):
            if key in task:
                yield from _iter_tasks(task[key])


def test_backup_store_does_not_restrict_monitor_host_ssh():
    tasks = _load_yaml("roles/backup_store/tasks/main.yml")
    firewall_tasks = [
        task
        for task in _iter_tasks(tasks)
        if task.get("ansible.posix.firewalld") is not None
    ]

    assert firewall_tasks
    assert all(
        "inventory_hostname not in (groups['monitor'] | default([]))" in task["when"]
        for task in firewall_tasks
    )


def test_backup_store_forces_pgbackrest_only_ssh_access():
    tasks = _load_yaml("roles/backup_store/tasks/_ssh.yml")
    auth_tasks = [
        task
        for task in _iter_tasks(tasks)
        if task.get("ansible.posix.authorized_key") is not None
    ]

    assert len(auth_tasks) == 1
    key = auth_tasks[0]["ansible.posix.authorized_key"]["key"]
    assert 'command="/usr/bin/pgbackrest server"' in key
    assert "restrict" in key
    assert 'from="' in key
