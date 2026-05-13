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
    (tmp_path / "responses" / "single.rsp.yml.example").write_text(
        (ROOT / "responses" / "single.rsp.yml.example").read_text()
    )

    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "RESPONSE_FILE_PATH", tmp_path / "responses" / "site.rsp.yml")
    monkeypatch.setattr(module, "INVENTORY_PATH", tmp_path / "inventory" / "site.yml")
    monkeypatch.setattr(module, "RESPONSE_VARS_PATH", tmp_path / "group_vars" / "response.yml")
    monkeypatch.setattr(sys, "stdin", _TtyStdin())
    answers = iter(["pg-dev", "example.internal"])
    monkeypatch.setattr(builtins, "input", lambda _prompt: next(answers))

    rc = module.cmd_interactive(argparse.Namespace(profile="single"))

    assert rc == 0
    raw = (tmp_path / "responses" / "site.rsp.yml").read_text()
    assert raw.startswith("---\n")
    assert yaml.safe_load(raw)["profile"] == "single"
