#!/usr/bin/env python3
"""Snapshot the package inventory of the PGDG repositories this project uses.

`roles/repos` wires up PGDG as the highest-priority repo (priority 10) and
enables the versioned repo for `postgres_version` plus `pgdg-rhel10-extras`.
Deciding whether a package can come from PGDG -- rather than a vendor repo,
EPEL, or an upstream tarball -- currently means querying the repo by hand.

This writes that inventory to docs/reference/pgdg-packages.md so the answer is
in the tree, reviewable in diffs, and available without network access.

Usage:
    ./bin/snapshot_pgdg_packages.py            # refresh the snapshot
    ./bin/snapshot_pgdg_packages.py --check    # fail if stale (CI)

The snapshot is a point-in-time record, not a lockfile: nothing installs from
it. Refresh it when you need current data.
"""

from __future__ import annotations

import argparse
import bz2
import gzip
import lzma
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs/reference/pgdg-packages.md"

BASE = "https://download.postgresql.org/pub/repos/yum"
ARCH = "x86_64"
EL = "rhel-10"

# Mirrors the repo ids the pgdg-redhat-repo RPM defines and roles/repos enables.
# Keep `postgres_repo_versions` in sync with repos_pgdg_postgres_repo_versions.
POSTGRES_REPO_VERSIONS = [14, 15, 16, 17, 18]

REPO_MD = "repodata/repomd.xml"
NS = {
    "repo": "http://linux.duke.edu/metadata/repo",
    "common": "http://linux.duke.edu/metadata/common",
}

TIMEOUT = 120


@dataclass(frozen=True)
class Package:
    name: str
    version: str
    release: str
    arch: str
    summary: str
    license: str
    size: int

    @property
    def evr(self) -> str:
        return f"{self.version}-{self.release}"


@dataclass(frozen=True)
class Repo:
    repo_id: str
    url: str
    note: str


def repos() -> list[Repo]:
    out = [
        Repo(
            "pgdg-common",
            f"{BASE}/common/redhat/{EL}-{ARCH}",
            "Version-independent tools. Enabled by the PGDG release RPM.",
        )
    ]
    for version in POSTGRES_REPO_VERSIONS:
        out.append(
            Repo(
                f"pgdg{version}",
                f"{BASE}/{version}/redhat/{EL}-{ARCH}",
                f"PostgreSQL {version} server and extensions.",
            )
        )
    out.append(
        Repo(
            "pgdg-rhel10-extras",
            f"{BASE}/extras/redhat/{EL}-{ARCH}",
            "Dependencies for PGDG packages absent from the vendor repos. "
            "Ships disabled; roles/repos enables it.",
        )
    )
    return out


def _fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "pigsty-lite-snapshot"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310
        return response.read()


def _decompress(payload: bytes, href: str) -> bytes:
    if href.endswith(".zst"):
        try:
            import zstandard
        except ImportError:
            import subprocess

            return subprocess.run(  # noqa: S603
                ["zstd", "-dc"],  # noqa: S607
                input=payload,
                capture_output=True,
                check=True,
            ).stdout
        return zstandard.ZstdDecompressor().stream_reader(payload).read()
    if href.endswith(".xz"):
        return lzma.decompress(payload)
    if href.endswith(".bz2"):
        return bz2.decompress(payload)
    if href.endswith(".gz"):
        return gzip.decompress(payload)
    return payload


def primary_href(repo_url: str) -> str:
    root = ET.fromstring(_fetch(f"{repo_url}/{REPO_MD}"))
    for data in root.findall("repo:data", NS):
        if data.get("type") == "primary":
            location = data.find("repo:location", NS)
            if location is not None:
                return location.attrib["href"]
    raise RuntimeError(f"no primary metadata in {repo_url}")


def packages(repo: Repo) -> list[Package]:
    href = primary_href(repo.url)
    xml = _decompress(_fetch(f"{repo.url}/{href}"), href)
    root = ET.fromstring(xml)

    found: list[Package] = []
    for element in root.findall("common:package", NS):
        version = element.find("common:version", NS)
        size = element.find("common:size", NS)
        fmt = element.find("common:format", NS)
        license_element = (
            fmt.find("{http://linux.duke.edu/metadata/rpm}license") if fmt is not None else None
        )
        found.append(
            Package(
                name=element.findtext("common:name", "", NS),
                version=version.get("ver", "") if version is not None else "",
                release=version.get("rel", "") if version is not None else "",
                arch=element.findtext("common:arch", "", NS),
                summary=" ".join(element.findtext("common:summary", "", NS).split()),
                license=license_element.text if license_element is not None else "",
                size=int(size.get("package", 0)) if size is not None else 0,
            )
        )
    return found


def latest_only(found: list[Package]) -> list[Package]:
    """Collapse a repo's builds to the newest one per (name, arch).

    PGDG keeps older builds around -- pgexporter alone carries five. Listing
    every one buries the signal, and the question this file answers is "can I
    get X from PGDG", not "which builds exist".
    """
    from functools import cmp_to_key

    try:
        import rpm  # type: ignore[import-not-found]

        def newer(left: Package, right: Package) -> int:
            return rpm.labelCompare(
                (None, left.version, left.release),
                (None, right.version, right.release),
            )
    except ImportError:

        def _key(value: str) -> list:
            # Coarse fallback: numeric chunks compare numerically, others
            # lexically. Good enough to pick a newest build for a snapshot.
            return [
                int(part) if part.isdigit() else part for part in re.split(r"(\d+)", value) if part
            ]

        def newer(left: Package, right: Package) -> int:
            a = (_key(left.version), _key(right.version))
            try:
                if a[0] != a[1]:
                    return 1 if a[0] > a[1] else -1
            except TypeError:
                lv, rv = str(left.version), str(right.version)
                if lv != rv:
                    return 1 if lv > rv else -1
            lr, rr = str(left.release), str(right.release)
            if lr == rr:
                return 0
            return 1 if lr > rr else -1

    best: dict[tuple[str, str], Package] = {}
    for package in found:
        key = (package.name, package.arch)
        current = best.get(key)
        if current is None or newer(package, current) > 0:
            best[key] = package
    return sorted(best.values(), key=cmp_to_key(lambda a, b: (a.name > b.name) - (a.name < b.name)))


def render(snapshots: list[tuple[Repo, list[Package], int]]) -> str:
    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    lines = [
        "# PGDG package inventory",
        "",
        f"Snapshot of the PGDG repositories `roles/repos` configures, taken {stamp}",
        f"for **{EL}-{ARCH}**.",
        "",
        "Generated by `bin/snapshot_pgdg_packages.py`; do not edit by hand.",
        "Refresh with `./bin/snapshot_pgdg_packages.py`.",
        "",
        'This is a point-in-time record for answering *"can this come from PGDG',
        'instead of a vendor repo, EPEL, or an upstream tarball?"* Nothing',
        "installs from it, and it pins nothing. Only the newest build of each",
        "package is listed; PGDG retains older builds.",
        "",
        "## Repositories",
        "",
        "| Repo id | Packages | Base URL |",
        "|---------|---------:|----------|",
    ]
    for repo, found, _total in snapshots:
        lines.append(f"| `{repo.repo_id}` | {len(found)} | `{repo.url}` |")
    lines.append("")
    for repo, _found, _total in snapshots:
        lines.append(f"- **`{repo.repo_id}`** — {repo.note}")
    lines.append("")

    lines += [
        "## Monitoring exporters",
        "",
        "Called out because the exporter package source is an open question for",
        "`roles/monitoring_agents` (see `tests/molecule/COVERAGE.md`).",
        "",
    ]
    exporters = []
    for repo, found, _total in snapshots:
        for package in found:
            if "export" in package.name.lower() or "prometheus" in package.name.lower():
                exporters.append((repo.repo_id, package))
    if exporters:
        lines += ["| Package | Version | Arch | Repo | License |", "|---|---|---|---|---|"]
        for repo_id, package in exporters:
            lines.append(
                f"| `{package.name}` | {package.evr} | {package.arch} "
                f"| `{repo_id}` | {package.license} |"
            )
    else:
        lines.append("None.")
    lines.append("")
    present = {package.name for _repo, package in exporters}
    absent = [
        name
        for name in (
            "postgres_exporter",
            "pg_exporter",
            "node_exporter",
            "pgbouncer_exporter",
            "pgbackrest_exporter",
        )
        if name not in present
    ]
    if absent:
        lines += [
            "Not packaged by PGDG (would need another repo or an upstream tarball): "
            + ", ".join(f"`{name}`" for name in absent)
            + ".",
            "",
        ]

    for repo, found, total in snapshots:
        lines += [
            f"## `{repo.repo_id}`",
            "",
            f"{len(found)} packages ({total} builds including superseded ones).",
            "",
            "| Package | Version | Arch | Summary |",
            "|---|---|---|---|",
        ]
        for package in found:
            summary = package.summary.replace("|", "\\|")
            if len(summary) > 90:
                summary = summary[:87] + "..."
            lines.append(f"| `{package.name}` | {package.evr} | {package.arch} | {summary} |")
        lines.append("")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero if the snapshot differs from the current repo contents",
    )
    args = parser.parse_args()

    snapshots = []
    for repo in repos():
        print(f"fetching {repo.repo_id} ...", file=sys.stderr)
        try:
            found = packages(repo)
        except Exception as error:  # noqa: BLE001
            print(f"  failed: {error}", file=sys.stderr)
            return 1
        snapshots.append((repo, latest_only(found), len(found)))
        print(f"  {len(found)} builds, {len(snapshots[-1][1])} packages", file=sys.stderr)

    rendered = render(snapshots)

    if args.check:
        if not OUTPUT.exists():
            print(f"{OUTPUT} is missing; run ./bin/snapshot_pgdg_packages.py", file=sys.stderr)
            return 1
        current = OUTPUT.read_text()
        # The date line changes on every run; compare everything else.
        strip = lambda text: "\n".join(  # noqa: E731
            line for line in text.splitlines() if not line.startswith("Snapshot of the PGDG")
        )
        if strip(current) != strip(rendered):
            print(f"{OUTPUT} is stale; run ./bin/snapshot_pgdg_packages.py", file=sys.stderr)
            return 1
        print(f"{OUTPUT} is current", file=sys.stderr)
        return 0

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(rendered)
    print(f"wrote {OUTPUT.relative_to(ROOT)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
