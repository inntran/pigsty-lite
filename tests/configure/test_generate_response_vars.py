"""Tests for group_vars/response.yml generator."""

from __future__ import annotations

from pathlib import Path

import yaml

from bin._generate_response_vars import generate

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    with (FIXTURES / name).open() as fh:
        return yaml.safe_load(fh)


def test_cluster_keys_mapped():
    out = yaml.safe_load(generate(_load("ha.rsp.yml")))
    assert out["cluster_name"] == "pg-prod"
    assert out["cluster_domain"] == "example.internal"
    assert out["cluster_profile"] == "ha"


def test_postgres_keys_namespaced():
    out = yaml.safe_load(generate(_load("ha.rsp.yml")))
    assert out["postgres_version"] == 18
    assert out["postgres_port"] == 5432
    assert out["postgres_tune_profile"] == "oltp"
    assert out["postgres_shared_buffer_ratio"] == 0.25
    assert out["postgres_extensions"] == ["pg_stat_statements", "pgvector"]
    assert out["postgres_databases"] == [{"name": "app", "owner": "app"}]


def test_firewall_keys_promoted_to_top_level():
    out = yaml.safe_load(generate(_load("ha.rsp.yml")))
    assert out["operator_cidrs"] == ["10.0.0.0/8"]
    assert out["postgres_client_cidrs"] == ["10.20.40.0/24"]


def test_tls_keys_namespaced():
    out = yaml.safe_load(generate(_load("ha.rsp.yml")))
    assert out["ca_mode"] == "generate"
    assert out["nginx_proxy_tls_mode"] == "ca_signed"


def test_monitoring_retention_namespaced():
    out = yaml.safe_load(generate(_load("ha.rsp.yml")))
    assert out["vmsingle_retention"] == "90d"
    assert out["vlsingle_retention"] == "30d"


def test_pgbackrest_disabled_when_response_says_so():
    out = yaml.safe_load(generate(_load("single.rsp.yml")))
    assert out["pgbackrest_enabled"] is False


def test_pgbackrest_schedule_promoted():
    out = yaml.safe_load(generate(_load("ha.rsp.yml")))
    assert out["pgbackrest_enabled"] is True
    assert out["pgbackrest_schedule_full"] == "0 1 * * 0"
    assert out["pgbackrest_schedule_differential"] == "0 1 * * 1-6"
    assert out["pgbackrest_retention_full"] == 4


def test_generated_file_has_banner():
    raw = generate(_load("single.rsp.yml"))
    assert raw.lstrip().startswith("#")
    assert "GENERATED" in raw
