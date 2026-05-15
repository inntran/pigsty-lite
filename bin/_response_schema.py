"""Response-file schema validation for pigsty-lite.

Pure-Python validator using stdlib only. Imported by the `configure` CLI.
Raises SchemaError with a human-readable message on validation failure.
"""

from __future__ import annotations

import ipaddress
import re
from typing import Any

ALLOWED_PROFILES = {"single", "ha"}
ALLOWED_NODE_ROLES = {"monitor", "backup_store", "pg_primary", "pg_replica"}
ALLOWED_IP_VERSIONS = {"dual", "ipv4", "ipv6"}
ALLOWED_TUNE = {"oltp", "olap", "tiny"}
ALLOWED_CA_MODES = {"generate", "existing", "byo"}
ALLOWED_USER_TLS = {"ca_signed", "byo", "http"}
DURATION_RE = re.compile(r"^\d+[smhdw]$")


class SchemaError(ValueError):
    """Raised when a response file fails schema validation."""


def _require(d: dict, key: str, path: str) -> Any:
    if key not in d:
        raise SchemaError(f"{path}: missing required key '{key}'")
    return d[key]


def _require_str(d: dict, key: str, path: str) -> str:
    value = _require(d, key, path)
    if not isinstance(value, str):
        raise SchemaError(f"{path}.{key}: expected string, got {type(value).__name__}")
    return value


def _require_int(d: dict, key: str, path: str) -> int:
    value = _require(d, key, path)
    if not isinstance(value, int) or isinstance(value, bool):
        raise SchemaError(f"{path}.{key}: expected int, got {type(value).__name__}")
    return value


def _required_ip_version(ip_version: str) -> int | None:
    if ip_version == "ipv4":
        return 4
    if ip_version == "ipv6":
        return 6
    return None


def _check_ip(value: str, path: str, ip_version: str = "dual") -> None:
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise SchemaError(f"{path}: invalid ip '{value}': {exc}") from exc

    required = _required_ip_version(ip_version)
    if required is not None and address.version != required:
        raise SchemaError(
            f"network.ip_version '{ip_version}' requires IPv{required} address at {path}; "
            f"got '{value}'"
        )


def _check_cidr(value: str, path: str, ip_version: str = "dual") -> None:
    try:
        network = ipaddress.ip_network(value, strict=False)
    except ValueError as exc:
        raise SchemaError(f"{path}: invalid cidr '{value}': {exc}") from exc

    required = _required_ip_version(ip_version)
    if required is not None and network.version != required:
        raise SchemaError(
            f"network.ip_version '{ip_version}' requires IPv{required} CIDR at {path}; "
            f"got '{value}'"
        )


def _validate_network(network: dict | None) -> str:
    if network is None:
        return "dual"
    if not isinstance(network, dict):
        raise SchemaError("network: must be a mapping")
    ip_version = network.get("ip_version", "dual")
    if not isinstance(ip_version, str):
        raise SchemaError("network.ip_version: expected string")
    if ip_version not in ALLOWED_IP_VERSIONS:
        allowed = sorted(ALLOWED_IP_VERSIONS)
        raise SchemaError(f"network.ip_version: '{ip_version}' not in {allowed}")
    return ip_version


def _validate_nodes(nodes: dict, profile: str, ip_version: str) -> None:
    if not isinstance(nodes, dict) or not nodes:
        raise SchemaError("nodes: must be a non-empty mapping")

    roles: list[str] = []
    for name, node in nodes.items():
        path = f"nodes.{name}"
        if not isinstance(node, dict):
            raise SchemaError(f"{path}: must be a mapping")
        ip = _require_str(node, "ip", path)
        _check_ip(ip, f"{path}.ip", ip_version)
        role = _require_str(node, "role", path)
        if role not in ALLOWED_NODE_ROLES:
            raise SchemaError(f"{path}.role: '{role}' not in {sorted(ALLOWED_NODE_ROLES)}")
        roles.append(role)

    primaries = roles.count("pg_primary")
    replicas = roles.count("pg_replica")
    monitors = roles.count("monitor")

    if monitors != 1:
        raise SchemaError(f"nodes: profile '{profile}' requires exactly 1 monitor node")
    if primaries != 1:
        raise SchemaError(
            f"nodes: profile '{profile}' requires exactly 1 pg_primary; got {primaries}"
        )

    if profile == "single":
        if replicas != 0:
            raise SchemaError(
                "nodes: profile 'single' allows 0 pg_replica; "
                f"got {replicas} (use profile 'ha' for replicas)"
            )
    elif profile == "ha" and replicas < 2:
        raise SchemaError(f"nodes: profile 'ha' requires at least 2 pg_replica; got {replicas}")


def _validate_hba_rules(postgres: dict, ip_version: str) -> None:
    hba_rules = postgres.get("hba_rules", [])
    if not isinstance(hba_rules, list):
        raise SchemaError("postgres.hba_rules: must be a list")
    for index, rule in enumerate(hba_rules):
        if not isinstance(rule, dict):
            raise SchemaError(f"postgres.hba_rules[{index}]: must be a mapping")
        source = rule.get("source")
        if isinstance(source, str):
            try:
                _check_cidr(source, f"postgres.hba_rules[{index}].source", ip_version)
            except SchemaError as exc:
                if "invalid cidr" not in str(exc):
                    raise


def _validate_users(postgres: dict) -> None:
    users = postgres.get("users", [])
    if not isinstance(users, list):
        raise SchemaError("postgres.users: must be a list")
    for index, user in enumerate(users):
        path = f"postgres.users[{index}]"
        if not isinstance(user, dict):
            raise SchemaError(f"{path}: must be a mapping")
        if "name" not in user:
            raise SchemaError(f"{path}.name: missing required key 'name'")
        _require_str(user, "name", path)
        roles = user.get("roles", [])
        if not isinstance(roles, list):
            raise SchemaError(f"{path}.roles: must be a list of role names")
        for role_index, role in enumerate(roles):
            if not isinstance(role, str):
                raise SchemaError(f"{path}.roles[{role_index}]: expected string")


def _validate_databases(postgres: dict) -> None:
    dbs = postgres.get("databases", [])
    if not isinstance(dbs, list):
        raise SchemaError("postgres.databases: must be a list")
    for index, db in enumerate(dbs):
        path = f"postgres.databases[{index}]"
        if not isinstance(db, dict):
            raise SchemaError(f"{path}: must be a mapping")
        if "name" not in db:
            raise SchemaError(f"{path}.name: missing required key 'name'")
        _require_str(db, "name", path)


def _validate_extensions(postgres: dict) -> None:
    exts = postgres.get("extensions", [])
    if not isinstance(exts, list):
        raise SchemaError("postgres.extensions: must be a list")
    for index, ext in enumerate(exts):
        path = f"postgres.extensions[{index}]"
        if isinstance(ext, str):
            continue
        if isinstance(ext, dict):
            if "name" not in ext:
                raise SchemaError(f"{path}.name: missing required key 'name'")
            _require_str(ext, "name", path)
            continue
        raise SchemaError(f"{path}: must be a string or a mapping with at least 'name'")


def _validate_minor_upgrade(postgres: dict) -> None:
    minor_upgrade = postgres.get("minor_upgrade")
    if minor_upgrade is None:
        return
    if not isinstance(minor_upgrade, dict):
        raise SchemaError("postgres.minor_upgrade: must be a mapping")
    hours = minor_upgrade.get("require_recent_backup_hours")
    if hours is not None and (not isinstance(hours, int) or isinstance(hours, bool) or hours < 1):
        raise SchemaError(
            "postgres.minor_upgrade.require_recent_backup_hours: must be a positive integer"
        )


def _validate_postgres(postgres: dict, ip_version: str) -> None:
    if not isinstance(postgres, dict):
        raise SchemaError("postgres: must be a mapping")
    version = _require_int(postgres, "version", "postgres")
    if version < 14 or version > 18:
        raise SchemaError(f"postgres.version: {version} not in 14..18")
    port = _require_int(postgres, "port", "postgres")
    if port < 1 or port > 65535:
        raise SchemaError(f"postgres.port: {port} out of range")
    tune = _require_str(postgres, "tune", "postgres")
    if tune not in ALLOWED_TUNE:
        raise SchemaError(f"postgres.tune: '{tune}' not in {sorted(ALLOWED_TUNE)}")
    shared_buffer_ratio = postgres.get("shared_buffer_ratio", 0.25)
    if not isinstance(shared_buffer_ratio, int | float) or not 0.05 <= shared_buffer_ratio <= 0.6:
        raise SchemaError("postgres.shared_buffer_ratio: must be a float in 0.05..0.6")
    _validate_hba_rules(postgres, ip_version)
    _validate_users(postgres)
    _validate_databases(postgres)
    _validate_extensions(postgres)
    _validate_minor_upgrade(postgres)


def _validate_tls(tls: dict) -> None:
    if not isinstance(tls, dict):
        raise SchemaError("tls: must be a mapping")
    mode = _require_str(tls, "internal_ca", "tls")
    if mode not in ALLOWED_CA_MODES:
        raise SchemaError(f"tls.internal_ca: '{mode}' not in {sorted(ALLOWED_CA_MODES)}")
    user = _require(tls, "user_facing", "tls")
    if not isinstance(user, dict):
        raise SchemaError("tls.user_facing: must be a mapping")
    user_mode = _require_str(user, "mode", "tls.user_facing")
    if user_mode not in ALLOWED_USER_TLS:
        raise SchemaError(f"tls.user_facing.mode: '{user_mode}' not in {sorted(ALLOWED_USER_TLS)}")


def _validate_firewall(firewall: dict, ip_version: str) -> None:
    if not isinstance(firewall, dict):
        raise SchemaError("firewall: must be a mapping")
    for key in ("operator_cidrs", "postgres_client_cidrs"):
        value = _require(firewall, key, "firewall")
        if not isinstance(value, list) or not value:
            raise SchemaError(f"firewall.{key}: must be a non-empty list")
        for index, cidr in enumerate(value):
            _check_cidr(cidr, f"firewall.{key}[{index}]", ip_version)


ALLOWED_HAPROXY_RTO = {"tight", "norm", "loose"}
ALLOWED_HAPROXY_BACKEND = {"pgbouncer", "postgres"}


def _validate_connection_layer(value: Any, ip_version: str) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        raise SchemaError("connection_layer: must be a mapping")
    haproxy = value.get("haproxy", {})
    if not isinstance(haproxy, dict):
        raise SchemaError("connection_layer.haproxy: must be a mapping")
    rto = haproxy.get("rto_profile", "norm")
    if rto not in ALLOWED_HAPROXY_RTO:
        raise SchemaError(
            f"connection_layer.haproxy.rto_profile: '{rto}' not in {sorted(ALLOWED_HAPROXY_RTO)}"
        )
    backend = haproxy.get("backend_target", "pgbouncer")
    if backend not in ALLOWED_HAPROXY_BACKEND:
        raise SchemaError(
            f"connection_layer.haproxy.backend_target: '{backend}' not in "
            f"{sorted(ALLOWED_HAPROXY_BACKEND)}"
        )
    vip = value.get("vip_manager", {})
    if not isinstance(vip, dict):
        raise SchemaError("connection_layer.vip_manager: must be a mapping")
    enabled = vip.get("enabled", False)
    if not isinstance(enabled, bool):
        raise SchemaError("connection_layer.vip_manager.enabled: must be bool")
    if enabled:
        cidr = vip.get("vip_cidr")
        iface = vip.get("interface")
        if not isinstance(cidr, str) or not cidr:
            raise SchemaError(
                "connection_layer.vip_manager.enabled=true requires vip_cidr (string)"
            )
        _check_cidr(cidr, "connection_layer.vip_manager.vip_cidr", ip_version)
        if not isinstance(iface, str) or not iface:
            raise SchemaError(
                "connection_layer.vip_manager.enabled=true requires interface (string)"
            )


def _validate_monitoring(monitoring: dict) -> None:
    if not isinstance(monitoring, dict):
        raise SchemaError("monitoring: must be a mapping")
    for key in ("vmsingle_retention", "vlsingle_retention"):
        value = _require_str(monitoring, key, "monitoring")
        if not DURATION_RE.match(value):
            raise SchemaError(f"monitoring.{key}: '{value}' must match Nm|Nh|Nd|Nw form (e.g. 90d)")

    alertmanager = monitoring.get("alertmanager")
    if alertmanager is not None:
        if not isinstance(alertmanager, dict):
            raise SchemaError("monitoring.alertmanager: must be a mapping")
        receivers = alertmanager.get("receivers", [])
        if not isinstance(receivers, list):
            raise SchemaError("monitoring.alertmanager.receivers: must be a list")
        known_types = {"slack", "email", "webhook", "pagerduty"}
        for index, receiver in enumerate(receivers):
            path = f"monitoring.alertmanager.receivers[{index}]"
            if not isinstance(receiver, dict):
                raise SchemaError(f"{path}: must be a mapping")
            if "name" not in receiver:
                raise SchemaError(f"{path}.name: missing required key 'name'")
            _require_str(receiver, "name", path)
            if "type" not in receiver:
                raise SchemaError(f"{path}.type: missing required key 'type'")
            rtype = _require_str(receiver, "type", path)
            if rtype not in known_types:
                raise SchemaError(
                    f"{path}.type: '{rtype}' not in {sorted(known_types)}"
                )

    scrape_interval = monitoring.get("scrape_interval")
    if scrape_interval is not None and (
        not isinstance(scrape_interval, str) or not re.fullmatch(r"\d+[smhd]", scrape_interval)
    ):
        raise SchemaError("monitoring.scrape_interval: must match Ns|Nm|Nh|Nd form (e.g. 15s)")


def _validate_backup_cron(value: str, field: str) -> None:
    parts = value.split()
    if len(parts) != 5:
        raise SchemaError(f"{field}: unsupported cron expression")

    minute, hour, day_of_month, month, day_of_week = parts
    if day_of_month != "*" or month != "*":
        raise SchemaError(f"{field}: unsupported cron expression")
    if not minute.isdigit() or not 0 <= int(minute) <= 59:
        raise SchemaError(f"{field}: unsupported cron expression")
    if not hour.isdigit() or not 0 <= int(hour) <= 23:
        raise SchemaError(f"{field}: unsupported cron expression")

    dow_parts = day_of_week.split("-", maxsplit=1)
    if not all(part.isdigit() for part in dow_parts):
        raise SchemaError(f"{field}: unsupported cron expression")
    dow_values = [int(part) for part in dow_parts]
    if not all(0 <= day <= 6 for day in dow_values):
        raise SchemaError(f"{field}: unsupported cron expression")
    if len(dow_values) == 2 and dow_values[0] > dow_values[1]:
        raise SchemaError(f"{field}: unsupported cron expression")


def _validate_backup(backup: Any) -> None:
    if backup is None:
        return
    if not isinstance(backup, dict):
        raise SchemaError("backup: must be a mapping")
    enabled = backup.get("enabled", False)
    if not isinstance(enabled, bool):
        raise SchemaError("backup.enabled: must be a boolean")
    if not enabled:
        return

    tool = backup.get("tool", "pgbackrest")
    if tool != "pgbackrest":
        raise SchemaError("backup.tool: unsupported backup tool")

    retention = backup.get("retention", {})
    if retention:
        if not isinstance(retention, dict):
            raise SchemaError("backup.retention: must be a mapping")
        full = retention.get("full")
        if full is not None and (not isinstance(full, int) or isinstance(full, bool) or full < 1):
            raise SchemaError("backup.retention.full: must be a positive integer")

    schedule = backup.get("schedule", {})
    if schedule:
        if not isinstance(schedule, dict):
            raise SchemaError("backup.schedule: must be a mapping")
        for key in ("full", "differential"):
            value = schedule.get(key)
            if value is None:
                continue
            if not isinstance(value, str):
                raise SchemaError(f"backup.schedule.{key}: must be a cron string")
            _validate_backup_cron(value, f"backup.schedule.{key}")

    secondary_store = backup.get("secondary_store", {})
    if secondary_store:
        if not isinstance(secondary_store, dict):
            raise SchemaError("backup.secondary_store: must be a mapping")
        enabled_secondary = secondary_store.get("enabled", False)
        if not isinstance(enabled_secondary, bool):
            raise SchemaError("backup.secondary_store.enabled: must be a boolean")
        if enabled_secondary:
            for field in ("type", "bucket", "endpoint"):
                if not secondary_store.get(field):
                    raise SchemaError(
                        f"backup.secondary_store.{field}: required when secondary_store is enabled"
                    )


def validate(data: Any) -> None:
    """Validate a response-file dict in place. Raises SchemaError on failure."""
    if not isinstance(data, dict):
        raise SchemaError("response file: top-level must be a mapping")

    profile = _require_str(data, "profile", "")
    if profile not in ALLOWED_PROFILES:
        raise SchemaError(f"profile: '{profile}' not in {sorted(ALLOWED_PROFILES)}")
    ip_version = _validate_network(data.get("network"))

    cluster = _require(data, "cluster", "")
    if not isinstance(cluster, dict):
        raise SchemaError("cluster: must be a mapping")
    _require_str(cluster, "name", "cluster")
    _require_str(cluster, "domain", "cluster")

    nodes = _require(data, "nodes", "")
    _validate_nodes(nodes, profile, ip_version)
    _validate_postgres(_require(data, "postgres", ""), ip_version)
    _validate_tls(_require(data, "tls", ""))
    _validate_firewall(_require(data, "firewall", ""), ip_version)
    _validate_monitoring(_require(data, "monitoring", ""))
    _validate_backup(data.get("backup"))
    _validate_connection_layer(data.get("connection_layer"), ip_version)
