"""The pinned exporter record must stay internally consistent.

roles/monitoring_agents/vars/exporter_versions.yml pins four exporters that
are fetched as GitHub release artifacts rather than installed from a repo.
Because nothing resolves them at deploy time, a typo in a URL or a checksum
that no longer matches its version would surface only as a failed download --
or, worse, as a silently unverified one.

These tests are offline. Whether a pin is *behind upstream* is a separate
question, answered by `./bin/check_exporter_releases.py --check`.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
RECORD = ROOT / "roles/monitoring_agents/vars/exporter_versions.yml"
SCRIPT = ROOT / "bin/check_exporter_releases.py"
DOC = ROOT / "docs/reference/exporters.md"

EXPECTED = {
    "node_exporter",
    "postgres_exporter",
    "pgbouncer_exporter",
    "pgbackrest_exporter",
}


def _record() -> dict:
    loaded = yaml.safe_load(RECORD.read_text())
    return loaded["monitoring_agents_exporter_releases"]


def test_every_exporter_the_role_installs_is_pinned():
    assert set(_record()) == EXPECTED


def test_each_pin_is_complete():
    for name, entry in _record().items():
        for field in (
            "repo",
            "version",
            "tag",
            "released",
            "license",
            "asset",
            "sha256",
            "url",
        ):
            assert entry.get(field), f"{name}: missing {field}"


def test_checksums_are_full_sha256_digests():
    """A truncated or placeholder digest would still look plausible in a diff."""
    for name, entry in _record().items():
        digest = entry["sha256"]
        assert re.fullmatch(r"[0-9a-f]{64}", digest), f"{name}: {digest!r} is not a sha256"


def test_urls_agree_with_repo_tag_and_asset():
    """The URL is assembled from three other fields; if they disagree, the
    download either 404s or silently fetches the wrong version."""
    for name, entry in _record().items():
        expected = (
            f"https://github.com/{entry['repo']}/releases/download/{entry['tag']}/{entry['asset']}"
        )
        assert entry["url"] == expected, f"{name}: url does not match repo/tag/asset"


def test_asset_names_contain_their_pinned_version():
    for name, entry in _record().items():
        assert entry["version"] in entry["asset"], (
            f"{name}: asset {entry['asset']} does not carry version {entry['version']}"
        )


def test_tags_and_versions_agree():
    for name, entry in _record().items():
        assert entry["tag"].lstrip("v") == entry["version"], f"{name}: tag/version mismatch"


def test_every_exporter_installs_from_a_tarball():
    """One install path for all four. pgbackrest_exporter also publishes an
    RPM, and pinning it would reintroduce a second code path in the role."""
    for name, entry in _record().items():
        assert entry["asset"].endswith(".tar.gz"), (
            f"{name}: {entry['asset']} is not a tarball; the role unpacks all four"
        )


def test_licenses_are_compatible_with_this_project():
    """pigsty-lite is Apache-2.0; a copyleft exporter would be a licensing
    decision, not a version bump."""
    for name, entry in _record().items():
        assert entry["license"] in {"Apache-2.0", "MIT", "BSD-3-Clause"}, (
            f"{name}: unexpected license {entry['license']}"
        )


def test_the_updater_covers_exactly_the_pinned_exporters():
    """If the script and the record drift apart, `--update` silently stops
    maintaining one of the pins."""
    keys = set(re.findall(r'^\s*"([a-z_]+)",$', SCRIPT.read_text(), re.M))
    assert keys >= EXPECTED, f"bin/check_exporter_releases.py is missing {EXPECTED - keys}"


def test_doc_points_at_the_record_and_the_updater():
    text = DOC.read_text()
    assert "exporter_versions.yml" in text
    assert "check_exporter_releases.py" in text
