"""Static checks for Molecule cert SAN setup."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
EXPECTED_SANS = [
    "DNS:{{ inventory_hostname }}",
    "DNS:{{ inventory_hostname }}.test.local",
    "IP:{{ ansible_facts['default_ipv4']['address'] }}",
]


def _load_yaml(path: Path):
    with path.open() as fh:
        return yaml.safe_load(fh)


def test_shared_node_playbook_imports_pin_cert_sans_to_container_ip():
    prepare_files = sorted((ROOT / "tests/molecule").glob("*/molecule/*/prepare.yml"))
    node_imports = []

    for path in prepare_files:
        for entry in _load_yaml(path):
            if entry.get("ansible.builtin.import_playbook", "").endswith("_node.yml"):
                node_imports.append((path, entry))

    assert node_imports
    for path, entry in node_imports:
        assert entry.get("vars", {}).get("certs_subject_alternative_names") == EXPECTED_SANS, path


def test_nginx_proxy_prepare_pins_cert_sans_to_container_ip():
    prepare = _load_yaml(ROOT / "tests/molecule/nginx_proxy/molecule/default/prepare.yml")

    assert (
        prepare[0]["pre_tasks"][0]["ansible.builtin.set_fact"]["certs_subject_alternative_names"]
        == EXPECTED_SANS
    )


def test_monitoring_scenarios_enable_repos_for_alertmanager_package():
    scenario_files = [
        "tests/molecule/grafana/molecule/default/molecule.yml",
        "tests/molecule/monitoring_agents/molecule/default/molecule.yml",
        "tests/molecule/monitoring_server/molecule/default/molecule.yml",
        "tests/molecule/nginx_proxy/molecule/default/molecule.yml",
    ]

    for path in scenario_files:
        molecule = _load_yaml(ROOT / path)
        group_vars = molecule["provisioner"]["inventory"]["group_vars"]["all"]
        assert group_vars["repos_epel_enabled"] is True, path
        assert group_vars["repos_pigsty_enabled"] is True, path
