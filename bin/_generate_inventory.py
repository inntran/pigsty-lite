"""Inventory generator: response dict -> inventory YAML string."""

from __future__ import annotations

from io import StringIO
from typing import Any

import yaml

BANNER = (
    "# GENERATED FILE - DO NOT EDIT; regenerate with configure.\n"
    "# Command: ./configure -s -f responses/site.rsp.yml\n"
    "# Source of truth: responses/site.rsp.yml\n"
    "---\n"
)


def _split_nodes_by_role(nodes: dict[str, dict]) -> dict[str, list[tuple[str, dict]]]:
    by_role: dict[str, list[tuple[str, dict]]] = {
        "monitor": [],
        "backup_store": [],
        "pg_primary": [],
        "pg_replica": [],
    }
    for name, node in nodes.items():
        by_role[node["role"]].append((name, node))
    return by_role


def _build_group(hosts: list[tuple[str, dict[str, Any]]]) -> dict:
    return {"hosts": {name: host_vars for name, host_vars in hosts}}


def generate(response: dict[str, Any]) -> str:
    """Produce inventory/site.yml content from a validated response dict."""
    by_role = _split_nodes_by_role(response["nodes"])

    monitor_hosts = [(name, {"ansible_host": node["ip"]}) for name, node in by_role["monitor"]]

    if by_role["backup_store"]:
        backup_hosts = [
            (name, {"ansible_host": node["ip"]}) for name, node in by_role["backup_store"]
        ]
    else:
        backup_hosts = monitor_hosts.copy()

    pg_nodes = by_role["pg_primary"] + by_role["pg_replica"]
    pg_hosts: list[tuple[str, dict[str, Any]]] = []
    for seq, (name, node) in enumerate(pg_nodes, start=1):
        host_vars = {
            "ansible_host": node["ip"],
            "postgres_role": "primary" if node["role"] == "pg_primary" else "replica",
            "postgres_seq": seq,
        }
        pg_hosts.append((name, host_vars))

    etcd_hosts: list[tuple[str, dict[str, Any]]] = []
    for seq, (name, node) in enumerate(pg_nodes, start=1):
        etcd_hosts.append((name, {"ansible_host": node["ip"], "etcd_seq": seq}))

    inventory = {
        "all": {
            "children": {
                "monitor": _build_group(monitor_hosts),
                "backup_store": _build_group(backup_hosts),
                "etcd": _build_group(etcd_hosts),
                "postgres": _build_group(pg_hosts),
            }
        }
    }

    buf = StringIO()
    buf.write(BANNER)
    yaml.safe_dump(inventory, buf, sort_keys=False, default_flow_style=False)
    return buf.getvalue()
