"""Static checks for the pgbackrest role and its playbook wiring."""

from __future__ import annotations

from pathlib import Path

import yaml
from jinja2 import Environment

ROOT = Path(__file__).resolve().parents[2]


def _defaults() -> dict:
    return yaml.safe_load((ROOT / "roles/pgbackrest/defaults/main.yml").read_text())


def _eval(expr: str, **ctx: object) -> str:
    return Environment().from_string(str(expr)).render(**ctx)


def _tasks(path: str) -> list:
    return yaml.safe_load((ROOT / path).read_text())


def test_retention_and_schedule_follow_response_vars():
    d = _defaults()
    assert _eval(d["pgbackrest_retention_full"], backup_retention_full=7) == "7"
    assert _eval(d["pgbackrest_retention_full"]) == "4"
    assert (
        _eval(d["pgbackrest_schedule_full"], backup_schedule_full_oncalendar="Sat *-*-* 02:00:00")
        == "Sat *-*-* 02:00:00"
    )
    assert _eval(d["pgbackrest_schedule_full"]) == "Sun *-*-* 01:00:00"
    assert (
        _eval(
            d["pgbackrest_schedule_diff"],
            backup_schedule_differential_oncalendar="Mon *-*-* 03:00:00",
        )
        == "Mon *-*-* 03:00:00"
    )
    assert _eval(d["pgbackrest_schedule_diff"]) == "Mon..Sat *-*-* 01:00:00"


def test_s3_follows_secondary_store():
    d = _defaults()
    store = {
        "enabled": True,
        "type": "s3",
        "bucket": "b",
        "endpoint": "s3.example.com",
        "region": "eu-west-1",
        "path": "/aio",
    }
    assert _eval(d["pgbackrest_s3_enabled"], backup_secondary_store=store) == "True"
    assert _eval(d["pgbackrest_s3_enabled"]) == "False"
    assert _eval(d["pgbackrest_s3_bucket"], backup_secondary_store=store) == "b"
    assert _eval(d["pgbackrest_s3_endpoint"], backup_secondary_store=store) == "s3.example.com"
    assert _eval(d["pgbackrest_s3_region"], backup_secondary_store=store) == "eu-west-1"
    assert _eval(d["pgbackrest_s3_region"]) == "us-east-1"
    assert _eval(d["pgbackrest_s3_path"], backup_secondary_store=store) == "/aio"
    assert _eval(d["pgbackrest_s3_path"]) == "/pgbackrest"
    assert "vault_pgbackrest_s3_key" in d["pgbackrest_s3_key"]
    assert "vault_pgbackrest_s3_key_secret" in d["pgbackrest_s3_key_secret"]


def test_site_gates_pgbackrest_on_backup_enabled():
    site = yaml.safe_load((ROOT / "playbooks/site.yml").read_text())
    entry = next(e for e in site if e["import_playbook"] == "_pgbackrest.yml")
    assert entry["when"] == "backup_enabled | default(true) | bool"


def test_s3_configuration_task_hides_rendered_credentials():
    tasks = {t["name"]: t for t in _tasks("roles/pgbackrest/tasks/_config.yml")}
    assert tasks["Render pgBackRest configuration"]["no_log"] == (
        "{{ pgbackrest_s3_enabled | bool }}"
    )


def test_main_accepts_local_mode_and_skips_tls_pieces():
    tasks = {t["name"]: t for t in _tasks("roles/pgbackrest/tasks/main.yml")}
    assert (
        "'local'"
        in tasks["Assert pgbackrest_mode is set to a valid value"]["ansible.builtin.assert"][
            "that"
        ][1]
    )
    assert tasks["Deploy and start pgBackRest TLS server daemon"]["when"] == (
        "pgbackrest_mode in ['server', 'client']"
    )
    assert tasks["Open firewalld for pgBackRest TLS port"]["when"] == (
        "pgbackrest_mode in ['server', 'client']"
    )
    assert tasks["Create pgBackRest stanza"]["when"] == "pgbackrest_mode in ['server', 'local']"
    assert tasks["Install backup timers"]["when"] == "pgbackrest_mode in ['server', 'local']"
    local_assert = tasks["Assert local mode runs on a single-node cluster"]
    assert any(
        "groups['postgres'] | length == 1" in condition
        for condition in local_assert["ansible.builtin.assert"]["that"]
    )


def test_install_prepares_repo_for_server_and_local_modes():
    tasks = {t["name"]: t for t in _tasks("roles/pgbackrest/tasks/_install.yml")}
    assert tasks["Ensure pgBackRest repo directory exists"]["when"] == (
        "pgbackrest_mode in ['server', 'local']"
    )
    for task_name in (
        "Register SELinux fcontext for repo directory",
        "Relabel repo directory if fcontext changed",
    ):
        assert tasks[task_name]["when"][0] == "pgbackrest_mode in ['server', 'local']"


def test_restart_handler_skips_local_mode():
    handlers = {h["name"]: h for h in _tasks("roles/pgbackrest/handlers/main.yml")}
    assert handlers["Restart pgbackrest"]["when"] == "pgbackrest_mode != 'local'"


def test_playbook_picks_local_mode_for_colocated_backup_server():
    plays = _tasks("playbooks/_pgbackrest.yml")
    assert plays[0]["hosts"] == "postgres"
    assert "local" in plays[0]["vars"]["pgbackrest_mode"]
    assert "groups['backup_server']" in plays[0]["vars"]["pgbackrest_mode"]
    assert plays[1]["hosts"] == "backup_server:!postgres"
    assert plays[1]["vars"]["pgbackrest_mode"] == "server"


def test_backup_service_runs_each_enabled_repo():
    service = (ROOT / "roles/pgbackrest/templates/pgbackrest-backup@.service.j2").read_text()
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
    template = env.from_string(service)
    context = {"pgbackrest_stanza": "pg-aio", "pgbackrest_s3_enabled": False}

    local_repo = template.render(**context).splitlines()
    exec_starts = [line for line in local_repo if line.startswith("ExecStart=")]
    assert exec_starts == [
        "ExecStart=/usr/bin/pgbackrest --stanza=pg-aio --repo=1 --type=%i backup"
    ]

    s3_repos = template.render(**(context | {"pgbackrest_s3_enabled": True})).splitlines()
    exec_starts = [line for line in s3_repos if line.startswith("ExecStart=")]
    assert exec_starts == [
        "ExecStart=/usr/bin/pgbackrest --stanza=pg-aio --repo=1 --type=%i backup",
        "ExecStart=/usr/bin/pgbackrest --stanza=pg-aio --repo=2 --type=%i backup",
    ]


def test_haproxy_skipped_for_single_host_profiles():
    site = yaml.safe_load((ROOT / "playbooks/site.yml").read_text())
    entry = next(e for e in site if e["import_playbook"] == "_haproxy.yml")
    assert entry["when"] == "cluster_profile | default('ha') not in ['spof', 'aio']"
    scrape = (ROOT / "roles/monitoring_agents/templates/vmagent-scrape.yml.j2").read_text()
    assert "not in ['spof', 'aio']" in scrape


def test_archive_play_does_not_pin_role_defaults_with_include_vars():
    # include_vars outranks inventory vars, so it would silently pin
    # pgbackrest_initial_backup and the backup_* mapping to their defaults.
    plays = _tasks("playbooks/_pgbackrest.yml")
    for task in plays[2]["tasks"]:
        assert "ansible.builtin.include_vars" not in task
        assert "include_vars" not in task


def test_stanza_is_created_when_s3_repo_lacks_it():
    tasks = {t["name"]: t for t in _tasks("roles/pgbackrest/tasks/_stanza.yml")}
    probe = tasks["Check whether the stanza exists in the S3 repository"]
    assert "--repo=2" in probe["ansible.builtin.command"]["cmd"]
    assert probe["when"] == "pgbackrest_s3_enabled | bool"
    create_when = tasks["Create pgBackRest stanza"]["when"]
    assert "_pgbackrest_stanza_archive_info.stat.exists" in create_when
    assert "_pgbackrest_repo2_info" in create_when
    assert "[1, 3]" in create_when
