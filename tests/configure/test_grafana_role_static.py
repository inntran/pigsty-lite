"""Static checks for Grafana role defaults."""

from __future__ import annotations

from pathlib import Path
import configparser

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _load_yaml(path: str):
    with (ROOT / path).open() as fh:
        return yaml.safe_load(fh)


def _load_ini(path: str):
    parser = configparser.ConfigParser()
    parser.read(ROOT / path)
    return parser


def test_grafana_default_port_default_is_not_recursive():
    defaults = _load_yaml("roles/grafana/defaults/main.yml")

    assert defaults["grafana_default_port"] == 3000


def test_grafana_collection_role_vars_are_typed_and_scoped():
    tasks = _load_yaml("roles/grafana/tasks/main.yml")

    include_tasks = [
        block_task
        for task in tasks
        if task.get("block")
        for block_task in task["block"]
        if block_task.get("ansible.builtin.include_role", {}).get("name")
        == "grafana.grafana.grafana"
    ]

    include_vars = include_tasks[0]["vars"]

    expected_vars = {
        "grafana_manage_repo": True,
        "grafana_rhsm_subscription": False,
        "grafana_rhsm_repo": False,
        "grafana_use_provisioning": True,
        "grafana_provisioning_synced": False,
        "grafana_provisioning_dashboards_from_file_structure": False,
        "grafana_cap_net_bind_service": False,
        "grafana_ldap": {},
        "grafana_plugins": [],
        "grafana_dashboards": [],
        "grafana_dashboards_dir": "dashboards",
        "grafana_alert_notifications": [],
        "grafana_alert_resources": {},
        "grafana_datasources": [],
        "grafana_api_keys": [],
        "grafana_environment": {},
    }

    for variable, expected_value in expected_vars.items():
        assert include_vars[variable] == expected_value


def test_ansible_collection_resolution_uses_repo_local_collections_only():
    config = _load_ini("ansible.cfg")

    assert config["defaults"]["collections_path"] == "./collections"
    assert config["defaults"]["collections_scan_sys_path"] == "False"
