"""pg_hba.conf has exactly one writer: Patroni, on every member. A second
writer on the leader alone is how operator rules went missing after a
failover."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

THIS_FILE = Path(__file__).resolve()


def test_no_role_writes_pg_hba_with_the_postgresql_pg_hba_module():
    offenders = [
        path for path in (ROOT / "roles").rglob("*.yml") if "postgresql_pg_hba" in path.read_text()
    ]

    assert not offenders, offenders


def test_provision_has_no_hba_tasks():
    assert not (ROOT / "roles/provision/tasks/_hba.yml").exists()

    offenders = [
        path
        for pattern in ("*.yml", "*.j2")
        for path in (ROOT / "roles/provision").rglob(pattern)
        if "pg_hba" in path.read_text()
    ]

    assert not offenders, offenders


def test_patroni_always_renders_postgresql_pg_hba():
    contents = (ROOT / "roles/patroni/templates/patroni.yml.j2").read_text()

    assert "  pg_hba:" in contents
    assert "patroni_pg_hba_managed" not in contents


def test_the_managed_toggle_is_gone():
    patterns = ["*.yml", "*.j2", "*.py"]
    dirs = [ROOT / "roles", ROOT / "playbooks", ROOT / "inventory", ROOT / "tests"]

    offenders = []
    for base in dirs:
        if not base.exists():
            continue
        for pattern in patterns:
            for path in base.rglob(pattern):
                if path.resolve() == THIS_FILE:
                    continue
                if not path.is_file():
                    continue
                contents = path.read_text()
                if "patroni_pg_hba_managed" in contents or "provision_pg_hba" in contents:
                    offenders.append(path)

    assert not offenders, offenders
