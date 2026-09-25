"""Static checks for Patroni configuration handlers."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _load_yaml(path: str):
    with (ROOT / path).open() as fh:
        return yaml.safe_load(fh)


def test_patroni_yml_change_reloads_not_restarts():
    tasks = _load_yaml("roles/patroni/tasks/_configure.yml")

    task = next(task for task in tasks if task.get("name") == "Render patroni.yml")

    assert task["notify"] == "Reload patroni"


def test_dropin_change_notifies_the_dropin_topic():
    tasks = _load_yaml("roles/patroni/tasks/_configure.yml")

    task = next(
        task
        for task in tasks
        if task.get("ansible.builtin.template", {}).get("src") == "systemd-override.conf.j2"
    )

    assert task["notify"] == "patroni drop-in changed"


def test_running_state_is_recorded_before_any_render():
    tasks = _load_yaml("roles/patroni/tasks/_configure.yml")

    assert tasks[0]["register"] == "patroni_was_active"

    template_indices = [
        index for index, task in enumerate(tasks) if "ansible.builtin.template" in task
    ]
    assert template_indices
    assert all(index > 0 for index in template_indices)


def test_reload_only_acts_on_a_previously_running_cluster():
    handlers = _load_yaml("roles/patroni/handlers/main.yml")

    reload = next(handler for handler in handlers if handler["name"] == "Reload patroni")

    assert "patroni_was_active" in reload["when"]


def test_reload_surfaces_pending_restart_instead_of_restarting():
    handlers = _load_yaml("roles/patroni/handlers/main.yml")

    reload_index = next(
        index for index, handler in enumerate(handlers) if handler["name"] == "Reload patroni"
    )
    reload_listeners = [
        (index, handler)
        for index, handler in enumerate(handlers)
        if handler.get("listen") == "Reload patroni"
    ]

    assert any(
        handler.get("ansible.builtin.uri", {}).get("url", "").endswith("/patroni")
        for _, handler in reload_listeners
    )
    assert any(
        any("pending_restart" in condition for condition in handler.get("when", []))
        for _, handler in reload_listeners
        if "ansible.builtin.debug" in handler
    )
    assert all(index > reload_index for index, _ in reload_listeners)


def test_no_handler_restarts_patroni():
    handlers = _load_yaml("roles/patroni/handlers/main.yml")

    assert all(
        handler.get("ansible.builtin.systemd", {}).get("state") != "restarted"
        for handler in handlers
    )


def test_dropin_daemon_reload_is_unconditional():
    handlers = _load_yaml("roles/patroni/handlers/main.yml")

    daemon_reload = next(
        handler
        for handler in handlers
        if handler["name"] == "Reload systemd for the patroni drop-in"
    )

    assert daemon_reload["ansible.builtin.systemd"]["daemon_reload"] is True
    assert daemon_reload["listen"] == "patroni drop-in changed"
    assert "when" not in daemon_reload
