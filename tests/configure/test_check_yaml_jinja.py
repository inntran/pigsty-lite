"""Tests for bin/check_yaml_jinja.py."""

from pathlib import Path

import pytest

from bin import check_yaml_jinja

REPO_ROOT = Path(__file__).resolve().parents[2]


def _check(tmp_path: Path, text: str) -> list[str]:
    path = tmp_path / "f.yml"
    path.write_text(text)
    return check_yaml_jinja.check_file(path)


def test_quoted_scalars_are_clean(tmp_path):
    assert _check(tmp_path, "a: \"{{ x }}\"\nb: '{{ x }}'\n") == []


def test_block_scalar_is_clean(tmp_path):
    assert _check(tmp_path, "msg: >-\n  hello {{ x }}\n") == []


def test_unquoted_expression_reported(tmp_path):
    findings = _check(tmp_path, "name: Install {{ x }}\n")
    assert len(findings) == 1
    assert ":1: unquoted Jinja" in findings[0]


def test_unquoted_statement_reported(tmp_path):
    findings = _check(tmp_path, "name: Run {% if x %}y{% endif %}\n")
    assert len(findings) == 1
    assert "unquoted Jinja" in findings[0]


def test_leading_double_brace_parsed_as_mapping(tmp_path):
    findings = _check(tmp_path, "msg: {{ x }}\n")
    assert any("parsed as a YAML mapping" in f for f in findings)


def test_spaced_braces_are_clean(tmp_path):
    assert _check(tmp_path, "msg: { { a: 1 } }\n") == []


def test_leading_statement_is_yaml_error(tmp_path):
    findings = _check(tmp_path, "msg: {% if x %}\n")
    assert len(findings) == 1
    assert "YAML error" in findings[0]


def test_multi_document_line_number(tmp_path):
    findings = _check(tmp_path, "a: 1\n---\nb: 2\nname: Install {{ x }}\n")
    assert len(findings) == 1
    assert ":4: unquoted Jinja" in findings[0]


def test_main_on_repo_is_clean(monkeypatch, capsys):
    monkeypatch.chdir(REPO_ROOT)
    assert check_yaml_jinja.main([]) == 0
    assert capsys.readouterr().out == ""


def test_main_reports_findings(tmp_path, capsys):
    path = tmp_path / "bad.yml"
    path.write_text("name: Install {{ x }}\n")
    assert check_yaml_jinja.main([str(path)]) == 1
    assert "unquoted Jinja" in capsys.readouterr().out


@pytest.mark.parametrize("text", ["a: 1\n", ""])
def test_clean_files(tmp_path, text):
    assert _check(tmp_path, text) == []
