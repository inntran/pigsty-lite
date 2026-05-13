"""group_vars/response.yml generator: response dict -> Ansible vars YAML."""

from __future__ import annotations

from io import StringIO
from typing import Any

import yaml

BANNER = (
    "# GENERATED FILE - DO NOT EDIT.\n"
    "# Regenerate via: ./configure -s -f responses/site.rsp.yml\n"
    "# This is the operator-facing variable layer; edit the response file.\n"
)


def _flatten_pgbackrest(pgbackrest: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"pgbackrest_enabled": bool(pgbackrest.get("enabled", False))}
    if not out["pgbackrest_enabled"]:
        return out

    schedule = pgbackrest.get("schedule", {})
    if "full" in schedule:
        out["pgbackrest_schedule_full"] = schedule["full"]
    if "differential" in schedule:
        out["pgbackrest_schedule_differential"] = schedule["differential"]

    retention = pgbackrest.get("retention", {})
    if "full" in retention:
        out["pgbackrest_retention_full"] = retention["full"]
    if pgbackrest.get("repo2", {}).get("enabled"):
        out["pgbackrest_repo2"] = pgbackrest["repo2"]
    return out


def generate(response: dict[str, Any]) -> str:
    """Produce group_vars/response.yml content from a validated response dict."""
    postgres = response["postgres"]
    tls = response["tls"]
    monitoring = response["monitoring"]
    firewall = response["firewall"]
    repos = response.get("repos", {})

    out: dict[str, Any] = {
        "cluster_profile": response["profile"],
        "cluster_name": response["cluster"]["name"],
        "cluster_domain": response["cluster"]["domain"],
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
        "operator_cidrs": firewall["operator_cidrs"],
        "postgres_client_cidrs": firewall["postgres_client_cidrs"],
        "repos_pigsty_enabled": bool(repos.get("pigsty", {}).get("enabled", False)),
        "repos_pigsty_packages": repos.get("pigsty", {}).get("packages", []),
    }
    out.update(_flatten_pgbackrest(response.get("pgbackrest", {})))

    buf = StringIO()
    buf.write(BANNER)
    yaml.safe_dump(out, buf, sort_keys=False, default_flow_style=False)
    return buf.getvalue()
