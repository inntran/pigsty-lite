"""Tests for the pgbouncer role config template."""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment

ROOT = Path(__file__).resolve().parents[2]


def _render_pgbouncer_config() -> str:
    template = Environment(trim_blocks=False, lstrip_blocks=False).from_string(
        (ROOT / "roles/pgbouncer/templates/pgbouncer.ini.j2").read_text()
    )
    return template.render(
        ansible_managed="test",
        pgbouncer_upstream_host="127.0.0.1",
        pgbouncer_upstream_port=5432,
        pgbouncer_listen_address="0.0.0.0",
        pgbouncer_listen_port=6432,
        pgbouncer_unix_socket_dir="/var/run/pgbouncer",
        pgbouncer_pid_file="/var/run/pgbouncer/pgbouncer.pid",
        pgbouncer_auth_type="scram-sha-256",
        pgbouncer_auth_file="/etc/pgbouncer/userlist.txt",
        pgbouncer_auth_user="postgres",
        pgbouncer_auth_query="SELECT usename, passwd FROM pg_shadow WHERE usename=$1",
        pgbouncer_admin_users=["postgres"],
        pgbouncer_stats_users=["postgres"],
        pgbouncer_pool_mode="transaction",
        pgbouncer_max_client_conn=1000,
        pgbouncer_default_pool_size=25,
        pgbouncer_reserve_pool_size=5,
        pgbouncer_server_idle_timeout=600,
        pgbouncer_server_lifetime=3600,
        pgbouncer_log_file="/var/log/pgbouncer/pgbouncer.log",
        pgbouncer_log_connections=1,
        pgbouncer_log_disconnections=1,
        pgbouncer_log_pooler_errors=1,
    )


def test_pgbouncer_includes_auth_query_for_scram():
    rendered = _render_pgbouncer_config()
    assert "auth_query = SELECT usename, passwd FROM pg_shadow WHERE usename=$1" in rendered
