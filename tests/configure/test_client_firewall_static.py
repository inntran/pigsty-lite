"""Static checks for database client firewall scope."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _load_yaml(path: str):
    with (ROOT / path).open() as fh:
        return yaml.safe_load(fh)


def _task_named(tasks: list[dict], name: str):
    return next(task for task in tasks if task.get("name") == name)


def _walk(value):
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _walk(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk(nested)


def test_database_client_services_are_not_enabled_zone_wide():
    for path in (ROOT / "roles").rglob("*.yml"):
        if "tasks" not in path.parts:
            continue
        with path.open() as fh:
            content = yaml.safe_load(fh)

        for item in _walk(content):
            firewalld = item.get("ansible.posix.firewalld")
            if not isinstance(firewalld, dict):
                continue
            assert not (
                firewalld.get("service") in {"postgresql", "haproxy-postgres", "pgbouncer"}
                and firewalld.get("state") == "enabled"
            ), f"{path.relative_to(ROOT)} enables a database service zone-wide"


def test_haproxy_client_rule_covers_both_services_by_client_cidr():
    tasks = _load_yaml("roles/haproxy/tasks/main.yml")
    task = _task_named(tasks, "Open client ports to postgres_client_cidrs")
    firewalld = task["ansible.posix.firewalld"]

    assert "haproxy_client_cidrs" in task["loop"]
    assert "postgresql" in task["loop"]
    assert "haproxy-postgres" in task["loop"]
    assert "item.0" in firewalld["rich_rule"]
    assert "item.1" in firewalld["rich_rule"]
    assert firewalld["permanent"] is True
    assert firewalld["immediate"] is True


def test_pgbouncer_is_closed_zone_wide_and_client_rule_follows_the_flag():
    """The client rule must run in both states: with a `when:` instead,
    turning direct access off would leave earlier rules in place."""
    tasks = _load_yaml("roles/pgbouncer/tasks/main.yml")
    zone_wide = _task_named(tasks, "Keep the pgbouncer service closed zone-wide")
    assert zone_wide["ansible.posix.firewalld"]["service"] == (
        "{{ pgbouncer_firewalld_service_name }}"
    )
    assert zone_wide["ansible.posix.firewalld"]["state"] == "disabled"

    client_rule = _task_named(
        tasks, "Admit postgres_client_cidrs to pgBouncer only when explicitly enabled"
    )
    firewalld = client_rule["ansible.posix.firewalld"]
    assert "when" not in client_rule
    assert "pgbouncer_firewalld_enabled" in firewalld["state"]
    assert "'disabled'" in firewalld["state"]
    assert "pgbouncer_client_cidrs" in client_rule["loop"]
    assert "pgbouncer_firewalld_service_name" in firewalld["rich_rule"]
    assert "item" in firewalld["rich_rule"]
    assert firewalld["permanent"] is True
    assert firewalld["immediate"] is True


def test_client_cidrs_are_not_used_by_intra_cluster_firewall_rules():
    paths = (
        "roles/patroni/tasks/main.yml",
        "roles/etcd/tasks/_firewall.yml",
        "roles/pgbackrest/tasks/_firewall.yml",
    )
    for path in paths:
        text = (ROOT / path).read_text()
        assert "postgres_client_cidrs" not in text
        assert "haproxy_client_cidrs" not in text
