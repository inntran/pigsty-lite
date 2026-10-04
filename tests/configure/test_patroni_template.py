"""Tests for the patroni role config template.

These render the template and parse the result as YAML, rather than
asserting on source text, so that pg_hba regressions (address family, rule
order, auth method, quoting) are caught before a deploy.

The system rules come from the role's real defaults, resolved against the
same context, so the tests cover what ships rather than a copy of it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment

ROOT = Path(__file__).resolve().parents[2]
DEFAULTS = ROOT / "roles/patroni/defaults/main.yml"

APP_RULE = {"db": "app", "user": "app", "source": "10.20.40.0/24", "method": "scram-sha-256"}


def _env() -> Environment:
    env = Environment(trim_blocks=False, lstrip_blocks=False)
    # Minimal stand-ins for the Ansible filters/lookups the template uses.
    env.filters["to_json"] = lambda value, **_: json.dumps(value)
    env.filters["bool"] = lambda value: str(value).lower() in ("true", "yes", "1", "on")
    env.globals["lookup"] = lambda _kind, path: Path(path).read_text()
    return env


class UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_mapping_no_duplicates(
    loader: UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                None, None, f"duplicate key {key!r}", key_node.start_mark
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping_no_duplicates
)


def _resolve(value: Any, env: Environment, context: dict[str, Any]) -> Any:
    if isinstance(value, str):
        return env.from_string(value).render(context)
    if isinstance(value, dict):
        return {key: _resolve(item, env, context) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve(item, env, context) for item in value]
    return value


def _render_patroni_config(
    *,
    ipv6: bool = False,
    operator_rules: list[dict[str, Any]] | None = None,
    monitor_rules: list[dict[str, Any]] | None = None,
    tune_profile: str = "oltp",
    preload_prepend: list[str] | None = None,
    preload_append: list[str] | None = None,
    extra_parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    env = _env()
    context: dict[str, Any] = dict(
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
        patroni_postgres_listen_address="::" if ipv6 else "0.0.0.0",
        patroni_postgres_port=5432,
        patroni_postgres_data_dir="/var/lib/pgsql/18/data",
        postgres_version=18,
        patroni_superuser="postgres",
        patroni_superuser_password="superuser-pw",
        patroni_replication_password="replication-pw",
        patroni_rewind_password="rewind-pw",
        role_path=str(ROOT / "roles/patroni"),
        patroni_tune_profile=tune_profile,
        patroni_preload_libraries_base=["pg_stat_statements"],
        patroni_preload_libraries_prepend=preload_prepend or [],
        patroni_preload_libraries_append=preload_append or [],
        postgres_extra_parameters=extra_parameters or {},
    )
    defaults = yaml.safe_load(DEFAULTS.read_text())
    context["patroni_hba_system_rules"] = _resolve(
        defaults["patroni_hba_system_rules"], env, context
    )
    context["patroni_hba_monitor_rules"] = monitor_rules or []
    context["patroni_hba_operator_rules"] = operator_rules or []

    template = env.from_string((ROOT / "roles/patroni/templates/patroni.yml.j2").read_text())
    return yaml.load(template.render(context), Loader=UniqueKeyLoader)


def test_preload_libraries_default_to_pg_stat_statements():
    parameters = _render_patroni_config()["postgresql"]["parameters"]

    assert parameters["shared_preload_libraries"] == "pg_stat_statements"
    assert "timescaledb.telemetry_level" not in parameters
    assert "max_locks_per_transaction" not in parameters


def test_preload_libraries_merge_in_order_and_add_companions():
    parameters = _render_patroni_config(preload_prepend=["citus"], preload_append=["timescaledb"])[
        "postgresql"
    ]["parameters"]

    assert parameters["shared_preload_libraries"] == "citus,pg_stat_statements,timescaledb"
    assert parameters["max_locks_per_transaction"] == "400"
    assert parameters["timescaledb.telemetry_level"] == "off"


def test_preload_libraries_remove_duplicates_preserving_first_occurrence():
    parameters = _render_patroni_config(preload_append=["pg_stat_statements"])["postgresql"][
        "parameters"
    ]

    assert parameters["shared_preload_libraries"] == "pg_stat_statements"


def test_extra_preload_parameter_override_controls_companions():
    added = _render_patroni_config(
        extra_parameters={"shared_preload_libraries": "pg_stat_statements, timescaledb"}
    )["postgresql"]["parameters"]
    removed = _render_patroni_config(
        preload_append=["timescaledb"],
        extra_parameters={"shared_preload_libraries": "pg_stat_statements"},
    )["postgresql"]["parameters"]

    assert added["shared_preload_libraries"] == "pg_stat_statements, timescaledb"
    assert added["timescaledb.telemetry_level"] == "off"
    assert added["max_locks_per_transaction"] == "400"
    assert "timescaledb.telemetry_level" not in removed
    assert "max_locks_per_transaction" not in removed


def test_extra_max_locks_parameter_overrides_derived_companion():
    parameters = _render_patroni_config(
        preload_append=["timescaledb"], extra_parameters={"max_locks_per_transaction": 512}
    )["postgresql"]["parameters"]

    assert parameters["max_locks_per_transaction"] == 512


def test_citus_companion_uses_tuning_profile_max_connections():
    parameters = _render_patroni_config(tune_profile="olap", preload_prepend=["citus"])[
        "postgresql"
    ]["parameters"]

    assert parameters["max_locks_per_transaction"] == "200"


def _hba(**kwargs: Any) -> list[str]:
    return _render_patroni_config(**kwargs)["postgresql"]["pg_hba"]


def test_patroni_template_renders_valid_yaml_for_both_families():
    for ipv6 in (False, True):
        assert _hba(ipv6=ipv6), "postgresql pg_hba must not be empty"


def test_bootstrap_pg_hba_is_not_rendered():
    """Patroni ignores bootstrap.pg_hba whenever postgresql.pg_hba is set, so
    rendering it would only be a second list that silently does nothing."""
    config = _render_patroni_config()
    assert "pg_hba" not in config["bootstrap"]


def test_patroni_hba_uses_ipv4_wildcard_by_default():
    rules = _hba(ipv6=False)

    assert "hostssl replication replicator 0.0.0.0/0 scram-sha-256" in rules
    assert "hostssl postgres rewind_user 0.0.0.0/0 scram-sha-256" in rules
    assert not any("::/0" in rule for rule in rules)


def test_patroni_hba_uses_ipv6_wildcard_in_single_stack_v6():
    """An IPv4 0.0.0.0/0 never matches an IPv6 peer, so replicas could not
    authenticate replication or rewind connections on a v6-only cluster."""
    rules = _hba(ipv6=True)

    assert "hostssl replication replicator ::/0 scram-sha-256" in rules
    assert "hostssl postgres rewind_user ::/0 scram-sha-256" in rules
    assert not any("0.0.0.0/0" in rule for rule in rules)


def test_system_rules_render_exactly():
    assert _hba() == [
        "local all postgres peer",
        "host all all 127.0.0.1/32 scram-sha-256",
        "host all all ::1/128 scram-sha-256",
        "host replication replicator 127.0.0.1/32 scram-sha-256",
        "host replication replicator ::1/128 scram-sha-256",
        "hostssl replication replicator 0.0.0.0/0 scram-sha-256",
        "hostssl postgres rewind_user 0.0.0.0/0 scram-sha-256",
    ]


def test_system_rules_have_no_trust():
    assert not any(rule.endswith(" trust") for rule in _hba())


def test_only_postgres_uses_the_socket_by_os_identity():
    local_rules = [rule for rule in _hba() if rule.startswith("local ")]

    assert local_rules == ["local all postgres peer"]


def test_every_user_may_use_loopback_with_a_password():
    rules = _hba()

    assert "host all all 127.0.0.1/32 scram-sha-256" in rules
    assert "host all all ::1/128 scram-sha-256" in rules


def test_replication_authenticates_with_scram_not_cert():
    """Patroni's replication user connects with a password; the `cert`
    method roles/provision used to write would reject it."""
    rules = _hba(operator_rules=[APP_RULE])

    assert not any(rule.split()[-1] == "cert" for rule in rules)
    remote_replication = [r for r in rules if r.split()[:2] == ["hostssl", "replication"]]
    assert remote_replication
    assert all(r.endswith(" scram-sha-256") for r in remote_replication)


def test_operator_rules_render_from_response_file_keys():
    rules = _hba(operator_rules=[APP_RULE])

    assert "hostssl app app 10.20.40.0/24 scram-sha-256" in rules


def test_operator_rule_defaults_contype_and_method():
    rules = _hba(operator_rules=[{"db": "app", "user": "app", "source": "10.0.0.0/8"}])

    assert rules[-1] == "hostssl app app 10.0.0.0/8 scram-sha-256"


def test_null_contype_and_method_fall_back_to_defaults():
    rules = _hba(
        operator_rules=[
            {
                "contype": None,
                "db": "app",
                "user": "app",
                "source": "10.0.0.0/8",
                "method": None,
            }
        ]
    )

    assert rules[-1] == "hostssl app app 10.0.0.0/8 scram-sha-256"


def test_local_rules_have_no_address_column():
    rules = _hba(
        operator_rules=[{"contype": "local", "db": "app", "user": "app", "method": "peer"}]
    )

    assert rules[-1] == "local app app peer"


def test_rules_render_in_system_monitor_operator_order():
    monitor = {"db": "postgres", "user": "monitor", "source": "10.9.9.9/32"}
    rules = _hba(operator_rules=[APP_RULE], monitor_rules=[monitor])

    system_last = rules.index("hostssl postgres rewind_user 0.0.0.0/0 scram-sha-256")
    monitor_at = rules.index("hostssl postgres monitor 10.9.9.9/32 scram-sha-256")
    operator_at = rules.index("hostssl app app 10.20.40.0/24 scram-sha-256")
    assert system_last < monitor_at < operator_at


def test_operator_values_cannot_break_the_yaml():
    """Every line is emitted through to_json, so YAML-significant characters
    in an operator value stay inside one list item."""
    tricky = {"db": "app", "user": "app", "source": ".example.com", "method": "scram-sha-256"}
    rules = _hba(operator_rules=[tricky, {"db": "a#b", "user": "c: d", "source": "10.0.0.0/8"}])

    assert "hostssl app app .example.com scram-sha-256" in rules
    assert "hostssl a#b c: d 10.0.0.0/8 scram-sha-256" in rules


def test_ipv6_single_stack_renders_operator_rules_unchanged():
    rule = {"db": "app", "user": "app", "source": "2001:db8:40::/64"}
    rules = _hba(ipv6=True, operator_rules=[rule])

    assert rules[-1] == "hostssl app app 2001:db8:40::/64 scram-sha-256"
