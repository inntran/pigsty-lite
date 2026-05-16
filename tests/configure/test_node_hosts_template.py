"""Tests for the node role /etc/hosts template."""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment

ROOT = Path(__file__).resolve().parents[2]


def _render_hosts(*, ipv6_single_stack: bool) -> str:
    template = Environment(trim_blocks=False, lstrip_blocks=False).from_string(
        (ROOT / "roles/node/templates/hosts.j2").read_text()
    )
    return template.render(
        cluster_domain="example.internal",
        groups={
            "monitor": ["pgmon01"],
            "backup_server": ["pgmon01"],
            "postgres": ["pgnode01"],
            "etcd": ["pgnode01"],
        },
        hostvars={
            "pgmon01": {"ansible_host": "2001:db8:10::10"},
            "pgnode01": {"ansible_host": "2001:db8:10::11"},
        },
        network_ipv6_single_stack=ipv6_single_stack,
    )


def test_hosts_template_omits_ipv4_localhost_in_ipv6_single_stack():
    rendered = _render_hosts(ipv6_single_stack=True)
    assert "127.0.0.1   localhost localhost.localdomain" not in rendered
    assert "::1         localhost localhost.localdomain" in rendered
    assert "2001:db8:10::11 pgnode01 pgnode01.example.internal" in rendered


def test_hosts_template_keeps_ipv4_localhost_by_default():
    rendered = _render_hosts(ipv6_single_stack=False)
    assert "127.0.0.1   localhost localhost.localdomain" in rendered
    assert "::1         localhost localhost.localdomain" in rendered
