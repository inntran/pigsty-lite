"""Static checks for Molecule cert SAN setup."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
MOLECULE_CONTAINER_IP_EXPR = "{{ ansible_facts['default_ipv4']['address'] }}"


def _load_yaml(path: Path):
    with path.open() as fh:
        return yaml.safe_load(fh)


def test_shared_molecule_config_pins_cert_ip_san_to_container_ip():
    config = _load_yaml(ROOT / ".config/molecule/config.yml")
    group_vars = config["provisioner"]["inventory"]["group_vars"]["all"]

    assert group_vars["certs_default_ip_san_address"] == MOLECULE_CONTAINER_IP_EXPR


def test_shared_node_playbook_imports_use_common_molecule_cert_san_config():
    prepare_files = sorted((ROOT / "tests/molecule").glob("*/molecule/*/prepare.yml"))
    node_imports = []

    for path in prepare_files:
        for entry in _load_yaml(path):
            if entry.get("ansible.builtin.import_playbook", "").endswith("_node.yml"):
                node_imports.append((path, entry))

    assert node_imports
    for path, entry in node_imports:
        assert "certs_subject_alternative_names" not in entry.get("vars", {}), path


def test_nginx_proxy_prepare_uses_common_molecule_cert_san_config():
    prepare = _load_yaml(ROOT / "tests/molecule/nginx_proxy/molecule/default/prepare.yml")

    for play in prepare:
        assert "pre_tasks" not in play


def test_molecule_scenarios_do_not_pin_epel_repo_state():
    """Roles reach EPEL packages via `enablerepo`, so no scenario (nor the
    shared config) should pin repos_epel_enabled — molecule must exercise the
    same disabled-by-default state production uses."""
    # Globbed rather than listed, so a new scenario cannot slip past this.
    # external_pull predates the rule and is grandfathered in.
    grandfathered = {"tests/molecule/monitoring_agents/molecule/external_pull/molecule.yml"}
    scenario_files = [
        path
        for path in sorted((ROOT / "tests/molecule").glob("*/molecule/*/molecule.yml"))
        if str(path.relative_to(ROOT)) not in grandfathered
    ]

    assert scenario_files, "expected molecule scenarios to check"
    for path in scenario_files:
        molecule = _load_yaml(path)
        group_vars = (
            molecule.get("provisioner", {})
            .get("inventory", {})
            .get("group_vars", {})
            .get("all", {})
        )
        assert "repos_epel_enabled" not in group_vars, path
        assert "repos_pigsty_enabled" not in group_vars, path

    config = _load_yaml(ROOT / ".config/molecule/config.yml")
    group_vars = config["provisioner"]["inventory"]["group_vars"]["all"]
    assert "repos_epel_enabled" not in group_vars
