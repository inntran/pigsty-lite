"""Generate per-deployment external monitoring operator notes."""

from __future__ import annotations

from typing import Any

import yaml


def _target_names(nodes: dict[str, dict[str, Any]], postgres_only: bool = False) -> list[str]:
    names: list[str] = []
    for name, node in nodes.items():
        if postgres_only and node["role"] not in {"pg_primary", "pg_replica"}:
            continue
        names.append(name)
    return names


def _external_pull(response: dict[str, Any]) -> str:
    pull = response["monitoring"]["external_pull"]
    scheme = "https" if pull.get("tls", True) else "http"
    port = pull["metrics_port"]
    username = pull["auth"]["username"]
    nodes = response["nodes"]
    jobs = [
        ("pigsty-node", "/metrics/node", _target_names(nodes)),
        ("pigsty-postgres", "/metrics/postgres", _target_names(nodes, postgres_only=True)),
        ("pigsty-pgbouncer", "/metrics/pgbouncer", _target_names(nodes, postgres_only=True)),
        ("pigsty-pgbackrest", "/metrics/pgbackrest", _target_names(nodes, postgres_only=True)),
    ]
    scrape_configs = []
    for job_name, metrics_path, targets in jobs:
        if not targets:
            continue
        scrape_configs.append(
            {
                "job_name": job_name,
                "scheme": scheme,
                "metrics_path": metrics_path,
                "basic_auth": {
                    "username": username,
                    "password": "<from vault: vault_monitoring_pull_password>",
                },
                "static_configs": [
                    {"targets": [f"{target}:{port}" for target in targets]},
                ],
            }
        )

    block = yaml.safe_dump({"scrape_configs": scrape_configs}, sort_keys=False)
    return (
        "# External monitoring scrape config\n\n"
        "Paste this into a Prometheus- or vmagent-style scraper. The password is stored in "
        "`vault_monitoring_pull_password` and is intentionally not rendered here.\n\n"
        "```yaml\n"
        f"{block}"
        "```\n"
    )


def _external_push(response: dict[str, Any]) -> str:
    push = response["monitoring"]["external_push"]
    auth = push.get("auth", {}) or {}
    lines = [
        "# External monitoring push checklist",
        "",
        "pigsty-lite agents push metrics and logs to the configured external service.",
        "",
        f"- Metrics remote-write URL: `{push['metrics_url']}`",
        f"- Logs ingest URL: `{push['logs_url']}`",
        f"- TLS skip verify: `{str(push.get('tls_skip_verify', False)).lower()}`",
    ]
    if auth.get("username"):
        lines.append(f"- Basic-auth username: `{auth['username']}`")
        lines.append("- Basic-auth password: `<from vault: vault_monitoring_external_password>`")
    if auth.get("bearer", False):
        lines.append("- Bearer token: `<from vault: vault_monitoring_external_bearer_token>`")
    if not auth:
        lines.append("- Auth: none configured")
    lines.append("")
    return "\n".join(lines)


def generate(response: dict[str, Any]) -> str:
    """Generate responses/external-monitoring.md for an external monitoring mode."""
    mode = response["monitoring"].get("mode", "self_hosted")
    if mode == "external_pull":
        return _external_pull(response)
    if mode == "external_push":
        return _external_push(response)
    return ""
