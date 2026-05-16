"""Static checks for backup Molecule topology contracts."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _load_molecule(path: str) -> dict:
    with (ROOT / path).open() as fh:
        return yaml.safe_load(fh)


def test_backup_default_uses_dedicated_infra_node():
    data = _load_molecule("tests/molecule/backup/molecule/default/molecule.yml")
    platforms = data["platforms"]

    infra_nodes = []
    data_nodes = []
    for platform in platforms:
        groups = set(platform.get("groups", []))
        if {"monitor", "backup_server"}.issubset(groups):
            infra_nodes.append(groups)
        if {"postgres", "etcd"}.issubset(groups):
            data_nodes.append(groups)

    assert infra_nodes, "backup/default must include a dedicated infra node"
    assert all("postgres" not in groups and "etcd" not in groups for groups in infra_nodes)
    assert data_nodes, "backup/default must include at least one data node"


def test_backup_server_hosts_use_server_suffix():
    molecule_files = ROOT.glob("tests/molecule/*/molecule/*/molecule.yml")

    backup_server_hosts: list[str] = []
    bad_hosts: list[str] = []
    for path in molecule_files:
        data = _load_molecule(str(path.relative_to(ROOT)))
        for platform in data.get("platforms", []):
            groups = set(platform.get("groups", []))
            if "backup_server" not in groups:
                continue
            host_name = platform["name"]
            backup_server_hosts.append(host_name)
            if not host_name.endswith("-server"):
                bad_hosts.append(host_name)

    assert backup_server_hosts, "expected at least one backup_server platform in molecule scenarios"
    assert not bad_hosts, f"backup_server platform names must end with '-server': {bad_hosts}"
