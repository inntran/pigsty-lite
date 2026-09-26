"""Tests for the pgbouncer role config templates."""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment

ROOT = Path(__file__).resolve().parents[2]


def _render_pgbouncer_config(**overrides: object) -> str:
    template = Environment(trim_blocks=False, lstrip_blocks=False).from_string(
        (ROOT / "roles/pgbouncer/templates/pgbouncer.ini.j2").read_text()
    )
    context = {
        "ansible_managed": "test",
        "pgbouncer_upstream_host": "127.0.0.1",
        "pgbouncer_upstream_port": 5432,
        "pgbouncer_listen_address": "0.0.0.0",
        "pgbouncer_listen_port": 6432,
        "pgbouncer_unix_socket_dir": "/run/pgbouncer",
        "pgbouncer_pid_file": "/run/pgbouncer/pgbouncer.pid",
        "pgbouncer_auth_type": "hba",
        "pgbouncer_auth_hba_file": "/etc/pgbouncer/pgbouncer_hba.conf",
        "pgbouncer_auth_file": "/etc/pgbouncer/userlist.txt",
        "pgbouncer_auth_user": "postgres",
        "pgbouncer_auth_query": "SELECT usename, passwd FROM pg_shadow WHERE usename=$1",
        "pgbouncer_admin_users": ["postgres"],
        "pgbouncer_stats_users": ["postgres"],
        "pgbouncer_pool_mode": "transaction",
        "pgbouncer_max_client_conn": 1000,
        "pgbouncer_default_pool_size": 25,
        "pgbouncer_reserve_pool_size": 5,
        "pgbouncer_server_idle_timeout": 600,
        "pgbouncer_server_lifetime": 3600,
        "pgbouncer_log_file": "/var/log/pgbouncer/pgbouncer.log",
        "pgbouncer_log_connections": 1,
        "pgbouncer_log_disconnections": 1,
        "pgbouncer_log_pooler_errors": 1,
    }
    context.update(overrides)
    return template.render(**context)


def _render_pgbouncer_hba(**overrides: object) -> str:
    template = Environment(trim_blocks=False, lstrip_blocks=False).from_string(
        (ROOT / "roles/pgbouncer/templates/pgbouncer_hba.conf.j2").read_text()
    )
    context = {
        "ansible_managed": "test",
        "pgbouncer_peer_console_user": "postgres",
    }
    context.update(overrides)
    return template.render(**context)


def test_pgbouncer_includes_auth_query_for_scram():
    rendered = _render_pgbouncer_config()
    assert "auth_query = SELECT usename, passwd FROM pg_shadow WHERE usename=$1" in rendered


def test_pgbouncer_includes_auth_hba_file_for_hba_auth_type():
    rendered = _render_pgbouncer_config()
    assert "auth_type = hba" in rendered
    assert "auth_hba_file = /etc/pgbouncer/pgbouncer_hba.conf" in rendered


def test_pgbouncer_omits_auth_hba_file_for_scram_auth_type():
    rendered = _render_pgbouncer_config(pgbouncer_auth_type="scram-sha-256")
    assert "auth_type = scram-sha-256" in rendered
    assert "auth_hba_file" not in rendered


def test_pgbouncer_hba_first_rule_is_peer_for_console_user():
    rendered = _render_pgbouncer_hba()
    rules = [
        line.split() for line in rendered.splitlines() if line.strip() and not line.startswith("#")
    ]
    assert rules[0] == ["local", "pgbouncer", "postgres", "peer"]


def test_pgbouncer_hba_remaining_rules_are_scram_sha_256():
    rendered = _render_pgbouncer_hba()
    rules = [
        line.split() for line in rendered.splitlines() if line.strip() and not line.startswith("#")
    ]
    assert rules[1:] == [
        ["local", "all", "all", "scram-sha-256"],
        ["host", "all", "all", "0.0.0.0/0", "scram-sha-256"],
        ["host", "all", "all", "::/0", "scram-sha-256"],
    ]


def test_pgbouncer_hba_never_uses_trust():
    rendered = _render_pgbouncer_hba()
    assert "trust" not in rendered
