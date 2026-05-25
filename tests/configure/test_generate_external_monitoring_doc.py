"""Tests for generated external monitoring operator snippets."""

from __future__ import annotations

from pathlib import Path

import yaml

from bin._generate_external_monitoring_doc import generate

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    with (FIXTURES / name).open() as fh:
        return yaml.safe_load(fh)


def test_external_pull_doc_contains_scrape_jobs_and_vault_placeholder():
    data = _load("ha.rsp.yml")
    data["monitoring"] = {
        "mode": "external_pull",
        "external_pull": {
            "metrics_port": 9965,
            "auth": {"username": "pigsty"},
            "source_cidrs": ["10.0.0.0/8"],
            "tls": True,
        },
    }
    out = generate(data)
    assert "job_name: pigsty-node" in out
    assert "metrics_path: /metrics/postgres" in out
    assert "pgnode01:9965" in out
    assert "pgnode03:9965" in out
    assert "vault_monitoring_pull_password" in out
    assert "cleartext" not in out


def test_external_push_doc_contains_targets_and_no_secrets():
    data = _load("spof.rsp.yml")
    data["monitoring"] = {
        "mode": "external_push",
        "external_push": {
            "metrics_url": "https://vm.example/api/v1/write",
            "logs_url": "https://vl.example/insert/jsonline",
            "auth": {"username": "pigsty", "bearer": True},
            "tls_skip_verify": False,
        },
    }
    out = generate(data)
    assert "https://vm.example/api/v1/write" in out
    assert "https://vl.example/insert/jsonline" in out
    assert "vault_monitoring_external_password" in out
    assert "vault_monitoring_external_bearer_token" in out
    assert "secret" not in out.lower()
