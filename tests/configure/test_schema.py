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


@pytest.mark.parametrize(
    "source",
    ["10.20.40.0/24", "10.0.0.1", "all", "samehost", "samenet", "localhost",
     "db.example.com", ".example.com"],
)
def test_hba_source_accepts_cidrs_keywords_and_hostnames(source):
    data = _load("ha.rsp.yml")
    data["postgres"]["hba_rules"][0]["source"] = source
    validate(data)


@pytest.mark.parametrize(
    "source",
    [
        "not-a-cidr",      # bare word typo
        "smaenet",         # misspelled keyword
        "10.20.40.0/99",   # prefix out of range
        "999.1.1.1/24",    # octet out of range
        "10.20.40.o/24",   # letter o for zero
        "",                # empty
    ],
)
def test_hba_source_rejects_malformed_values(source):
    """A malformed source used to be swallowed here and would surface only
    when pg_hba.conf was written, failing mid-deploy."""
    data = _load("ha.rsp.yml")
    data["postgres"]["hba_rules"][0]["source"] = source
    with pytest.raises(SchemaError, match="hba_rules"):
        validate(data)


def test_external_push_rejects_basic_auth_and_bearer_together():
    """vmagent/vlagent exit with "cannot simultaneously use `authorization`,
    `basic_auth` and `bearer_token_file`", so the agents crash-loop after an
    otherwise successful deploy."""
    data = _load("spof.rsp.yml")
    data["monitoring"] = {
        "mode": "external_push",
        "external_push": {
            "metrics_url": "https://example.com/api/v1/write",
            "logs_url": "https://example.com/insert/jsonline",
            "auth": {"username": "pigsty", "bearer": True},
        },
    }
    with pytest.raises(SchemaError, match="not both"):
        validate(data)


@pytest.mark.parametrize("auth", [{"username": "pigsty"}, {"bearer": True}])
def test_external_push_accepts_a_single_auth_method(auth):
    data = _load("spof.rsp.yml")
    data["monitoring"] = {
        "mode": "external_push",
        "external_push": {
            "metrics_url": "https://example.com/api/v1/write",
            "logs_url": "https://example.com/insert/jsonline",
            "auth": auth,
        },
    }
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


def test_postgres_extension_packages_accepts_list_of_strings():
    data = _load("spof.rsp.yml")
    data["postgres"]["extension_packages"] = ["pgvector_{{ postgres_version }}"]
    validate(data)


def test_postgres_extension_packages_must_be_list():
    data = _load("spof.rsp.yml")
    data["postgres"]["extension_packages"] = "pgvector_18"
    with pytest.raises(SchemaError, match=r"postgres\.extension_packages: must be a list"):
        validate(data)


def test_postgres_extension_packages_rejects_non_string_entries():
    data = _load("spof.rsp.yml")
    data["postgres"]["extension_packages"] = ["pgvector_18", 42]
    with pytest.raises(SchemaError, match=r"postgres\.extension_packages\[1\]: expected string"):
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


def test_access_absent_is_valid():
    data = _minimal_spof_response()
    data.pop("access", None)
    validate(data)


def test_access_custom_user_is_valid():
    data = _minimal_spof_response()
    data["access"] = {"ansible_user": "ansible-svc"}
    validate(data)


def test_access_must_be_mapping():
    data = _minimal_spof_response()
    data["access"] = "dba"
    with pytest.raises(SchemaError, match="access: must be a mapping"):
        validate(data)


def test_access_ansible_user_must_be_string():
    data = _minimal_spof_response()
    data["access"] = {"ansible_user": 42}
    with pytest.raises(SchemaError, match="access.ansible_user"):
        validate(data)


def test_access_ansible_user_must_not_be_empty():
    data = _minimal_spof_response()
    data["access"] = {"ansible_user": ""}
    with pytest.raises(SchemaError, match="access.ansible_user"):
        validate(data)


def test_monitoring_self_hosted_mode_is_default():
    response = _minimal_spof_response()
    response["monitoring"].pop("mode", None)
    validate(response)


def test_monitoring_self_hosted_rejects_external_blocks():
    response = _minimal_spof_response()
    response["monitoring"]["external_push"] = {
        "metrics_url": "https://vm.example/api/v1/write",
        "logs_url": "https://vl.example/insert/jsonline",
    }
    with pytest.raises(SchemaError, match=r"monitoring\.external_push"):
        validate(response)


def test_monitoring_external_push_validates_without_monitor_or_retention():
    response = _minimal_spof_response()
    response["nodes"] = {
        name: node for name, node in response["nodes"].items() if node["role"] != "monitor"
    }
    response["monitoring"] = {
        "mode": "external_push",
        "scrape_interval": "15s",
        "external_push": {
            "metrics_url": "https://vm.example/api/v1/write",
            "logs_url": "https://vl.example/insert/jsonline",
            # Basic auth only: combining it with bearer makes vmagent and
            # vlagent exit at startup. See
            # test_external_push_rejects_basic_auth_and_bearer_together.
            "auth": {"username": "pigsty"},
            "tls_skip_verify": True,
        },
    }
    validate(response)


def test_monitoring_external_push_rejects_bad_url_and_extra_pull_block():
    response = _minimal_spof_response()
    response["monitoring"] = {
        "mode": "external_push",
        "external_push": {
            "metrics_url": "not-a-url",
            "logs_url": "https://vl.example/insert/jsonline",
        },
        "external_pull": {},
    }
    with pytest.raises(SchemaError, match=r"monitoring\.external_pull"):
        validate(response)
    response["monitoring"].pop("external_pull")
    with pytest.raises(SchemaError, match=r"metrics_url"):
        validate(response)


def test_monitoring_external_push_rejects_unknown_auth_keys():
    response = _minimal_spof_response()
    response["monitoring"] = {
        "mode": "external_push",
        "external_push": {
            "metrics_url": "https://vm.example/api/v1/write",
            "logs_url": "https://vl.example/insert/jsonline",
            "auth": {"token": "secret"},
        },
    }
    with pytest.raises(SchemaError, match=r"unknown keys"):
        validate(response)


def test_monitoring_external_pull_validates_without_monitor_or_retention():
    response = _minimal_spof_response()
    response["nodes"] = {
        name: node for name, node in response["nodes"].items() if node["role"] != "monitor"
    }
    response["monitoring"] = {
        "mode": "external_pull",
        "scrape_interval": "15s",
        "external_pull": {
            "metrics_port": 9965,
            "auth": {"username": "pigsty"},
            "source_cidrs": ["10.0.0.0/8"],
            "tls": False,
        },
    }
    validate(response)


def test_monitoring_external_pull_rejects_bad_port_empty_auth_and_empty_cidrs():
    response = _minimal_spof_response()
    response["monitoring"] = {
        "mode": "external_pull",
        "external_pull": {
            "metrics_port": 70000,
            "auth": {"username": "pigsty"},
            "source_cidrs": ["10.0.0.0/8"],
        },
    }
    with pytest.raises(SchemaError, match=r"metrics_port"):
        validate(response)
    response["monitoring"]["external_pull"]["metrics_port"] = 9965
    response["monitoring"]["external_pull"]["auth"]["username"] = ""
    with pytest.raises(SchemaError, match=r"auth\.username"):
        validate(response)
    response["monitoring"]["external_pull"]["auth"]["username"] = "pigsty"
    response["monitoring"]["external_pull"]["source_cidrs"] = []
    with pytest.raises(SchemaError, match=r"source_cidrs"):
        validate(response)


def test_monitoring_external_pull_cidrs_follow_ip_version():
    response = _load("ipv6.rsp.yml")
    response["monitoring"] = {
        "mode": "external_pull",
        "external_pull": {
            "metrics_port": 9965,
            "auth": {"username": "pigsty"},
            "source_cidrs": ["10.0.0.0/8"],
        },
    }
    with pytest.raises(SchemaError, match=r"network\.ip_version"):
        validate(response)


def test_external_monitoring_rejects_more_than_one_monitor():
    response = _minimal_spof_response()
    response["nodes"]["pgmon02"] = {"ip": "10.20.30.42", "role": "monitor"}
    response["monitoring"] = {
        "mode": "external_push",
        "external_push": {
            "metrics_url": "https://vm.example/api/v1/write",
            "logs_url": "https://vl.example/insert/jsonline",
        },
    }
    with pytest.raises(SchemaError, match=r"at most 1 monitor"):
        validate(response)
