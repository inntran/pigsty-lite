"""Static checks for the exporter_exporter external_pull endpoint."""

from __future__ import annotations

from pathlib import Path

import yaml
from jinja2 import Environment

ROOT = Path(__file__).resolve().parents[2]
ROLE = ROOT / "roles/monitoring_agents"


def _render_template(path: str, **context) -> str:
    environment = Environment(trim_blocks=False, lstrip_blocks=False)
    environment.filters["bool"] = bool
    template = environment.from_string((ROLE / path).read_text())
    variables = {
        "ansible_managed": "test",
        "groups": {"postgres": ["pgnode01"]},
        "inventory_hostname": "pgnode01",
        "network_loopback_address": "127.0.0.1",
        "monitoring_agents_node_exporter_port": 9100,
        "monitoring_agents_postgres_exporter_port": 9187,
        "monitoring_agents_pgbouncer_exporter_port": 9127,
        "monitoring_agents_pgbackrest_exporter_port": 9854,
        "monitoring_agents_exporter_exporter_user": "exporter_exporter",
        "monitoring_agents_exporter_exporter_config": "/etc/exporter_exporter/expexp.yaml",
        "monitoring_agents_exporter_exporter_token_file": "/etc/exporter_exporter/token",
        "monitoring_agents_pull_listen_address": "0.0.0.0",
        "monitoring_agents_pull_metrics_port": 9999,
        "monitoring_agents_pull_tls": False,
        "monitoring_agents_pull_tls_cert": "/etc/pki/pigsty/node.crt",
        "monitoring_agents_pull_tls_key": "/etc/pki/pigsty/node.key",
    }
    variables.update(context)
    return template.render(**variables)


def test_exporter_modules_are_host_specific():
    node_only = _render_template(
        "templates/expexp.yaml.j2",
        groups={"postgres": []},
    )
    postgres_host = _render_template("templates/expexp.yaml.j2")

    node_config = yaml.safe_load(node_only)
    postgres_config = yaml.safe_load(postgres_host)
    assert set(node_config["modules"]) == {"node"}
    assert set(postgres_config["modules"]) == {
        "node",
        "postgres",
        "pgbouncer",
        "pgbackrest",
    }
    assert node_config["modules"]["node"]["http"] == {
        "address": "127.0.0.1",
        "port": 9100,
    }
    assert postgres_config["modules"]["pgbackrest"]["http"]["port"] == 9854


def test_service_listen_flags_follow_tls_and_address_family():
    tls_service = _render_template(
        "templates/exporter-exporter.service.j2",
        monitoring_agents_pull_tls=True,
    )
    plaintext_service = _render_template("templates/exporter-exporter.service.j2")
    ipv6_service = _render_template(
        "templates/exporter-exporter.service.j2",
        monitoring_agents_pull_listen_address="::",
    )

    assert "-web.listen-address= " in tls_service
    assert "-web.tls.listen-address=0.0.0.0:9999" in tls_service
    assert "-web.listen-address=0.0.0.0:9999" in plaintext_service
    assert "-web.tls." not in plaintext_service
    assert "-web.listen-address=[::]:9999" in ipv6_service


def test_role_has_no_nginx_frontend():
    forbidden = ("nginx" + "-metrics", "ht" + "passwd", "99" + "65")
    references = {
        path: [term for term in forbidden if term in path.read_text()]
        for path in ROLE.rglob("*")
        if path.is_file()
    }
    assert not {path: terms for path, terms in references.items() if terms}
