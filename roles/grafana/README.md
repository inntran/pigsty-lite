# grafana

Dashboards. Targets the `monitor` group (one host). Installs Grafana via
the `grafana.grafana` collection, then configures the VictoriaMetrics
and VictoriaLogs datasources and provisions dashboards via
`community.grafana` modules and file provisioning.

## What this role owns

- Grafana on `network_loopback_address:3000`, SQLite-backed,
  `serve_from_sub_path` true (fronted by nginx at `/grafana/`).
- The VictoriaMetrics (Prometheus-type) and VictoriaLogs datasources.
- Dashboards provisioned from `roles/grafana/files/dashboards/`.

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
