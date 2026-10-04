#!/usr/bin/env python3
"""Fail on unquoted Jinja in YAML files.

An unquoted plain scalar containing `{{` or `{%` is fragile: YAML may parse a
leading `{{` as a flow mapping, and `{%` is not valid at the start of a value.
Wrap such values in quotes. Quoted and block (| and >) scalars are never reported.

    ./bin/check_yaml_jinja.py                # every tracked YAML file
    ./bin/check_yaml_jinja.py a.yml b.yml    # only the given files

Without arguments, files under .github/ and any generated/ directory are skipped.
Findings are printed one per line; the exit status is 1 if there are any.
"""

import subprocess
import sys
from pathlib import Path

import yaml


def check_file(path: Path) -> list[str]:
    """Return one finding string per unquoted Jinja occurrence in `path`."""
    text = path.read_text()
    try:
        tokens = list(yaml.scan(text))
    except yaml.YAMLError as exc:
        lines = str(exc).strip().splitlines()
        detail = lines[0] if lines else type(exc).__name__
        return [f"{path}: YAML error: {detail}"]

    findings: list[str] = []
    for token, following in zip(tokens, [*tokens[1:], None], strict=True):
        line = token.start_mark.line + 1
        if isinstance(token, yaml.ScalarToken) and token.plain:
            if "{{" in token.value or "{%" in token.value:
                findings.append(f"{path}:{line}: unquoted Jinja; wrap the value in quotes")
        elif (
            isinstance(token, yaml.FlowMappingStartToken)
            and isinstance(following, yaml.FlowMappingStartToken)
            and following.start_mark.index == token.end_mark.index
        ):
            findings.append(
                f"{path}:{line}: unquoted {{{{ parsed as a YAML mapping; wrap the value in quotes"
            )
    return findings


def _tracked_yaml_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "*.yml", "*.yaml"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return [
        Path(name)
        for name in out.splitlines()
        if name
        and not name.startswith(".github/")
        and "/generated/" not in name
        and Path(name).exists()  # tracked but deleted in the working tree
    ]


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    paths = [Path(a) for a in args] if args else _tracked_yaml_files()
    findings = [finding for path in paths for finding in check_file(path)]
    for finding in findings:
        print(finding)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
