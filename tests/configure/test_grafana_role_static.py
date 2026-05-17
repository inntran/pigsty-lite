"""Static checks for Grafana role defaults."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _load_yaml(path: str):
    with (ROOT / path).open() as fh:
        return yaml.safe_load(fh)


def test_grafana_default_port_default_is_not_recursive():
    defaults = _load_yaml("roles/grafana/defaults/main.yml")

    assert defaults["grafana_default_port"] == 3000
