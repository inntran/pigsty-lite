# grafana

Dashboards. Targets the `monitor` group (one host). Installs Grafana via
the `grafana.grafana` collection, then configures the VictoriaMetrics
and VictoriaLogs datasources and provisions dashboards via
`community.grafana` modules and file provisioning.

## What this role owns

- Grafana on `network_loopback_address:3000`, SQLite-backed,
  with `root_url` ending in `/grafana/`. The nginx proxy strips the
  `/grafana/` prefix before forwarding, so `serve_from_sub_path` stays false.
- The VictoriaMetrics (Prometheus-type) and VictoriaLogs datasources.
- Dashboards provisioned from `roles/grafana/files/dashboards/`.

## PostgreSQL Exporter dashboard

`pgsql-exporter.json` adapts upstream Pigsty's `files/grafana/pgsql/pgsql-exporter.json`
for the `prometheus-community/postgres_exporter` deployed by this project. It
keeps the exporter health, database scrape, collector, and process panels.
Pigsty-specific query cache, per-database exporter, and log panels have no
equivalent in the installed exporter or this monitoring stack.

The dashboard selects `job="postgres"` and the `cluster` and `instance` labels
set by `vmagent-scrape.yml.j2`. That template creates the job only for hosts in
the `postgres` inventory group and targets their `postgres_exporter` endpoint.
The overview distinguishes `up` (vmagent can scrape the exporter) from `pg_up`
(the exporter can connect to PostgreSQL). Its
`pg_exporter_last_scrape_duration_seconds`, `pg_exporter_last_scrape_error`,
and `pg_exporter_scrapes_total` queries use metrics emitted by
`postgres_exporter`. Grafana provisions the dashboard automatically with the
VictoriaMetrics datasource.

## Dashboards adapted from Pigsty

These five dashboards are adapted from `pgsty/pigsty@745a4ea` and licensed
Apache-2.0:

| Title | UID | Pigsty source |
|---|---|---|
| Node Instance | `node-instance` | `files/grafana/node/node-instance.json` |
| PostgreSQL Instance | `postgresql-instance` | `files/grafana/pgsql/pgsql-instance.json` |
| PostgreSQL Database | `postgresql-database` | `files/grafana/pgsql/pgsql-database.json` |
| PostgreSQL pgBouncer | `postgresql-pgbouncer` | `files/grafana/pgsql/pgsql-pgbouncer.json` |
| PostgreSQL Patroni | `postgresql-patroni` | `files/grafana/pgsql/pgsql-patroni.json` |

The adapted queries use only metrics pigsty-lite already collects:
`postgres_exporter` default collectors, `pgbouncer_exporter`, `node_exporter`,
and Patroni `/metrics`. They use no recording rules; labels are `cluster` and
`instance` instead of Pigsty's `cls`, `ins`, and `ip`. Panels without a native
metric equivalent were dropped rather than left empty. User-visible text says
`PostgreSQL`, not `PGSQL`.

There is no Tables dashboard because `postgres_exporter` connects with
`dbname=postgres`; `pg_stat_user_tables_*` would therefore show only tables
in the `postgres` database.

### Metric-name guardrail

`tests/configure/test_grafana_dashboards_static.py` fails if a dashboard
queries a name not in `tests/configure/fixtures/grafana_metric_names.txt`.
After an exporter version bump, refresh the fixture from a deployed AIO host:

```sh
curl -s http://127.0.0.1:8428/api/v1/label/__name__/values | python3 -c 'import json,sys; print("\n".join(sorted(json.load(sys.stdin)["data"])))'
```

Keep the fixture's header comment, updating its date and exporter versions,
and preserve the trailing replica-only section. Passing the guardrail proves
that names exist, not that queries return data.

### Live check

After deploying, generate light load with `pgbench` and open each dashboard
with its default variables. A panel showing "No data" is a bug unless it is
normally empty when idle: locks, waiting clients, paused/disabled pools,
Patroni pending restart / failsafe / WAL paused, or replication lag on a
single node.

## What this role does NOT own

- vmsingle/vlsingle — that's `monitoring_server`.
- TLS termination / the public `/grafana/` route — that's `nginx_proxy`.

## Ordering

`_assert` → `_install` → `_datasources` → `_dashboards`. Datasources
are created after Grafana is up; dashboards reference the datasources.

## Idempotence

Second run is zero-change: the collection role diffs Grafana config,
`grafana_datasource` is declarative, dashboard JSON is content-compared.

## Tags

- `monitoring` — full role
- `monitoring,config` — datasources + dashboards only
