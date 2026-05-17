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


def test_monitoring_server_does_not_pass_recursive_collection_vars():
    tasks = _load_yaml("roles/monitoring_server/tasks/main.yml")
    text = yaml.safe_dump(tasks)

    assert "{{ victoriametrics_service_args" not in text
    assert "{{ victorialogs_service_args" not in text


def test_monitoring_server_creates_alertmanager_identity_before_data_dir():
    tasks = _load_yaml("roles/monitoring_server/tasks/_alertmanager.yml")
    group_index = next(index for index, task in enumerate(tasks) if "ansible.builtin.group" in task)
    user_index = next(index for index, task in enumerate(tasks) if "ansible.builtin.user" in task)
    data_dir_index = next(
        index
        for index, task in enumerate(tasks)
        if task.get("ansible.builtin.file", {}).get("path")
        == "{{ monitoring_server_alertmanager_data_dir }}"
    )

    assert group_index < user_index < data_dir_index


def test_monitoring_epel_packages_explicitly_enable_epel_repo():
    alertmanager_tasks = _load_yaml("roles/monitoring_server/tasks/_alertmanager.yml")
    alertmanager_install = next(
        task
        for task in alertmanager_tasks
        if task.get("name") == "Install Alertmanager"
    )["ansible.builtin.dnf"]

    assert alertmanager_install["enablerepo"] == "{{ monitoring_server_epel_repo_id }}"

    exporter_tasks = _load_yaml("roles/monitoring_agents/tasks/_exporters.yml")
    node_exporter_install = next(
        task
        for task in exporter_tasks
        if task.get("name") == "Install node_exporter"
    )["ansible.builtin.dnf"]
    pg_exporter_install = next(
        task
        for task in exporter_tasks
        if task.get("name") == "Install the PostgreSQL-side exporters"
    )["ansible.builtin.dnf"]

    assert node_exporter_install["enablerepo"] == "{{ monitoring_agents_epel_repo_id }}"
    assert pg_exporter_install["enablerepo"] == "{{ monitoring_agents_epel_repo_id }}"
