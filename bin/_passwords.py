"""Orchestration for vault-backed password lifecycle.

Pure functions over a vault dict {key: value}. No file I/O; callers
combine this with bin._vault for persistence. Separating the policy
(which keys exist, machine-vs-human) from the I/O makes both pieces
trivially testable.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from bin._vault import generate_password


@dataclass(frozen=True)
class Secret:
    key: str          # Variable name in vault.yml (e.g. vault_patroni_superuser_password)
    prompt: str       # Human-facing prompt label for interactive secrets
    length: int = 24  # Length for auto-generated secrets


MACHINE_SECRETS: tuple[Secret, ...] = (
    Secret("vault_patroni_superuser_password", "patroni superuser"),
    Secret("vault_patroni_replication_password", "patroni replication user"),
    Secret("vault_patroni_rewind_password", "patroni rewind user"),
    Secret("vault_haproxy_stats_password", "haproxy stats"),
)

HUMAN_SECRETS: tuple[Secret, ...] = (
    Secret("vault_grafana_admin_password", "Grafana admin web UI"),
)

ALL_SECRETS: tuple[Secret, ...] = MACHINE_SECRETS + HUMAN_SECRETS


def ensure_machine_secrets(vault: dict[str, str]) -> dict[str, str]:
    """Fill in any missing machine-secret keys with generated values."""
    result = dict(vault)
    for secret in MACHINE_SECRETS:
        if secret.key not in result or not result[secret.key]:
            result[secret.key] = generate_password(secret.length)
    return result


def ensure_human_secrets(
    vault: dict[str, str],
    prompter: Callable[[str], str],
) -> dict[str, str]:
    """Prompt for any missing human-secret keys; pass through existing ones."""
    result = dict(vault)
    for secret in HUMAN_SECRETS:
        if secret.key not in result or not result[secret.key]:
            result[secret.key] = prompter(secret.prompt)
    return result


def rotate(vault: dict[str, str], key: str) -> dict[str, str]:
    """Force-regenerate one secret. Raises ValueError for unknown keys."""
    secret = next((s for s in ALL_SECRETS if s.key == key), None)
    if secret is None:
        raise ValueError(f"unknown secret key: {key}")
    result = dict(vault)
    result[key] = generate_password(secret.length)
    return result
