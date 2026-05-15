"""Static checks for monitoring role behavior."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _load_yaml(path: str):
    with (ROOT / path).open() as fh:
        return yaml.safe_load(fh)


def test_monitoring_server_main_imports_existing_task_files():
    tasks = _load_yaml("roles/monitoring_server/tasks/main.yml")
    imported = [
        task["ansible.builtin.import_tasks"]
        for task in tasks
        if "ansible.builtin.import_tasks" in task
    ]

    assert imported
    for relative_path in imported:
        assert (ROOT / "roles/monitoring_server/tasks" / relative_path).exists()


def test_monitoring_agents_remote_write_defaults_use_http_scheme():
    defaults = _load_yaml("roles/monitoring_agents/defaults/main.yml")

    assert defaults["monitoring_agents_vmagent_remote_write_url"].startswith("http://")
    assert defaults["monitoring_agents_vlagent_remote_write_url"].startswith("http://")
