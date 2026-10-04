"""Tests for the etcd role config template."""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment

ROOT = Path(__file__).resolve().parents[2]


def _render_etcd_config() -> str:
    template = Environment(trim_blocks=False, lstrip_blocks=False).from_string(
        (ROOT / "roles/etcd/templates/etcd.conf.yml.j2").read_text()
    )
    return template.render(
        ansible_managed="test",
        groups={"etcd": ["pgnode01", "pgnode02", "pgnode03"]},
        hostvars={
            "pgnode01": {
                "etcd_advertise_address": "2001:db8:10::11",
                "ansible_host": "2001:db8:10::11",
            },
            "pgnode02": {
                "etcd_advertise_address": "2001:db8:10::12",
                "ansible_host": "2001:db8:10::12",
            },
            "pgnode03": {
                "etcd_advertise_address": "2001:db8:10::13",
                "ansible_host": "2001:db8:10::13",
            },
        },
        etcd_member_name="pgnode01",
        etcd_data_dir="/var/lib/etcd",
        etcd_listen_url_host="[::]",
        etcd_advertise_url_host="[2001:db8:10::11]",
        etcd_client_port=2379,
        etcd_peer_port=2380,
        etcd_cluster_token="pigsty-lite-test-etcd",
        etcd_initial_cluster_state="new",
        etcd_cert_file="/etc/pki/pigsty/pgnode01.crt",
        etcd_key_file="/etc/pki/pigsty/pgnode01.key",
        etcd_trusted_ca_file="/etc/pki/pigsty/ca.crt",
    )


def test_etcd_template_brackets_ipv6_urls():
    rendered = _render_etcd_config()
    assert "listen-client-urls: https://[::]:2379" in rendered
    assert "advertise-client-urls: https://[2001:db8:10::11]:2379" in rendered
    assert "initial-advertise-peer-urls: https://[2001:db8:10::11]:2380" in rendered


def test_etcd_initial_cluster_brackets_ipv6_peers():
    rendered = _render_etcd_config()
    assert (
        "initial-cluster: pgnode01=https://[2001:db8:10::11]:2380,"
        "pgnode02=https://[2001:db8:10::12]:2380,"
        "pgnode03=https://[2001:db8:10::13]:2380"
    ) in rendered


def test_etcd_initial_cluster_falls_back_to_ipv4_when_ipv6_facts_are_empty():
    """A host with no IPv6 default route still reports `default_ipv6: {}`.

    The peer-host fallback chain must look at the address key, not at the
    parent dict: `{} is defined` is true, so guarding on the parent picks the
    IPv6 branch and then fails rendering with "'dict' has no attribute
    'address'". Inventories that set `ansible_host` never reach this branch,
    which is why it only shows up under molecule.
    """
    template = Environment(trim_blocks=False, lstrip_blocks=False).from_string(
        (ROOT / "roles/etcd/templates/etcd.conf.yml.j2").read_text()
    )
    rendered = template.render(
        ansible_managed="test",
        groups={"etcd": ["pgnode01"]},
        hostvars={
            "pgnode01": {
                "ansible_facts": {
                    "default_ipv6": {},
                    "default_ipv4": {"address": "10.89.0.3"},
                },
            },
        },
        etcd_member_name="pgnode01",
        etcd_data_dir="/var/lib/etcd",
        etcd_listen_url_host="0.0.0.0",
        etcd_advertise_url_host="10.89.0.3",
        etcd_client_port=2379,
        etcd_peer_port=2380,
        etcd_cluster_token="pigsty-lite-test-etcd",
        etcd_initial_cluster_state="new",
        etcd_cert_file="/etc/pki/pigsty/pgnode01.crt",
        etcd_key_file="/etc/pki/pigsty/pgnode01.key",
        etcd_trusted_ca_file="/etc/pki/pigsty/ca.crt",
    )

    assert "initial-cluster: pgnode01=https://10.89.0.3:2380" in rendered
