"""monitoring_agents must never put a credential on a command line.

Covers both paths that handle secrets: the external_push remote_write
credentials, and the external_pull metrics-frontend htpasswd hash.

The upstream victoriametrics.cluster roles interpolate every entry of
vmagent_service_args / vlagent_service_args into the ExecStart line of a
world-readable (0644) systemd unit. An inline password would therefore be
readable from the unit file and from /proc/<pid>/cmdline by any local user.
Both agents support -remoteWrite.basicAuth.passwordFile and
-remoteWrite.bearerTokenFile, which read the value from disk instead.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
TASKS = ROOT / "roles/monitoring_agents/tasks/main.yml"
SECRETS_TASKS = ROOT / "roles/monitoring_agents/tasks/_external_secrets.yml"
DEFAULTS = ROOT / "roles/monitoring_agents/defaults/main.yml"

# Vault variables holding the actual secret values.
SECRET_VARS = (
    "vault_monitoring_external_password",
    "vault_monitoring_external_bearer_token",
)


def _walk(tasks):
    for task in tasks or []:
        for key in ("block", "rescue", "always"):
            if key in task:
                yield from _walk(task[key])
        yield task


def test_service_args_never_reference_secret_values():
    raw = TASKS.read_text()
    for secret in SECRET_VARS:
        assert secret not in raw, (
            f"{TASKS.name}: {secret} is interpolated into agent service args; "
            "pass a *File flag instead so the value stays out of ExecStart"
        )


def test_service_args_use_file_based_auth_flags():
    raw = TASKS.read_text()
    for flag in ("remoteWrite.basicAuth.passwordFile", "remoteWrite.bearerTokenFile"):
        assert flag in raw, f"{TASKS.name}: expected {flag}"
    # The inline forms must not come back.
    for flag in ("'remoteWrite.basicAuth.password'", "'remoteWrite.bearerToken'"):
        assert flag not in raw, f"{TASKS.name}: {flag} passes the secret inline"


def test_each_agent_reads_its_own_credential_file():
    """vmagent and vlagent run as different users, so they cannot share a
    file that is group-readable by only one of them."""
    raw = TASKS.read_text()
    assert "monitoring_agents_vmagent_password_file" in raw
    assert "monitoring_agents_vlagent_password_file" in raw
    assert "monitoring_agents_vmagent_bearer_token_file" in raw
    assert "monitoring_agents_vlagent_bearer_token_file" in raw


def test_credential_files_are_written_with_no_log_and_tight_modes():
    tasks = yaml.safe_load(SECRETS_TASKS.read_text())
    writes = [
        task
        for task in _walk(tasks)
        if "ansible.builtin.copy" in task and "content" in task["ansible.builtin.copy"]
    ]
    assert writes, "expected credential files to be written with copy:"

    for task in writes:
        name = task.get("name")
        assert task.get("no_log") is True, f"{name}: must set no_log"
        mode = task["ansible.builtin.copy"]["mode"]
        assert mode == "0640", f"{name}: mode {mode} should be 0640"
        assert task["ansible.builtin.copy"]["owner"] == "root", f"{name}: must be root-owned"


def test_secret_files_live_outside_world_readable_config():
    defaults = yaml.safe_load(DEFAULTS.read_text())
    for key in (
        "monitoring_agents_vmagent_password_file",
        "monitoring_agents_vlagent_password_file",
        "monitoring_agents_vmagent_bearer_token_file",
        "monitoring_agents_vlagent_bearer_token_file",
    ):
        assert defaults[key].startswith("{{ monitoring_agents_secrets_dir }}"), key


def test_stale_credential_files_are_removed_when_auth_is_disabled():
    """Turning off basic auth or bearer auth must not leave a readable
    credential behind on the host."""
    tasks = yaml.safe_load(SECRETS_TASKS.read_text())
    absent = [
        task
        for task in _walk(tasks)
        if task.get("ansible.builtin.file", {}).get("state") == "absent"
    ]
    assert absent, "expected a task removing unconfigured credential files"


NGINX_METRICS_TASKS = ROOT / "roles/monitoring_agents/tasks/_nginx_metrics.yml"


def test_openssl_cli_is_installed_not_assumed():
    """openssl-libs ships without the CLI on RHEL 10, so `openssl passwd`
    fails on a minimal host unless the binary is installed explicitly."""
    defaults = yaml.safe_load(DEFAULTS.read_text())
    packages = defaults["monitoring_agents_nginx_metrics_packages"]

    assert any("openssl" in str(pkg) for pkg in packages), (
        "the metrics frontend must install the openssl CLI"
    )
    assert defaults["monitoring_agents_openssl_package"] == "openssl"


def test_htpasswd_password_is_passed_on_stdin_not_argv():
    """argv is world-readable via /proc/<pid>/cmdline while the command runs."""
    tasks = yaml.safe_load(NGINX_METRICS_TASKS.read_text())
    hash_tasks = [
        task
        for task in _walk(tasks)
        if "passwd" in str(task.get("ansible.builtin.command", {}).get("cmd", ""))
    ]
    assert hash_tasks, "expected a password-hashing task"

    for task in hash_tasks:
        command = task["ansible.builtin.command"]
        assert "-stdin" in command["cmd"], "openssl passwd must read the password from stdin"
        assert "stdin" in command, "the password must be supplied via the stdin parameter"
        assert "monitoring_agents_pull_password" not in command["cmd"], (
            "the password must not appear in the command arguments"
        )
        assert task.get("no_log") is True


def test_openssl_availability_is_checked_outside_a_no_log_task():
    """A no_log failure is censored, so a missing binary must surface from a
    task whose output the operator can actually read."""
    tasks = list(_walk(yaml.safe_load(NGINX_METRICS_TASKS.read_text())))
    probes = [
        task
        for task in tasks
        if "openssl version" in str(task.get("ansible.builtin.command", {}).get("cmd", ""))
    ]
    assert probes, "expected an openssl availability probe"
    for task in probes:
        assert not task.get("no_log"), "the probe must not be no_log, or it cannot be diagnosed"
