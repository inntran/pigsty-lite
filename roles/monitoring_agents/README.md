# monitoring_agents

Per-node telemetry. Targets `all` hosts. Installs `node_exporter`
everywhere, the PostgreSQL/pgBouncer/pgBackRest exporters on postgres
hosts, and `vmagent` + `vlagent` to scrape locally and ship to the
monitor host.

Exporter package installs explicitly enable the disabled-by-default `epel`
repository for those tasks only.

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

## external_push credentials

In `external_push` mode the remote_write basic-auth password and bearer
token are written to root-owned files under
`monitoring_agents_secrets_dir` (`/etc/pigsty/monitoring`), mode `0640`,
group-owned by the agent that reads them, and passed to the agents as
`-remoteWrite.basicAuth.passwordFile` / `-remoteWrite.bearerTokenFile`.

They are deliberately *not* passed as inline flag values: the upstream
`victoriametrics.cluster` roles interpolate every service arg into the
`ExecStart` line of a world-readable (`0644`) systemd unit, which would
also expose the secret through `/proc/<pid>/cmdline`. vmagent and vlagent
run as different service users (`vic_vm_agent`, `vic_vl_agent`), so each
gets its own file rather than sharing one. Disabling either auth method
removes the corresponding files.

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
