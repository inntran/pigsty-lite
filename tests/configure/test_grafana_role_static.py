"""Static checks for Grafana role defaults."""

from __future__ import annotations

import configparser
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _load_yaml(path: str):
    with (ROOT / path).open() as fh:
        return yaml.safe_load(fh)


def _load_ini(path: str):
    parser = configparser.ConfigParser()
    parser.read(ROOT / path)
    return parser


def test_grafana_default_port_default_is_not_recursive():
    defaults = _load_yaml("roles/grafana/defaults/main.yml")

    assert defaults["grafana_default_port"] == 3000


def test_grafana_role_owns_install_and_does_not_depend_on_upstream_role():
    """The grafana role must not include grafana.grafana.grafana."""
    for path in (ROOT / "roles/grafana/tasks").glob("*.yml"):
        with path.open() as fh:
            content = fh.read()
        assert "grafana.grafana.grafana" not in content, path


def test_grafana_version_pin_default_tracks_12_line():
    defaults = _load_yaml("roles/grafana/defaults/main.yml")

    assert defaults["grafana_version_pin"] == "12.*"


def test_grafana_install_task_uses_static_repo_file_and_dnf_pin():
    install = _load_yaml("roles/grafana/tasks/_install.yml")

    copy_tasks = [t for t in install if "ansible.builtin.copy" in t]
    assert copy_tasks, "expected ansible.builtin.copy task for the yum repo"
    copy = copy_tasks[0]["ansible.builtin.copy"]
    assert copy["src"] == "grafana.repo"
    assert copy["dest"] == "/etc/yum.repos.d/grafana.repo"

    dnf_tasks = [t for t in install if "ansible.builtin.dnf" in t]
    assert dnf_tasks, "expected ansible.builtin.dnf task installing grafana"
    dnf = dnf_tasks[0]["ansible.builtin.dnf"]
    assert dnf["name"] == "grafana-{{ grafana_version_pin }}"
    assert dnf["state"] == "present"


def test_grafana_ini_defaults_match_loopback_behind_nginx():
    defaults = _load_yaml("roles/grafana/defaults/main.yml")
    ini = defaults["grafana_ini"]

    assert ini["server"]["http_addr"] == "{{ grafana_listen_address }}"
    assert ini["server"]["http_port"] == "{{ grafana_default_port }}"
    assert ini["database"]["type"] == "sqlite3"
    assert ini["security"]["admin_user"] == "{{ grafana_admin_user }}"


def test_grafana_configure_task_renders_grafana_ini_and_notifies_restart():
    configure = _load_yaml("roles/grafana/tasks/_configure.yml")

    template_tasks = [
        t for t in configure if t.get("ansible.builtin.template", {}).get("src") == "grafana.ini.j2"
    ]
    assert template_tasks
    template = template_tasks[0]
    assert template["ansible.builtin.template"]["dest"].endswith("/grafana.ini")
    assert template["notify"] == "Restart grafana"


def test_grafana_configure_task_enables_and_starts_grafana_server():
    configure = _load_yaml("roles/grafana/tasks/_configure.yml")

    systemd_tasks = [t for t in configure if "ansible.builtin.systemd" in t]
    assert systemd_tasks
    systemd = systemd_tasks[0]["ansible.builtin.systemd"]
    assert systemd["name"] == "{{ grafana_service_name }}"
    assert systemd["enabled"] is True
    assert systemd["state"] == "started"


def test_grafana_role_no_longer_references_grafana_version_variable():
    """The new role uses grafana_version_pin only; grafana_version is removed."""
    defaults = _load_yaml("roles/grafana/defaults/main.yml")
    assert "grafana_version" not in defaults


def test_monitor_group_vars_does_not_pin_legacy_grafana_version():
    monitor_vars = _load_yaml("group_vars/monitor.yml") or {}
    assert "grafana_version" not in monitor_vars


def test_requirements_drops_grafana_grafana_collection():
    requirements = _load_yaml("requirements.yml")
    collection_names = {c["name"] for c in requirements.get("collections", [])}
    assert "grafana.grafana" not in collection_names
    assert "community.grafana" in collection_names


def test_ansible_collection_resolution_uses_repo_local_collections_only():
    config = _load_ini("ansible.cfg")

    assert config["defaults"]["collections_path"] == "./collections"
    assert config["defaults"]["collections_scan_sys_path"] == "False"
