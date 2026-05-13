"""Tests for the response-file schema validator."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from bin._response_schema import SchemaError, validate

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    with (FIXTURES / name).open() as fh:
        return yaml.safe_load(fh)


def test_single_profile_fixture_validates():
    data = _load("single.rsp.yml")
    validate(data)


def test_ha_profile_fixture_validates():
    data = _load("ha.rsp.yml")
    validate(data)


def test_ipv6_single_stack_fixture_validates():
    data = _load("ipv6.rsp.yml")
    validate(data)


def test_ipv6_single_stack_rejects_ipv4_node_ip():
    data = _load("ipv6.rsp.yml")
    data["nodes"]["pgnode01"]["ip"] = "10.20.30.11"
    with pytest.raises(SchemaError, match="network.ip_version"):
        validate(data)


def test_ipv6_single_stack_rejects_ipv4_firewall_cidr():
    data = _load("ipv6.rsp.yml")
    data["firewall"]["postgres_client_cidrs"] = ["10.20.40.0/24"]
    with pytest.raises(SchemaError, match="network.ip_version"):
        validate(data)


def test_ipv6_single_stack_rejects_ipv4_hba_source():
    data = _load("ipv6.rsp.yml")
    data["postgres"]["hba_rules"][0]["source"] = "10.20.40.0/24"
    with pytest.raises(SchemaError, match="network.ip_version"):
        validate(data)


def test_unknown_network_ip_version_rejected():
    data = _load("single.rsp.yml")
    data["network"] = {"ip_version": "ipv5"}
    with pytest.raises(SchemaError, match="network.ip_version"):
        validate(data)


def test_invalid_profile_value_rejected():
    data = _load("invalid.rsp.yml")
    with pytest.raises(SchemaError, match="profile"):
        validate(data)


def test_missing_required_top_level_key_rejected():
    data = _load("ha.rsp.yml")
    del data["cluster"]
    with pytest.raises(SchemaError, match="cluster"):
        validate(data)


def test_bad_ip_rejected():
    data = _load("ha.rsp.yml")
    data["nodes"]["pgnode01"]["ip"] = "999.999.999.999"
    with pytest.raises(SchemaError, match="ip"):
        validate(data)


def test_unknown_node_role_rejected():
    data = _load("ha.rsp.yml")
    data["nodes"]["pgnode01"]["role"] = "wizard"
    with pytest.raises(SchemaError, match="role"):
        validate(data)


def test_single_profile_must_have_exactly_one_postgres_node():
    data = _load("ha.rsp.yml")
    data["profile"] = "single"
    with pytest.raises(SchemaError, match="single"):
        validate(data)


def test_ha_profile_requires_three_postgres_nodes():
    data = _load("ha.rsp.yml")
    del data["nodes"]["pgnode03"]
    with pytest.raises(SchemaError, match="ha"):
        validate(data)


def test_ha_profile_requires_exactly_one_primary():
    data = _load("ha.rsp.yml")
    data["nodes"]["pgnode02"]["role"] = "pg_primary"
    with pytest.raises(SchemaError, match="primary"):
        validate(data)
