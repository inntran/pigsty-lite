"""Tests for the configure CLI."""

from __future__ import annotations

import argparse
import builtins
import sys
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


class _TtyStdin:
    def isatty(self) -> bool:
        return True


def _load_configure_module():
    loader = SourceFileLoader("configure_cli", str(ROOT / "configure"))
    spec = spec_from_loader("configure_cli", loader)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    sys.modules["configure_cli"] = module
    spec.loader.exec_module(module)
    return module


def test_interactive_response_file_starts_with_document_marker(monkeypatch, tmp_path):
    module = _load_configure_module()
    (tmp_path / "responses").mkdir()
    (tmp_path / "responses" / "spof.rsp.yml.example").write_text(
        (ROOT / "responses" / "spof.rsp.yml.example").read_text()
    )

    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "RESPONSE_FILE_PATH", tmp_path / "responses" / "site.rsp.yml")
    monkeypatch.setattr(module, "INVENTORY_PATH", tmp_path / "inventory" / "site.yml")
    monkeypatch.setattr(
        module,
        "RESPONSE_VARS_PATH",
        tmp_path / "inventory" / "group_vars" / "all" / "response.yml",
    )
    monkeypatch.setattr(sys, "stdin", _TtyStdin())
    answers = iter(["pg-dev", "example.internal", "dba", "norm", "pgbouncer", "n", ""])
    monkeypatch.setattr(builtins, "input", lambda _prompt: next(answers))

    rc = module.cmd_interactive(argparse.Namespace(profile="spof", no_vault=True))

    assert rc == 0
    raw = (tmp_path / "responses" / "site.rsp.yml").read_text()
    assert raw.startswith("---\n")
    assert yaml.safe_load(raw)["profile"] == "spof"


def test_interactive_does_not_generate_derived_files(monkeypatch, tmp_path):
    """The wizard writes only the response file; inventory/response.yml come from `make regen`."""
    module = _load_configure_module()
    (tmp_path / "responses").mkdir()
    (tmp_path / "responses" / "spof.rsp.yml.example").write_text(
        (ROOT / "responses" / "spof.rsp.yml.example").read_text()
    )

    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "RESPONSE_FILE_PATH", tmp_path / "responses" / "site.rsp.yml")
    monkeypatch.setattr(module, "INVENTORY_PATH", tmp_path / "inventory" / "site.yml")
    monkeypatch.setattr(
        module,
        "RESPONSE_VARS_PATH",
        tmp_path / "inventory" / "group_vars" / "all" / "response.yml",
    )
    monkeypatch.setattr(sys, "stdin", _TtyStdin())
    answers = iter(["pg-dev", "example.internal", "dba", "norm", "pgbouncer", "n", ""])
    monkeypatch.setattr(builtins, "input", lambda _prompt: next(answers))

    rc = module.cmd_interactive(argparse.Namespace(profile="spof", no_vault=True))

    assert rc == 0
    assert not (tmp_path / "inventory" / "site.yml").exists()
    assert not (tmp_path / "inventory" / "group_vars" / "all" / "response.yml").exists()


def test_interactive_prompts_for_remote_user(monkeypatch, tmp_path):
    """The 4th prompt sets access.ansible_user in the written response file."""
    module = _load_configure_module()
    (tmp_path / "responses").mkdir()
    (tmp_path / "responses" / "spof.rsp.yml.example").write_text(
        (ROOT / "responses" / "spof.rsp.yml.example").read_text()
    )

    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "RESPONSE_FILE_PATH", tmp_path / "responses" / "site.rsp.yml")
    monkeypatch.setattr(module, "INVENTORY_PATH", tmp_path / "inventory" / "site.yml")
    monkeypatch.setattr(
        module,
        "RESPONSE_VARS_PATH",
        tmp_path / "inventory" / "group_vars" / "all" / "response.yml",
    )
    monkeypatch.setattr(sys, "stdin", _TtyStdin())
    answers = iter(["pg-dev", "example.internal", "ansible-svc", "norm", "pgbouncer", "n", ""])
    monkeypatch.setattr(builtins, "input", lambda _prompt: next(answers))

    rc = module.cmd_interactive(argparse.Namespace(profile="spof", no_vault=True))

    assert rc == 0
    data = yaml.safe_load((tmp_path / "responses" / "site.rsp.yml").read_text())
    assert data["access"]["ansible_user"] == "ansible-svc"


def test_interactive_remote_user_defaults_to_dba(monkeypatch, tmp_path):
    """Empty input on the remote-user prompt keeps the dba default."""
    module = _load_configure_module()
    (tmp_path / "responses").mkdir()
    (tmp_path / "responses" / "spof.rsp.yml.example").write_text(
        (ROOT / "responses" / "spof.rsp.yml.example").read_text()
    )

    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "RESPONSE_FILE_PATH", tmp_path / "responses" / "site.rsp.yml")
    monkeypatch.setattr(module, "INVENTORY_PATH", tmp_path / "inventory" / "site.yml")
    monkeypatch.setattr(
        module,
        "RESPONSE_VARS_PATH",
        tmp_path / "inventory" / "group_vars" / "all" / "response.yml",
    )
    monkeypatch.setattr(sys, "stdin", _TtyStdin())
    answers = iter(["pg-dev", "example.internal", "", "norm", "pgbouncer", "n", ""])
    monkeypatch.setattr(builtins, "input", lambda _prompt: next(answers))

    rc = module.cmd_interactive(argparse.Namespace(profile="spof", no_vault=True))

    assert rc == 0
    data = yaml.safe_load((tmp_path / "responses" / "site.rsp.yml").read_text())
    assert data["access"]["ansible_user"] == "dba"


def test_interactive_prompts_for_db_routing(monkeypatch, tmp_path):
    """The db_routing prompts write haproxy + vip-manager settings."""
    module = _load_configure_module()
    (tmp_path / "responses").mkdir()
    (tmp_path / "responses" / "ha.rsp.yml.example").write_text(
        (ROOT / "responses" / "ha.rsp.yml.example").read_text()
    )

    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "RESPONSE_FILE_PATH", tmp_path / "responses" / "site.rsp.yml")
    monkeypatch.setattr(module, "INVENTORY_PATH", tmp_path / "inventory" / "site.yml")
    monkeypatch.setattr(
        module,
        "RESPONSE_VARS_PATH",
        tmp_path / "inventory" / "group_vars" / "all" / "response.yml",
    )
    monkeypatch.setattr(sys, "stdin", _TtyStdin())
    answers = iter(
        [
            "pg-dev",
            "example.internal",
            "dba",
            "tight",
            "postgres",
            "y",
            "10.20.30.20/24",
            "eth0",
            "",
        ]
    )
    monkeypatch.setattr(builtins, "input", lambda _prompt: next(answers))

    rc = module.cmd_interactive(argparse.Namespace(profile="ha", no_vault=True))

    assert rc == 0
    routing = yaml.safe_load((tmp_path / "responses" / "site.rsp.yml").read_text())["db_routing"]
    assert routing["haproxy"] == {"rto_profile": "tight", "backend_target": "postgres"}
    assert routing["vip_manager"] == {
        "enabled": True,
        "vip_cidr": "10.20.30.20/24",
        "interface": "eth0",
    }
