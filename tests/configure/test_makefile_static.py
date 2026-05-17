"""Static checks for developer Makefile entry points."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
TASK_IGNORE_ERRORS_EXPR = (
    "{{ lookup('ansible.builtin.env', 'MOLECULE_TASK_IGNORE_ERRORS') | default('', true) | bool }}"
)


def _makefile() -> str:
    return (ROOT / "Makefile").read_text()


def test_test_role_documents_local_fail_fast_switch():
    makefile = _makefile()

    assert "make test-role ROLE=<name> FAIL_FAST=0" in makefile


def test_test_role_fail_fast_zero_enables_task_level_continue_mode():
    makefile = _makefile()

    assert 'if [ "$(FAIL_FAST)" = "0" ]' in makefile
    assert "MOLECULE_TASK_IGNORE_ERRORS=1" in makefile
    assert "MOLECULE_GLOB='molecule/*/molecule.yml' molecule test --all" in makefile
    assert "ignored=[1-9][0-9]*" in makefile
    assert "exit $$status" in makefile


def test_all_molecule_verify_plays_honor_task_level_continue_switch():
    verify_files = sorted((ROOT / "tests/molecule").glob("*/molecule/*/verify.yml"))

    assert verify_files
    for path in verify_files:
        with path.open() as fh:
            plays = yaml.safe_load(fh)
        for play in plays:
            assert play["ignore_errors"] == TASK_IGNORE_ERRORS_EXPR, path
