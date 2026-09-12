#!/usr/bin/env python3
"""Query the latest upstream exporter releases and update the pinned record.

The four exporters monitoring_agents installs are not packaged for EL10 by
PGDG or the vendor repos (see docs/reference/pgdg-packages.md), so they are
fetched as GitHub release artifacts and pinned by version + sha256 in
roles/monitoring_agents/vars/exporter_versions.yml.

The update workflow is: query GitHub first, then update the record.

    ./bin/check_exporter_releases.py            # report drift, write nothing
    ./bin/check_exporter_releases.py --update   # rewrite the pinned record
    ./bin/check_exporter_releases.py --check    # exit 1 if a pin is behind

Checksums come from each release's published checksums file, never from
hashing a local download -- a locally computed hash only proves the bytes
matched themselves, not that they matched what upstream published.

Requires `gh`, authenticated (`gh auth status`).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECORD = ROOT / "roles/monitoring_agents/vars/exporter_versions.yml"


@dataclass(frozen=True)
class Target:
    key: str
    repo: str
    # Picks the artifact to pin out of the release's asset list.
    asset_pattern: str
    # Names the release's checksums file.
    checksums_pattern: str
    kind: str


TARGETS = (
    Target(
        "node_exporter",
        "prometheus/node_exporter",
        r"^node_exporter-[\d.]+\.linux-amd64\.tar\.gz$",
        r"sha256sums?\.txt$",
        "tarball",
    ),
    Target(
        "postgres_exporter",
        "prometheus-community/postgres_exporter",
        r"^postgres_exporter-[\d.]+\.linux-amd64\.tar\.gz$",
        r"sha256sums?\.txt$",
        "tarball",
    ),
    Target(
        "pgbouncer_exporter",
        "prometheus-community/pgbouncer_exporter",
        r"^pgbouncer_exporter-[\d.]+\.linux-amd64\.tar\.gz$",
        r"sha256sums?\.txt$",
        "tarball",
    ),
    Target(
        "pgbackrest_exporter",
        "woblerr/pgbackrest_exporter",
        r"^pgbackrest_exporter_[\d.]+_linux_x86_64\.rpm$",
        r"checksums?\.txt$",
        "rpm",
    ),
)


def _gh(args: list[str], *, binary: bool = False) -> bytes | str:
    result = subprocess.run(  # noqa: S603
        ["gh", *args],  # noqa: S607
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.decode(errors="replace").strip()
        raise RuntimeError(f"gh {' '.join(args)} failed: {message}")
    return result.stdout if binary else result.stdout.decode()


def latest(target: Target) -> dict:
    raw = _gh(
        [
            "release",
            "view",
            "--repo",
            target.repo,
            "--json",
            "tagName,publishedAt,assets",
        ]
    )
    release = json.loads(raw)
    names = [asset["name"] for asset in release["assets"]]

    asset = next((name for name in names if re.match(target.asset_pattern, name)), None)
    if asset is None:
        raise RuntimeError(
            f"{target.repo} {release['tagName']}: no asset matching "
            f"{target.asset_pattern!r}; assets were: {', '.join(names[:10])}"
        )

    checksums_name = next(
        (name for name in names if re.search(target.checksums_pattern, name, re.I)), None
    )
    if checksums_name is None:
        raise RuntimeError(f"{target.repo} {release['tagName']}: no checksums asset")

    checksums = _gh(
        [
            "release",
            "download",
            "--repo",
            target.repo,
            "--pattern",
            checksums_name,
            "--output",
            "-",
        ]
    )
    digest = None
    for line in str(checksums).splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[-1].lstrip("*") == asset:
            digest = parts[0]
            break
    if digest is None:
        raise RuntimeError(f"{target.repo}: {asset} absent from {checksums_name}")

    tag = release["tagName"]
    version = tag.lstrip("v")
    return {
        "repo": target.repo,
        "version": version,
        "tag": tag,
        "released": release["publishedAt"][:10],
        "kind": target.kind,
        "asset": asset,
        "sha256": digest,
        "url": f"https://github.com/{target.repo}/releases/download/{tag}/{asset}",
    }


def pinned() -> dict:
    try:
        import yaml
    except ImportError:
        print("PyYAML is required; run via `uv run`", file=sys.stderr)
        raise SystemExit(2) from None
    if not RECORD.exists():
        return {}
    loaded = yaml.safe_load(RECORD.read_text()) or {}
    return loaded.get("monitoring_agents_exporter_releases", {})


def rewrite(found: dict[str, dict]) -> None:
    """Update versions in place, preserving the file's comments.

    The record is documentation as much as data -- the header explains why
    these are pinned at all, and the pgbackrest_exporter entry explains why it
    installs differently. A yaml.dump round-trip would discard all of it.
    """
    text = RECORD.read_text()
    current = pinned()

    for key, latest_release in found.items():
        old = current.get(key)
        if not old:
            continue
        # Anchor edits to the entry's own block so a shared value (a version
        # string two exporters happen to share) cannot be rewritten globally.
        block = re.search(rf"^  {re.escape(key)}:\n(?:(?:    .*)?\n)*", text, re.M)
        if not block:
            raise RuntimeError(f"{RECORD.name}: no block for {key}")
        updated = block.group(0)
        for field in ("version", "tag", "released", "asset", "sha256", "url"):
            value = latest_release[field]
            quote = '"' if field in ("version", "released") else ""
            updated = re.sub(
                rf"^(    {field}: ).*$",
                lambda match, v=value, q=quote: f"{match.group(1)}{q}{v}{q}",
                updated,
                flags=re.M,
            )
        text = text[: block.start()] + updated + text[block.end() :]

    RECORD.write_text(text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--update", action="store_true", help="rewrite the pinned record")
    group.add_argument("--check", action="store_true", help="exit 1 if any pin is behind")
    args = parser.parse_args()

    current = pinned()
    found: dict[str, dict] = {}
    drifted: list[str] = []

    for target in TARGETS:
        try:
            release = latest(target)
        except Exception as error:  # noqa: BLE001
            print(f"{target.key}: {error}", file=sys.stderr)
            return 2
        found[target.key] = release
        have = (current.get(target.key) or {}).get("version")
        if have == release["version"]:
            print(f"  {target.key:22} {have:10} up to date")
        else:
            drifted.append(target.key)
            print(f"  {target.key:22} {have or '-':10} -> {release['version']}  ({release['tag']})")

    if not drifted:
        print("\nAll exporter pins are current.")
        return 0

    if args.update:
        rewrite(found)
        print(f"\nUpdated {RECORD.relative_to(ROOT)}: {', '.join(drifted)}")
        print(
            "Review the diff, then update docs/reference/exporters.md if versions are quoted there."
        )
        return 0

    print(f"\n{len(drifted)} pin(s) behind: {', '.join(drifted)}")
    print("Run ./bin/check_exporter_releases.py --update to refresh the record.")
    return 1 if args.check else 0


if __name__ == "__main__":
    raise SystemExit(main())
