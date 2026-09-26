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

## PGSQL Exporter dashboard

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
