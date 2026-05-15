# monitoring_agents

Per-node telemetry. Targets `all` hosts. Installs `node_exporter`
everywhere, the PostgreSQL/pgBouncer/pgBackRest exporters on postgres
hosts, and `vmagent` + `vlagent` to scrape locally and ship to the
monitor host.

## What this role owns

- `node_exporter` on every host (`network_any_address:9100`).
- `postgres_exporter` (9187), `pgbouncer_exporter` (9127),
  `pgbackrest_exporter` (9854) on postgres hosts only.
- `vmagent` (`network_loopback_address:8429`) — scrapes local exporters,
  Patroni REST, and HAProxy stats, `remote_write`s to the monitor.
- `vlagent` (`network_loopback_address:9429`) — tails journald + PG
  logs + Patroni logs, ships to the monitor.
- The `postgres-exporter`, `pgbouncer-exporter`, `pgbackrest-exporter`
  custom firewalld services.

## What this role does NOT own

- vmsingle/vlsingle/vmalert/Alertmanager — that's `monitoring_server`.
- Grafana / nginx — separate roles.

## Ordering

`_assert` → `_exporters` → `_vmagent` → `_vlagent` → `_firewall`.
Exporters must listen before vmagent's scrape config references them.

## Idempotence

Second run is zero-change: packages present, exporter units
content-templated, the collection's agent roles diff their own config,
firewalld services content-compared.

## Tags

- `monitoring` — full role
- `monitoring,install` — exporters only
- `monitoring,config` — agent configs only
- `monitoring,firewall` — firewalld only
