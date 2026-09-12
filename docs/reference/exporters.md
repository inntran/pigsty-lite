# Exporters

`roles/monitoring_agents` scrapes four Prometheus exporters. This is what they
are, why each was chosen, and how their versions are kept current.

Pinned versions live in
[`roles/monitoring_agents/vars/exporter_versions.yml`](../../roles/monitoring_agents/vars/exporter_versions.yml).
That file is the record; this document is the reasoning.

## The four

| Exporter | Upstream | License | Port | Scrapes |
|---|---|---|---:|---|
| `node_exporter` | `prometheus/node_exporter` | Apache-2.0 | 9100 | Host CPU, memory, disk, network |
| `postgres_exporter` | `prometheus-community/postgres_exporter` | Apache-2.0 | 9187 | PostgreSQL via a local socket |
| `pgbouncer_exporter` | `prometheus-community/pgbouncer_exporter` | MIT | 9127 | pgBouncer pool stats |
| `pgbackrest_exporter` | `woblerr/pgbackrest_exporter` | MIT | 9854 | Backup age, size, success |

All four are permissively licensed and compatible with this project's
Apache-2.0 licence.

## Why these, and not the alternatives

### Not PGDG

PGDG packages none of them. It ships exactly one exporter, `pgexporter`
(plus per-version `pgexporter_ext`) — see
[`pgdg-packages.md`](pgdg-packages.md) for the full inventory.

`pgexporter` is a credible project: C, BSD-3-Clause, an official PGDG RPM, and
per-PG-version metric definitions. It was rejected on metric naming. It emits
`pgexporter_*`, while
[`roles/grafana/files/dashboards/pigsty-lite-overview.json`](../../roles/grafana/files/dashboards/pigsty-lite-overview.json)
queries `pg_up`, `pg_stat_activity_count` and `pg_replication_lag_bytes`.
Adopting it means rewriting every panel and alert rule, and it covers only
PostgreSQL — the other three exporters would still come from elsewhere.

### Not Percona PMM

PMM Server is **AGPL-3.0** and ships as a container/OVA/AMI appliance with no
release tarballs. It bundles its own VictoriaMetrics, Grafana, ClickHouse and
PostgreSQL — duplicating the stack this project already builds — and its value
is multi-database coverage (MySQL, MongoDB, Valkey) that is explicitly out of
scope here.

`pmm-client` is a closer call and worth recording, because the obvious
objection to it is wrong: it is **Apache-2.0**, not AGPL, ships an EL10 RPM,
and bundles `node_exporter` + `postgres_exporter` + `vmagent` that run
standalone with the usual CLI flags and emit ordinary `pg_*` metrics.

It was still rejected:

- **392MB installed** (99MB packaged) to use perhaps 30MB. Every Postgres node
  would carry `mongodb_exporter`, `valkey_exporter`, `rds_exporter` and
  `azure_exporter`.
- Installs under `/usr/local/percona/pmm/`, against this project's
  vendor-default-paths rule.
- Its `postgres_exporter` is a fork pinned at **0.15.0** against upstream's
  0.20.1 — inheriting Percona's patch cadence and CVE timeline.
- It ships no `pgbouncer_exporter` or `pgbackrest_exporter`, so the packaging
  gap stays open anyway.

PMM does have one capability with no equivalent here: Query Analytics,
per-query latency via `pg_stat_monitor`. If that becomes a requirement, this
decision is worth revisiting.

## How they install

All four are linux/amd64 tarballs, unpacked into place by the role.

`pgbackrest_exporter` also publishes `.rpm` and `.deb`, and pinning the RPM
was the initial choice. It was reversed: the role overrides the unit file and
service user for every exporter anyway, so a `dnf` path for one of the four
would be a second code path buying nothing. One install path is worth more
than dnf's bookkeeping on a single binary.

Watch the asset naming when updating by hand — the prometheus projects use
`linux-amd64`, `woblerr` uses `linux-x86_64`.

Each pin carries the upstream-published `sha256` for its exact artifact, taken
from the release's own checksums file — not computed by hashing a local
download, which would only prove the bytes matched themselves.

## Keeping versions current

Query GitHub first, then update the record:

```bash
./bin/check_exporter_releases.py            # report drift, write nothing
./bin/check_exporter_releases.py --update   # rewrite the pinned record
./bin/check_exporter_releases.py --check    # exit 1 if behind (for CI)
```

The script uses `gh release view` for each repository, selects the
linux/amd64 artifact, reads the digest out of the release's checksums file,
and rewrites `exporter_versions.yml` in place — preserving its comments, which
a `yaml.dump` round-trip would discard.

Requires `gh` authenticated (`gh auth status`).

After an update, re-read the release notes for breaking changes before
deploying: a new `postgres_exporter` minor can rename or drop collectors, and
the dashboards query metric names directly.

## Install path

`_install_exporter.yml` handles all four identically:

1. Run the installed binary's `--version`. All four print
   `<name>, version X.Y.Z`, though `node_exporter` uses stdout and the other
   three use stderr.
2. If it is missing or the version does not match the pin, fetch the tarball
   into a staging directory with `get_url`, which verifies the sha256 *before*
   putting the file in place — a substituted artifact fails there and never
   reaches `/usr/bin`.
3. Unpack, copy the binary to `/usr/bin/<name>` as `0755 root:root`, and
   notify that exporter's restart handler.
4. Remove the staging directory, on success or failure.

A converged host does no network I/O: the version check short-circuits every
download. Changing a pin — or a corrupted binary — triggers a reinstall of
just that exporter.

The unit files are rendered from templates in this role rather than taken
from upstream, so the service user, listen address and flags follow this
project's conventions. Upstream's own unit templates (woblerr ships
`pgbackrest_exporter.service.template`, prometheus ships
`examples/systemd/`) use an `EnvironmentFile` + `$ARGS` indirection that
buys nothing here, since Ansible already templates the arguments.

## Current status

The role installs and firewalls all four. `monitoring_agents/external_pull`
should now converge fully; `monitoring_agents/default` is blocked by an
unrelated pre-existing bug — its `molecule.yml` puts no host in the `etcd`
group, so `prepare` fails at Patroni's assert before the role runs. See
[`tests/molecule/COVERAGE.md`](../../tests/molecule/COVERAGE.md).
