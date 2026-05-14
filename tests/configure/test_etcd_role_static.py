"""Static checks for etcd role behavior that is hard to unit-test locally."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _load_yaml(path: str):
    with (ROOT / path).open() as fh:
        return yaml.safe_load(fh)


def test_etcd_firewall_uses_source_restricted_rich_rules():
    tasks = _load_yaml("roles/etcd/tasks/_firewall.yml")
    firewalld_args = [
        task["ansible.posix.firewalld"] for task in tasks if "ansible.posix.firewalld" in task
    ]

    assert firewalld_args
    assert all("rich_rule" in args for args in firewalld_args)
    assert all("service" not in args for args in firewalld_args)
    assert any('service name="etcd-server"' in args["rich_rule"] for args in firewalld_args)
    assert any('service name="etcd-client"' in args["rich_rule"] for args in firewalld_args)
    assert any("source address=" in args["rich_rule"] for args in firewalld_args)


def test_etcd_advertise_fallback_prefers_ipv6_before_ipv4():
    defaults = _load_yaml("roles/etcd/defaults/main.yml")
    expression = defaults["etcd_advertise_address"]

    assert "default_ipv6" in expression
    assert "default_ipv4" in expression
    assert expression.index("default_ipv6") < expression.index("default_ipv4")


def test_etcd_flushes_config_handlers_before_health_gate():
    tasks = _load_yaml("roles/etcd/tasks/main.yml")
    flush_index = next(
        index
        for index, task in enumerate(tasks)
        if task.get("ansible.builtin.meta") == "flush_handlers"
    )
    service_start_index = next(
        index
        for index, task in enumerate(tasks)
        if task.get("ansible.builtin.systemd", {}).get("name") == "{{ etcd_service_name }}"
    )
    health_gate_index = next(
        index
        for index, task in enumerate(tasks)
        if "endpoint health" in task.get("ansible.builtin.command", {}).get("cmd", "")
    )

    assert flush_index < service_start_index
    assert flush_index < health_gate_index
