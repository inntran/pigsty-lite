"""group_vars/response.yml generator: response dict -> Ansible vars YAML."""

from __future__ import annotations

from io import StringIO
from typing import Any

import yaml

BANNER = (
    "# GENERATED FILE - DO NOT EDIT.\n"
    "# Regenerate via: ./configure -s -f responses/site.rsp.yml\n"
    "# This is the operator-facing variable layer; edit the response file.\n"
    "---\n"
)


DAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


def _cron_to_oncalendar(value: str) -> str:
    minute, hour, _, _, day_of_week = value.split()
    if "-" in day_of_week:
        start, end = (int(part) for part in day_of_week.split("-", maxsplit=1))
        days = ",".join(DAY_NAMES[day] for day in range(start, end + 1))
    else:
        days = DAY_NAMES[int(day_of_week)]
    return f"{days} *-*-* {int(hour):02d}:{int(minute):02d}:00"


def _flatten_backup(backup: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"backup_enabled": bool(backup.get("enabled", False))}
    if not out["backup_enabled"]:
        return out

    out["backup_tool"] = backup.get("tool", "pgbackrest")

    schedule = backup.get("schedule", {})
    if "full" in schedule:
        out["backup_schedule_full"] = schedule["full"]
        out["backup_schedule_full_oncalendar"] = _cron_to_oncalendar(schedule["full"])
    if "differential" in schedule:
        out["backup_schedule_differential"] = schedule["differential"]
        out["backup_schedule_differential_oncalendar"] = _cron_to_oncalendar(
            schedule["differential"]
        )

    retention = backup.get("retention", {})
    if "full" in retention:
        out["backup_retention_full"] = retention["full"]
    if backup.get("secondary_store", {}).get("enabled"):
        out["backup_secondary_store"] = backup["secondary_store"]
    return out


def generate(response: dict[str, Any]) -> str:
    """Produce group_vars/response.yml content from a validated response dict."""
    postgres = response["postgres"]
    tls = response["tls"]
    monitoring = response["monitoring"]
    firewall = response["firewall"]
    repos = response.get("repos", {})
    ip_version = response.get("network", {}).get("ip_version", "dual")
    ipv6_single_stack = ip_version == "ipv6"
    loopback_addresses = ["::1"] if ipv6_single_stack else ["127.0.0.1"]
    if ip_version == "dual":
        loopback_addresses.append("::1")

    out: dict[str, Any] = {
        "cluster_profile": response["profile"],
        "cluster_name": response["cluster"]["name"],
        "cluster_domain": response["cluster"]["domain"],
        "network_ip_version": ip_version,
        "network_ipv6_single_stack": ipv6_single_stack,
        "network_loopback_address": "::1" if ipv6_single_stack else "127.0.0.1",
        "network_loopback_addresses": loopback_addresses,
        "network_any_address": "::" if ipv6_single_stack else "0.0.0.0",
        "haproxy_loopback_listen_addresses": ["127.0.0.2"],
        "postgres_version": postgres["version"],
        "postgres_port": postgres["port"],
        "postgres_tune_profile": postgres["tune"],
        "postgres_shared_buffer_ratio": postgres.get("shared_buffer_ratio", 0.25),
        "postgres_extensions": postgres.get("extensions", []),
        "postgres_databases": postgres.get("databases", []),
        "postgres_users": postgres.get("users", []),
        "postgres_hba_rules": postgres.get("hba_rules", []),
        "postgres_extra_parameters": postgres.get("extra_parameters", {}),
        "postgres_pin_version": postgres.get("pin_version", ""),
        "ca_mode": tls["internal_ca"],
        "nginx_proxy_tls_mode": tls["user_facing"]["mode"],
        "vmsingle_retention": monitoring["vmsingle_retention"],
        "vlsingle_retention": monitoring["vlsingle_retention"],
        "alertmanager_receivers": monitoring.get("alertmanager", {}).get("receivers", []),
        "monitoring_scrape_interval": monitoring.get("scrape_interval", "15s"),
        "operator_cidrs": firewall["operator_cidrs"],
        "postgres_client_cidrs": firewall["postgres_client_cidrs"],
        "repos_pigsty_enabled": bool(repos.get("pigsty", {}).get("enabled", False)),
        "repos_pigsty_packages": repos.get("pigsty", {}).get("packages", []),
    }
    out.update(_flatten_backup(response.get("backup", {})))

    conn = response.get("connection_layer", {}) or {}
    hap = conn.get("haproxy", {}) or {}
    vip = conn.get("vip_manager", {}) or {}

    out["haproxy_rto_profile"] = hap.get("rto_profile", "norm")
    out["haproxy_backend_target"] = hap.get("backend_target", "pgbouncer")
    out["vip_manager_enabled"] = bool(vip.get("enabled", False))
    if out["vip_manager_enabled"]:
        out["vip_manager_vip_cidr"] = vip["vip_cidr"]
        out["vip_manager_interface"] = vip["interface"]

    buf = StringIO()
    buf.write(BANNER)
    yaml.safe_dump(out, buf, sort_keys=False, default_flow_style=False)
    return buf.getvalue()
