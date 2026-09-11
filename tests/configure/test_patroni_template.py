"""Tests for the patroni role config template.

These render the template and parse the result as YAML, rather than
asserting on source text, so that address-family regressions in pg_hba
are caught before a deploy.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment

ROOT = Path(__file__).resolve().parents[2]


def _render_patroni_config(*, ipv6: bool) -> dict[str, Any]:
    env = Environment(trim_blocks=False, lstrip_blocks=False)
    # Minimal stand-ins for the Ansible filters/lookups the template uses.
    env.filters["to_json"] = lambda value, **_: json.dumps(value)
    env.filters["bool"] = lambda value: str(value).lower() in ("true", "yes", "1", "on")
    env.globals["lookup"] = lambda _kind, path: Path(path).read_text()

    template = env.from_string((ROOT / "roles/patroni/templates/patroni.yml.j2").read_text())
    rendered = template.render(
        ansible_managed="test",
        patroni_scope="pg-prod",
        patroni_namespace="/pigsty-lite",
        inventory_hostname="pgnode01",
        patroni_listen_address="::" if ipv6 else "0.0.0.0",
        patroni_advertise_address="2001:db8:10::11" if ipv6 else "10.20.30.11",
        patroni_rest_port=8008,
        patroni_cert_file="/etc/pki/pigsty/pgnode01.crt",
        patroni_key_file="/etc/pki/pigsty/pgnode01.key",
        patroni_trusted_ca_file="/etc/pki/pigsty/ca.crt",
        groups={"etcd": ["pgnode01"]},
        hostvars={"pgnode01": {"etcd_advertise_address": "2001:db8:10::11"}},
        etcd_client_port=2379,
        patroni_etcd_protocol="https",
        ansible_facts={"memtotal_mb": 4096},
        patroni_shared_buffer_ratio=0.25,
        patroni_replication_user="replicator",
        patroni_rewind_user="rewind_user",
        patroni_hba_any_cidr="::/0" if ipv6 else "0.0.0.0/0",
        patroni_hba_loopback_cidr="::1/128" if ipv6 else "127.0.0.1/32",
        patroni_pg_hba_managed=True,
        patroni_postgres_listen_address="::" if ipv6 else "0.0.0.0",
        patroni_postgres_port=5432,
        patroni_postgres_data_dir="/var/lib/pgsql/18/data",
        postgres_version=18,
        patroni_superuser="postgres",
        patroni_superuser_password="superuser-pw",
        patroni_replication_password="replication-pw",
        patroni_rewind_password="rewind-pw",
        role_path=str(ROOT / "roles/patroni"),
        patroni_tune_profile="oltp",
        postgres_extra_parameters={},
    )
    return yaml.safe_load(rendered)


def test_patroni_template_renders_valid_yaml_for_both_families():
    for ipv6 in (False, True):
        config = _render_patroni_config(ipv6=ipv6)
        assert config["bootstrap"]["pg_hba"], "bootstrap pg_hba must not be empty"
        assert config["postgresql"]["pg_hba"], "postgresql pg_hba must not be empty"


def test_patroni_bootstrap_and_runtime_hba_stay_in_sync():
    """The two blocks are rendered from one definition; keep them identical."""
    for ipv6 in (False, True):
        config = _render_patroni_config(ipv6=ipv6)
        assert config["bootstrap"]["pg_hba"] == config["postgresql"]["pg_hba"]


def test_patroni_hba_uses_ipv4_wildcard_by_default():
    config = _render_patroni_config(ipv6=False)
    rules = config["postgresql"]["pg_hba"]

    assert "hostssl replication replicator 0.0.0.0/0 scram-sha-256" in rules
    assert "hostssl postgres rewind_user 0.0.0.0/0 scram-sha-256" in rules
    assert not any("::/0" in rule for rule in rules)


def test_patroni_hba_uses_ipv6_wildcard_in_single_stack_v6():
    """An IPv4 0.0.0.0/0 never matches an IPv6 peer, so replicas could not
    authenticate replication or rewind connections on a v6-only cluster."""
    config = _render_patroni_config(ipv6=True)
    rules = config["postgresql"]["pg_hba"]

    assert "hostssl replication replicator ::/0 scram-sha-256" in rules
    assert "hostssl postgres rewind_user ::/0 scram-sha-256" in rules
    assert not any("0.0.0.0/0" in rule for rule in rules)
    assert not any("127.0.0.1/32" in rule for rule in rules)
