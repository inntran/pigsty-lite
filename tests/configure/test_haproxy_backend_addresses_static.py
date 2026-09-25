"""Static checks for HAProxy backend address resolution."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]

HAPROXY_ROLE = ROOT / "roles/haproxy"


def _load_yaml(path: str):
    with (ROOT / path).open() as fh:
        return yaml.safe_load(fh)


def _haproxy_tasks():
    return _load_yaml("roles/haproxy/tasks/main.yml")


def _task_named(name: str):
    return next(task for task in _haproxy_tasks() if task.get("name") == name)


def test_no_eager_default_on_hostvars_in_haproxy_role():
    for path in HAPROXY_ROLE.rglob("*"):
        if not path.is_file():
            continue
        text = path.read_text(errors="ignore")
        assert "default(hostvars[" not in text, f"{path} still uses default(hostvars[...])"


def test_template_reads_the_resolved_map():
    template = (HAPROXY_ROLE / "templates/haproxy.cfg.j2").read_text()
    assert template.count("haproxy_backend_addresses[h]") == 3


def test_resolve_task_is_guarded_and_tagged_for_both_consumers():
    task = _task_named("Resolve each postgres member's backend address")
    value = task["ansible.builtin.set_fact"]["haproxy_backend_addresses"]

    assert "patroni_advertise_address is defined" in value
    assert "ansible_host is defined" in value
    assert "ansible_facts" in value
    assert "default('', true)" in value
    assert "config" in task["tags"]
    assert "firewall" in task["tags"]


def test_assert_sits_between_resolve_and_render():
    tasks = _haproxy_tasks()
    resolve_index = next(
        index
        for index, task in enumerate(tasks)
        if task.get("name") == "Resolve each postgres member's backend address"
    )
    assert_index = next(
        index
        for index, task in enumerate(tasks)
        if task.get("name") == "Fail if a postgres member has no backend address"
    )
    render_index = next(
        index for index, task in enumerate(tasks) if task.get("name") == "Render haproxy.cfg"
    )

    assert resolve_index < assert_index < render_index


def test_backend_firewall_rule_reads_the_map():
    task = _task_named("Open haproxy backend port from postgres peer hosts")

    assert "haproxy_backend_addresses" in task["loop"]
    rich_rule = task["ansible.posix.firewalld"]["rich_rule"]
    assert "'ipv6' if ':' in item.value" in rich_rule
