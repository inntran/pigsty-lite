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


def test_clean_removes_only_rebuildable_generated_artifacts():
    makefile = _makefile()

    assert (
        "rm -rf inventory/site.yml group_vars/response.yml .ansible/"
    ) in makefile
    assert "find tests/molecule -path '*/_tmp*' -exec rm -rf {} +" in makefile
    assert "find . -name __pycache__ -type d -exec rm -rf {} +" in makefile
    assert "find . -name '*.pyc' -type f -delete" in makefile
    assert 'podman images --format "{{.Repository}}:{{.Tag}}"' in makefile
    assert "grep -E '^localhost/molecule-base(-common|-data|-infra)?:'" in makefile
    assert "xargs -r podman image rm -f" in makefile
    assert "rm -rf pki/" not in makefile
    assert "collections/" not in makefile.partition("clean:")[2]
    assert "roles.galaxy/" not in makefile.partition("clean:")[2]
    assert "responses/site.rsp.yml" not in makefile.partition("clean:")[2]


def test_all_molecule_verify_plays_honor_task_level_continue_switch():
    verify_files = sorted((ROOT / "tests/molecule").glob("*/molecule/*/verify.yml"))

    assert verify_files
    for path in verify_files:
        with path.open() as fh:
            plays = yaml.safe_load(fh)
        for play in plays:
            assert play["ignore_errors"] == TASK_IGNORE_ERRORS_EXPR, path
