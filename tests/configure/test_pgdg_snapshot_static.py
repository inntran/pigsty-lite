"""The PGDG package snapshot must describe the repos the role actually uses.

docs/reference/pgdg-packages.md answers "can this package come from PGDG?".
That answer is only trustworthy if the snapshot covers the same repositories
`roles/repos` configures. These tests catch the drift case: someone bumps
repos_pgdg_postgres_repo_versions or retargets the EL release without
regenerating the snapshot, leaving a doc that quietly describes the wrong set.

They do not hit the network -- staleness of the package data itself is checked
by `./bin/snapshot_pgdg_packages.py --check`.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = ROOT / "docs/reference/pgdg-packages.md"
SCRIPT = ROOT / "bin/snapshot_pgdg_packages.py"


def _script():
    spec = importlib.util.spec_from_file_location("snapshot_pgdg_packages", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _defaults():
    with (ROOT / "roles/repos/defaults/main.yml").open() as handle:
        return yaml.safe_load(handle)


def test_snapshot_exists_and_is_generated():
    assert SNAPSHOT.exists(), "run ./bin/snapshot_pgdg_packages.py"
    text = SNAPSHOT.read_text()
    assert "bin/snapshot_pgdg_packages.py" in text, "snapshot must name its generator"
    assert "do not edit by hand" in text


def test_snapshot_covers_every_postgres_repo_version_the_role_enables():
    versions = _defaults()["repos_pgdg_postgres_repo_versions"]
    assert versions == _script().POSTGRES_REPO_VERSIONS, (
        "bin/snapshot_pgdg_packages.py POSTGRES_REPO_VERSIONS has drifted from "
        "repos_pgdg_postgres_repo_versions; regenerate the snapshot"
    )

    text = SNAPSHOT.read_text()
    for version in versions:
        assert f"`pgdg{version}`" in text, f"snapshot is missing the pgdg{version} repo"


def test_snapshot_covers_the_extras_repo_the_role_enables():
    extras = _defaults()["repos_pgdg_extras_repo"]
    assert f"`{extras}`" in SNAPSHOT.read_text(), (
        f"snapshot is missing {extras}, which roles/repos enables"
    )


def test_snapshot_targets_the_same_el_release_as_the_role():
    """The role installs an EL-10 release RPM; a snapshot of another EL
    would list packages that never resolve on the target hosts."""
    module = _script()
    rpm_url = _defaults()["repos_pgdg_rpm_url"]

    assert "EL-10" in rpm_url, "unexpected PGDG release RPM URL"
    assert module.EL == "rhel-10", f"snapshot targets {module.EL}, role targets EL-10"
    assert f"**{module.EL}-{module.ARCH}**" in SNAPSHOT.read_text()


def test_snapshot_records_exporter_availability():
    """The exporter package source is an open decision for monitoring_agents;
    the snapshot is where that question gets answered."""
    text = SNAPSHOT.read_text()
    assert "## Monitoring exporters" in text
    # Whichever way it lands, the section must state something about the four
    # exporters monitoring_agents installs.
    assert "node_exporter" in text
    assert "pgbackrest_exporter" in text
