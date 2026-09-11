"""external_push credentials must never be passed as agent command-line args.

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
