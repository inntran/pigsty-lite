"""Static checks for monitoring role behavior."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _walk_tasks(tasks):
    """Yield tasks including those nested in block/rescue/always."""
    for task in tasks or []:
        for key in ("block", "rescue", "always"):
            if key in task:
                yield from _walk_tasks(task[key])
        yield task


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
        task for task in alertmanager_tasks if task.get("name") == "Install Alertmanager"
    )["ansible.builtin.dnf"]

    assert alertmanager_install["enablerepo"] == "{{ epel_repo_id }}"


def test_exporters_install_from_pinned_tarballs_not_dnf():
    """EPEL carries none of the four exporters for EL10, so a dnf install
    would fail at deploy time. They come from pinned release tarballs."""
    exporter_tasks = _load_yaml("roles/monitoring_agents/tasks/_exporters.yml")

    assert not [task for task in exporter_tasks if "ansible.builtin.dnf" in task], (
        "_exporters.yml installs with dnf; no EL10 repo packages these exporters"
    )

    includes = [
        task
        for task in exporter_tasks
        if task.get("ansible.builtin.include_tasks") == "_install_exporter.yml"
    ]
    assert len(includes) == 2, "expected node_exporter plus the PG-side loop"


def test_exporter_install_verifies_a_checksum():
    """An unverified download would install whatever the network returned."""
    tasks = _load_yaml("roles/monitoring_agents/tasks/_install_exporter.yml")
    downloads = [task for task in _walk_tasks(tasks) if "ansible.builtin.get_url" in task]
    assert downloads, "expected a get_url task"
    for task in downloads:
        checksum = task["ansible.builtin.get_url"].get("checksum", "")
        assert checksum.startswith("sha256:"), "the tarball must be checksum-verified"
