"""Static checks for postgres role behavior."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _load_yaml(path: str):
    with (ROOT / path).open() as fh:
        return yaml.safe_load(fh)


def test_postgres_extension_packages_default_empty():
    defaults = _load_yaml("roles/postgres/defaults/main.yml")

    assert defaults["postgres_extension_packages"] == []


def test_postgres_extension_packages_install_task_after_core_packages():
    tasks = _load_yaml("roles/postgres/tasks/main.yml")
    task_names = [task.get("name") for task in tasks]

    core_index = task_names.index("Install PostgreSQL server, contrib, and libs packages")
    extension_index = task_names.index("Install PostgreSQL extension packages")

    assert core_index < extension_index

    extension_task = tasks[extension_index]
    assert extension_task["ansible.builtin.dnf"]["name"] == "{{ postgres_extension_packages }}"
    assert extension_task["ansible.builtin.dnf"]["state"] == "present"
    assert extension_task["when"] == "postgres_extension_packages | length > 0"
    assert set(extension_task["tags"]) == {"postgres", "install"}
