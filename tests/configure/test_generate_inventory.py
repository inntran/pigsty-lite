"""Tests for the inventory generator."""

from __future__ import annotations

from pathlib import Path

import yaml

from bin._generate_inventory import generate

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    with (FIXTURES / name).open() as fh:
        return yaml.safe_load(fh)


def test_single_inventory_has_required_groups():
    out = yaml.safe_load(generate(_load("single.rsp.yml")))
    children = out["all"]["children"]
    assert set(children) >= {"monitor", "backup_server", "etcd", "postgres"}


def test_single_inventory_collocates_monitor_and_backup_server():
    out = yaml.safe_load(generate(_load("single.rsp.yml")))
    children = out["all"]["children"]
    mon_hosts = set(children["monitor"]["hosts"].keys())
    bs_hosts = set(children["backup_server"]["hosts"].keys())
    assert mon_hosts == bs_hosts == {"pgmon01"}


def test_single_postgres_node_is_in_etcd_group():
    out = yaml.safe_load(generate(_load("single.rsp.yml")))
    etcd_hosts = out["all"]["children"]["etcd"]["hosts"]
    pg_hosts = out["all"]["children"]["postgres"]["hosts"]
    assert set(etcd_hosts.keys()) == set(pg_hosts.keys()) == {"pgnode01"}


def test_ha_inventory_has_three_postgres_and_three_etcd_members():
    out = yaml.safe_load(generate(_load("ha.rsp.yml")))
    pg_hosts = out["all"]["children"]["postgres"]["hosts"]
    etcd_hosts = out["all"]["children"]["etcd"]["hosts"]
    assert set(pg_hosts.keys()) == {"pgnode01", "pgnode02", "pgnode03"}
    assert set(etcd_hosts.keys()) == {"pgnode01", "pgnode02", "pgnode03"}


def test_postgres_role_set_on_primary_and_replicas():
    out = yaml.safe_load(generate(_load("ha.rsp.yml")))
    pg = out["all"]["children"]["postgres"]["hosts"]
    assert pg["pgnode01"]["postgres_role"] == "primary"
    assert pg["pgnode02"]["postgres_role"] == "replica"
    assert pg["pgnode03"]["postgres_role"] == "replica"


def test_etcd_seq_and_postgres_seq_assigned_deterministically():
    out = yaml.safe_load(generate(_load("ha.rsp.yml")))
    pg = out["all"]["children"]["postgres"]["hosts"]
    etcd = out["all"]["children"]["etcd"]["hosts"]
    assert pg["pgnode01"]["postgres_seq"] == 1
    assert pg["pgnode02"]["postgres_seq"] == 2
    assert pg["pgnode03"]["postgres_seq"] == 3
    assert etcd["pgnode01"]["etcd_seq"] == 1
    assert etcd["pgnode02"]["etcd_seq"] == 2
    assert etcd["pgnode03"]["etcd_seq"] == 3


def test_ansible_host_propagated_from_response():
    out = yaml.safe_load(generate(_load("ha.rsp.yml")))
    pg = out["all"]["children"]["postgres"]["hosts"]
    assert pg["pgnode01"]["ansible_host"] == "10.20.30.11"


def test_ipv6_ansible_host_propagated_from_response():
    out = yaml.safe_load(generate(_load("ipv6.rsp.yml")))
    pg = out["all"]["children"]["postgres"]["hosts"]
    assert pg["pgnode01"]["ansible_host"] == "2001:db8:10::11"


def test_generated_inventory_includes_banner_comment():
    raw = generate(_load("single.rsp.yml"))
    assert raw.lstrip().startswith("#")
    assert "configure" in raw.splitlines()[0]
