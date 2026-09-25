"""Static checks for Patroni firewall peer rules."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _load_yaml(path: str):
    with (ROOT / path).open() as fh:
        return yaml.safe_load(fh)


def _patroni_tasks():
    tasks = _load_yaml("roles/patroni/tasks/main.yml")
    return tasks


def _task_named(name: str):
    return next(task for task in _patroni_tasks() if task.get("name") == name)


def test_peer_rule_family_is_derived_not_hardcoded():
    rich_rule = _task_named(
        "Open PostgreSQL port to other cluster members (replication + pg_basebackup)"
    )["ansible.posix.firewalld"]["rich_rule"]

    assert "family='ipv4'" not in rich_rule
    assert "'ipv6' if ':' in item.value" in rich_rule


def test_peer_address_lookup_is_guarded():
    peer_addresses = _task_named("Resolve each postgres peer's address for its firewall rule")[
        "ansible.builtin.set_fact"
    ]["patroni_peer_addresses"]

    assert "patroni_advertise_address is defined" in peer_addresses
    assert "ansible_host is defined" in peer_addresses
    assert "ansible_facts" in peer_addresses
    assert "default('', true)" in peer_addresses


def test_missing_peer_address_fails_with_a_clear_message():
    tasks = _patroni_tasks()
    resolve_index = next(
        index
        for index, task in enumerate(tasks)
        if task.get("name") == "Resolve each postgres peer's address for its firewall rule"
    )
    assert_index = next(
        index
        for index, task in enumerate(tasks)
        if task.get("name") == "Fail if a postgres peer has no address"
    )
    firewall_index = next(
        index
        for index, task in enumerate(tasks)
        if task.get("name")
        == "Open PostgreSQL port to other cluster members (replication + pg_basebackup)"
    )

    assert "ansible.builtin.assert" in tasks[assert_index]
    assert resolve_index < assert_index < firewall_index


def test_peer_address_does_not_use_eager_default_on_hostvars():
    task_names = {
        "Resolve each postgres peer's address for its firewall rule",
        "Fail if a postgres peer has no address",
        "Open PostgreSQL port to other cluster members (replication + pg_basebackup)",
    }
    task_dump = yaml.safe_dump(
        [task for task in _patroni_tasks() if task.get("name") in task_names]
    )

    assert "default(hostvars[" not in task_dump
