"""Render tests for roles/pgbackrest/templates/pgbackrest.conf.j2."""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment

ROOT = Path(__file__).resolve().parents[2]


def _render(mode: str, **overrides: object) -> str:
    env = Environment()
    env.filters["bool"] = lambda v: (
        str(v).strip().lower()
        in {
            "1",
            "true",
            "yes",
            "on",
            "y",
        }
    )
    template = env.from_string((ROOT / "roles/pgbackrest/templates/pgbackrest.conf.j2").read_text())
    ctx = {
        "ansible_managed": "test",
        "pgbackrest_mode": mode,
        "pgbackrest_log_path": "/var/log/pgbackrest",
        "pgbackrest_repo_path": "/var/lib/pgbackrest",
        "pgbackrest_retention_full": 4,
        "pgbackrest_s3_enabled": False,
        "pgbackrest_server_host": "pgmon01",
        "pgbackrest_tls_port": 8432,
        "pgbackrest_stanza": "pg-aio",
        "pgbackrest_pg_path": "/var/lib/pgsql/18/data",
        "pgbackrest_pg_port": 5432,
        "pigsty_pki_dir": "/etc/pki/pigsty",
        "inventory_hostname": "pgaio01",
        "groups": {"postgres": ["pgaio01"]},
        "hostvars": {"pgaio01": {}},
    }
    ctx.update(overrides)
    return template.render(**ctx)


def test_local_mode_has_repo_and_pg_path_without_tls():
    out = _render("local")
    assert "repo1-path=/var/lib/pgbackrest" in out
    assert "repo1-retention-full=4" in out
    assert "pg1-path=/var/lib/pgsql/18/data" in out
    assert "archive-async=y" in out
    assert "repo1-host" not in out
    assert "tls-server" not in out
    assert "pg1-host" not in out


def test_s3_block_only_when_enabled():
    assert "repo2-type" not in _render("local")
    assert "repo2-type" not in _render("local", pgbackrest_s3_enabled="False")
    out = _render(
        "local",
        pgbackrest_s3_enabled=True,
        pgbackrest_s3_bucket="b",
        pgbackrest_s3_endpoint="s3.example.com",
        pgbackrest_s3_region="us-east-1",
        pgbackrest_s3_path="/pgbackrest",
        pgbackrest_s3_key="k",
        pgbackrest_s3_key_secret="s",
        pgbackrest_s3_retention_full=4,
    )
    assert "repo2-type=s3" in out
    assert "repo2-s3-bucket=b" in out


def test_client_mode_s3_block_is_available_for_archive_push():
    out = _render(
        "client",
        pgbackrest_s3_enabled=True,
        pgbackrest_s3_bucket="b",
        pgbackrest_s3_endpoint="s3.example.com",
        pgbackrest_s3_region="us-east-1",
        pgbackrest_s3_path="/pgbackrest",
        pgbackrest_s3_key="k",
        pgbackrest_s3_key_secret="s",
        pgbackrest_s3_retention_full=4,
    )
    assert "repo2-type=s3" in out
    assert "repo2-s3-bucket=b" in out


def test_client_and_server_modes_unchanged():
    client = _render("client")
    assert "repo1-host=pgmon01" in client
    assert "tls-server-auth=pgmon01=pg-aio" in client
    server = _render("server", inventory_hostname="pgmon01")
    assert "pg1-host=pgaio01" in server
    assert "tls-server-auth=pgaio01=pg-aio" in server
