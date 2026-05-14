"""Static checks for HAProxy role operational behavior."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _load_tasks(path: str) -> list[dict]:
    with (ROOT / path).open() as fh:
        return yaml.safe_load(fh)


def test_haproxy_role_enables_nonlocal_bind_for_vip_manager():
    main_tasks = _load_tasks("roles/haproxy/tasks/main.yml")

    template_tasks = [
        task["ansible.builtin.template"]
        for task in main_tasks
        if "ansible.builtin.template" in task
    ]
    assert any(
        task["src"] == "haproxy-vip-sysctl.conf.j2"
        and task["dest"] == "/etc/sysctl.d/90-pigsty-lite-haproxy-vip.conf"
        for task in template_tasks
    )
    assert any(
        task.get("ansible.builtin.command", {}).get("cmd") == "sysctl --system"
        for task in main_tasks
    )


def test_haproxy_service_waits_for_bound_ports_not_loopback_data_ports():
    service_tasks = _load_tasks("roles/haproxy/tasks/main.yml")

    assert any(
        "ss -H -ltn" in task.get("ansible.builtin.command", {}).get("cmd", "")
        for task in service_tasks
    )
    assert not any(
        task.get("ansible.builtin.wait_for", {}).get("host")
        == "{{ network_loopback_address | default('127.0.0.1') }}"
        and "{{ haproxy_default_port }}" in task.get("loop", [])
        for task in service_tasks
    )
