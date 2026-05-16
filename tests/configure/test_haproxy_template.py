"""Tests for the haproxy role config template."""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment

ROOT = Path(__file__).resolve().parents[2]


def _render_haproxy_config(
    *,
    haproxy_client_listen_addresses: list[str] | None = None,
) -> str:
    template = Environment(trim_blocks=False, lstrip_blocks=False).from_string(
        (ROOT / "roles/haproxy/templates/haproxy.cfg.j2").read_text()
    )
    return template.render(
        ansible_managed="test",
        groups={"postgres": ["pgnode01", "pgnode02"]},
        hostvars={
            "pgnode01": {"ansible_host": "10.0.0.11"},
            "pgnode02": {"ansible_host": "10.0.0.12"},
        },
        haproxy_maxconn=4096,
        haproxy_stats_listen_address="127.0.0.1",
        haproxy_stats_port=7000,
        haproxy_stats_refresh_seconds=10,
        haproxy_stats_user="pigsty",
        haproxy_stats_password="secret",
        haproxy_client_listen_addresses=haproxy_client_listen_addresses or ["10.0.0.11"],
        haproxy_default_port=5432,
        haproxy_primary_port=5433,
        haproxy_replica_port=5434,
        haproxy_check_interval_ms=3000,
        haproxy_check_fall=3,
        haproxy_check_rise=2,
        haproxy_backend_port=6432,
        haproxy_patroni_rest_port=8008,
        haproxy_patroni_rest_ca_file="/etc/pki/pigsty/ca.crt",
    )


def test_haproxy_health_checks_verify_patroni_tls():
    rendered = _render_haproxy_config()
    assert "check-ssl verify required ca-file /etc/pki/pigsty/ca.crt" in rendered
    assert rendered.count("check-ssl verify required ca-file /etc/pki/pigsty/ca.crt") == 6


def test_haproxy_binds_client_services_to_all_client_addresses():
    rendered = _render_haproxy_config(
        haproxy_client_listen_addresses=[
            "127.0.0.2",
            "10.0.0.11",
            "10.20.30.20",
            "2001:db8::11",
            "2001:db8::20",
        ]
    )

    assert "bind 127.0.0.2:5432" in rendered
    assert "bind 10.0.0.11:5432" in rendered
    assert "bind 10.20.30.20:5432" in rendered
    assert "bind [2001:db8::11]:5432" in rendered
    assert "bind [2001:db8::20]:5432" in rendered
    assert "bind 127.0.0.2:5433" in rendered
    assert "bind 10.0.0.11:5433" in rendered
    assert "bind 10.20.30.20:5433" in rendered
    assert "bind 127.0.0.2:5434" in rendered
    assert "bind [2001:db8::11]:5434" in rendered
    assert "bind [2001:db8::20]:5434" in rendered
