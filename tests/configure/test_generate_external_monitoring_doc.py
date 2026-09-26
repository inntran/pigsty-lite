"""Tests for generated external monitoring operator snippets."""

from __future__ import annotations

from pathlib import Path

import yaml

from bin._generate_external_monitoring_doc import generate

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    with (FIXTURES / name).open() as fh:
        return yaml.safe_load(fh)


def test_external_pull_doc_uses_exporter_exporter_bearer_auth():
    data = _load("ha.rsp.yml")
    data["monitoring"] = {
        "mode": "external_pull",
        "external_pull": {
            "metrics_port": 9999,
            "source_cidrs": ["10.0.0.0/8"],
            "tls": True,
        },
    }
    out = generate(data)
    config = yaml.safe_load(out.split("```yaml\n", 1)[1].split("```", 1)[0])
    jobs = config["scrape_configs"]
    assert [job["job_name"] for job in jobs] == [
        "pigsty-node",
        "pigsty-postgres",
        "pigsty-pgbouncer",
        "pigsty-pgbackrest",
    ]
    assert all(job["metrics_path"] == "/proxy" for job in jobs)
    assert [job["params"]["module"] for job in jobs] == [
        ["node"],
        ["postgres"],
        ["pgbouncer"],
        ["pgbackrest"],
    ]
    assert jobs[0]["static_configs"][0]["targets"] == [
        "pgmon01:9999",
        "pgnode01:9999",
        "pgnode02:9999",
        "pgnode03:9999",
    ]
    assert jobs[1]["static_configs"][0]["targets"] == [
        "pgnode01:9999",
        "pgnode02:9999",
        "pgnode03:9999",
    ]
    assert all(
        job["authorization"]
        == {
            "type": "Bearer",
            "credentials": "<from vault: vault_monitoring_pull_token>",
        }
        for job in jobs
    )
    assert all("basic_auth" not in job for job in jobs)
    assert all(
        job["tls_config"] == {"ca_file": "<pigsty-lite CA: pki/ca/ca.crt on the control node>"}
        for job in jobs
    )
    assert "vault_monitoring_pull_token" in out
    assert "cleartext" not in out


def test_external_pull_doc_omits_tls_config_when_tls_is_disabled():
    data = _load("ha.rsp.yml")
    data["monitoring"] = {
        "mode": "external_pull",
        "external_pull": {
            "metrics_port": 9999,
            "source_cidrs": ["10.0.0.0/8"],
            "tls": False,
        },
    }

    out = generate(data)
    config = yaml.safe_load(out.split("```yaml\n", 1)[1].split("```", 1)[0])

    assert all("tls_config" not in job for job in config["scrape_configs"])
    assert "exporter_exporter" in out
    assert "intentionally not rendered" in out


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
