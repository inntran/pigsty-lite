"""Patroni REST calls must verify TLS against the pigsty-lite CA.

The project stands up an internal CA (roles/ca) and distributes the bundle
to every host (roles/certs). These checks keep the operational REST calls
-- including the ones that trigger a failover -- actually using it.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]

# Task files whose uri: calls talk to Patroni REST on a provisioned host.
VERIFIED_TASK_FILES = (
    "roles/cluster_ops/tasks/find_leader.yml",
    "roles/cluster_ops/tasks/assert_healthy.yml",
    "roles/cluster_ops/tasks/wait_member_converged.yml",
    "roles/patroni/tasks/_service.yml",
    "roles/provision/tasks/main.yml",
)


def _load_yaml(path: str):
    with (ROOT / path).open() as fh:
        return yaml.safe_load(fh)


def _uri_calls(tasks) -> list[dict]:
    found = []
    for task in tasks or []:
        for key in ("block", "rescue", "always"):
            if key in task:
                found.extend(_uri_calls(task[key]))
        if "ansible.builtin.uri" in task:
            found.append(task["ansible.builtin.uri"])
    return found


def test_rest_defaults_enable_verification_and_name_the_ca():
    cases = (
        ("roles/cluster_ops/defaults/main.yml", "cluster_ops_rest_validate_certs", "cluster_ops_rest_ca_file"),
        ("roles/provision/defaults/main.yml", "provision_patroni_rest_validate_certs", "provision_patroni_rest_ca_file"),
    )
    for path, verify_key, ca_key in cases:
        defaults = _load_yaml(path)
        assert defaults[verify_key] is True, f"{path}: {verify_key} must default to true"
        assert "ca.crt" in defaults[ca_key], f"{path}: {ca_key} must point at the CA bundle"

    patroni_defaults = _load_yaml("roles/patroni/defaults/main.yml")
    assert patroni_defaults["patroni_rest_validate_certs"] is True


def test_patroni_rest_calls_never_hardcode_validate_certs_false():
    for path in VERIFIED_TASK_FILES:
        for call in _uri_calls(_load_yaml(path)):
            assert call.get("validate_certs") is not False, (
                f"{path}: uri call to {call.get('url')} disables TLS verification"
            )


def test_verified_rest_calls_supply_a_ca_path():
    """validate_certs alone is not enough: the CA is not in the system trust
    store, so every verified call must also pass ca_path."""
    for path in VERIFIED_TASK_FILES:
        for call in _uri_calls(_load_yaml(path)):
            url = str(call.get("url", ""))
            if "https" not in url:
                continue
            assert "ca_path" in call, f"{path}: uri call to {url} is missing ca_path"
