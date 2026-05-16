"""Tests for the response-file schema validator."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from bin._response_schema import SchemaError, validate

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    with (FIXTURES / name).open() as fh:
        return yaml.safe_load(fh)


def _minimal_spof_response() -> dict:
    return deepcopy(_load("spof.rsp.yml"))


def test_spof_profile_fixture_validates():
    data = _load("spof.rsp.yml")
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


def test_postgres_users_must_be_list_of_dicts():
    data = _load("spof.rsp.yml")
    data["postgres"]["users"] = ["bare-string-not-dict"]
    with pytest.raises(SchemaError, match=r"postgres\.users\[0\]: must be a mapping"):
        validate(data)


def test_postgres_users_require_name():
    data = _load("spof.rsp.yml")
    data["postgres"]["users"] = [{"password": "pw"}]
    with pytest.raises(SchemaError, match=r"postgres\.users\[0\]\.name"):
        validate(data)


def test_postgres_databases_must_be_list_of_dicts():
    data = _load("spof.rsp.yml")
    data["postgres"]["databases"] = ["bare-string"]
    with pytest.raises(SchemaError, match=r"postgres\.databases\[0\]: must be a mapping"):
        validate(data)


def test_postgres_databases_require_name():
    data = _load("spof.rsp.yml")
    data["postgres"]["databases"] = [{"owner": "app"}]
    with pytest.raises(SchemaError, match=r"postgres\.databases\[0\]\.name"):
        validate(data)


def test_postgres_extensions_must_be_strings_or_name_dicts():
    data = _load("spof.rsp.yml")
    data["postgres"]["extensions"] = [42]
    with pytest.raises(SchemaError, match=r"postgres\.extensions\[0\]"):
        validate(data)


def test_postgres_extensions_dict_requires_name():
    data = _load("spof.rsp.yml")
    data["postgres"]["extensions"] = [{"db": "app"}]
    with pytest.raises(SchemaError, match=r"postgres\.extensions\[0\]\.name"):
        validate(data)


def test_minor_upgrade_block_must_be_mapping():
    response = _minimal_spof_response()
    response["postgres"]["minor_upgrade"] = "soon"
    with pytest.raises(SchemaError, match=r"postgres\.minor_upgrade: must be a mapping"):
        validate(response)


def test_minor_upgrade_require_recent_backup_hours_must_be_positive_int():
    response = _minimal_spof_response()
    response["postgres"]["minor_upgrade"] = {"require_recent_backup_hours": 0}
    with pytest.raises(SchemaError, match=r"postgres\.minor_upgrade\.require_recent_backup_hours"):
        validate(response)


def test_minor_upgrade_block_is_optional():
    response = _minimal_spof_response()
    response["postgres"].pop("minor_upgrade", None)
    validate(response)


def test_unknown_network_ip_version_rejected():
    data = _load("spof.rsp.yml")
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


def test_spof_profile_must_have_exactly_one_postgres_node():
    data = _load("ha.rsp.yml")
    data["profile"] = "spof"
    with pytest.raises(SchemaError, match="spof"):
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


def test_backup_enabled_must_be_bool():
    data = _load("spof.rsp.yml")
    data["backup"] = {"enabled": "yes-please"}
    with pytest.raises(SchemaError, match=r"backup\.enabled: must be a boolean"):
        validate(data)


def test_backup_retention_full_must_be_positive_int():
    data = _load("spof.rsp.yml")
    data["backup"] = {"enabled": True, "tool": "pgbackrest", "retention": {"full": 0}}
    with pytest.raises(SchemaError, match=r"backup\.retention\.full"):
        validate(data)


def test_backup_schedule_entries_must_be_strings():
    data = _load("spof.rsp.yml")
    data["backup"] = {
        "enabled": True,
        "tool": "pgbackrest",
        "schedule": {"full": 123, "differential": "0 1 * * 1-6"},
    }
    with pytest.raises(SchemaError, match=r"backup\.schedule\.full"):
        validate(data)


def test_backup_secondary_store_requires_bucket_when_enabled():
    data = _load("spof.rsp.yml")
    data["backup"] = {
        "enabled": True,
        "tool": "pgbackrest",
        "secondary_store": {"enabled": True, "type": "s3", "endpoint": "s3.example.com"},
    }
    with pytest.raises(SchemaError, match=r"backup\.secondary_store\.bucket"):
        validate(data)


def test_backup_disabled_skips_inner_validation():
    data = _load("spof.rsp.yml")
    data["backup"] = {"enabled": False, "retention": {"full": 0}}
    validate(data)


def test_backup_section_is_optional():
    data = _load("spof.rsp.yml")
    data.pop("backup", None)
    validate(data)


def test_monitoring_receiver_must_be_mapping():
    response = _minimal_spof_response()
    response["monitoring"]["alertmanager"] = {"receivers": ["not-a-dict"]}
    with pytest.raises(
        SchemaError, match=r"monitoring\.alertmanager\.receivers\[0\]: must be a mapping"
    ):
        validate(response)


def test_monitoring_receiver_requires_name_and_type():
    response = _minimal_spof_response()
    response["monitoring"]["alertmanager"] = {"receivers": [{"type": "slack"}]}
    with pytest.raises(SchemaError, match=r"monitoring\.alertmanager\.receivers\[0\]\.name"):
        validate(response)


def test_monitoring_receiver_type_must_be_known():
    response = _minimal_spof_response()
    response["monitoring"]["alertmanager"] = {
        "receivers": [{"name": "x", "type": "carrier-pigeon"}]
    }
    with pytest.raises(SchemaError, match=r"monitoring\.alertmanager\.receivers\[0\]\.type"):
        validate(response)


def test_monitoring_scrape_interval_must_be_duration():
    response = _minimal_spof_response()
    response["monitoring"]["scrape_interval"] = "fifteen"
    with pytest.raises(SchemaError, match=r"monitoring\.scrape_interval"):
        validate(response)


def test_monitoring_scrape_interval_is_optional():
    response = _minimal_spof_response()
    response["monitoring"].pop("scrape_interval", None)
    validate(response)
