# monitoring_server

The metrics/logs/alerting backend. Targets the `monitor` group (one
host). Installs VictoriaMetrics single-node (`vmsingle`), VictoriaLogs
single-node (`vlsingle`), `vmalert`, and Alertmanager. Agents on every
node `remote_write` metrics and ship logs here.

The Alertmanager package install explicitly enables the disabled-by-default
`epel` repository for that task only.

## Inputs (from response file, via group_vars)

| Variable | Meaning | Default |
| --- | --- | --- |
| `vmsingle_retention` | metrics retention | `90d` |
| `vlsingle_retention` | logs retention | `30d` |
| `alertmanager_receivers` | Alertmanager receiver list | `[]` |

## What this role owns

- `vmsingle` on `network_any_address:8428` (firewalled to postgres + monitor).
- `vlsingle` on `network_any_address:9428` (firewalled to postgres + monitor).
- `vmalert` on `network_loopback_address:8880` (loopback only).
- Alertmanager on `network_loopback_address:9093` (loopback only).
- The `victoriametrics` and `victorialogs` custom firewalld services.

## What this role does NOT own

- Agents and exporters — that's `monitoring_agents`.
- Grafana — that's the `grafana` role.
- The nginx reverse proxy fronting vmalert/alertmanager UIs — that's `nginx_proxy`.

## Ordering

`_assert` → `_vmsingle` → `_vlsingle` → `_vmalert` → `_alertmanager` →
`_firewall`. vmalert is configured after vmsingle (its datasource) and
Alertmanager (its notifier) exist.

## Idempotence

Second run is zero-change: the collection roles diff their own config,
the Alertmanager config is content-templated, firewalld services are
content-compared.

## Tags

- `monitoring` — full role
- `monitoring,config` — re-render configs only
- `monitoring,firewall` — firewalld only
- `monitoring,service` — restart services only
