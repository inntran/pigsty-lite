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
  Patroni REST, and (outside the `spof` profile) HAProxy stats,
  `remote_write`s to the monitor.
- `vlagent` (`network_loopback_address:9429`) — tails journald + PG
  logs + Patroni logs, ships to the monitor.
- The `postgres-exporter`, `pgbouncer-exporter`, `pgbackrest-exporter`
  custom firewalld services.

## Scrape targets and the `spof` profile

`site.yml` skips the HAProxy play when `cluster_profile` is `spof`, so
`vmagent-scrape.yml.j2` applies the same gate. Without it a single-node
deployment carries a permanently-down `haproxy` target, which is
indistinguishable from a load balancer that has actually fallen over.

## Credentials the exporters need

`pgbouncer_exporter` is the only exporter that authenticates.
`roles/pgbouncer` sets `auth_type = scram-sha-256`, which applies to the
admin console too — a local unix-socket connection from the right OS user
still gets `fe_sendauth: no password supplied`. The working credential is
the Patroni superuser, which `roles/pgbouncer` already writes into
`userlist.txt` and lists in `pgbouncer_admin_users` / `pgbouncer_stats_users`.

It reaches the exporter through a pgpass file
(`monitoring_agents_pgbouncer_exporter_pgpass`, `0600`, owned by the unit's
`User=`), referenced from the unit as `Environment=PGPASSFILE=…` — never
inside `--pgBouncer.connectionString`, which would put it in a `0644` unit
file and in `/proc/<pid>/cmdline`. The pgpass `host` field is pgBouncer's
*socket directory*, not `localhost`: that is what libpq matches a
unix-socket connection against, and the wrong value falls through to a
password prompt the exporter cannot answer.

Note that the socket directory is pgBouncer's own
(`pgbouncer_unix_socket_dir`, `/var/run/pgbouncer`), not PostgreSQL's.
Pointing the exporter at `/var/run/postgresql` yields a healthy-looking
`/metrics` endpoint publishing `pgbouncer_up 0` forever.

## The CA copy

The agents verify the monitor host against `monitoring_agents_ca_file`, a
`0644` copy of `{{ pigsty_pki_dir }}/ca.crt` placed in
`monitoring_agents_secrets_dir`. They cannot read the PKI directory itself —
it is `0750 root:pigsty` because it also holds every private key, and the
agents run as `vic_vm_agent` / `vic_vl_agent`. Pointing them at the original
gets ``cannot read `ca_file`: permission denied``, which takes down the
Patroni scrape *and* `remote_write`, because vmagent builds the TLS transport
even when the remote write URL is plain HTTP. A CA certificate is public
material, so a readable copy beats widening the PKI directory.

Rotating the CA replaces that file behind an unchanged `ExecStart`, so the
upstream agent roles see nothing to do; the copy task notifies its own
`Restart vmagent` / `Restart vlagent` handlers.

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

## Exporter versions

None of the four exporters are packaged for EL10 by PGDG, the vendor repos, or
EPEL, so `_install_exporter.yml` fetches each one's release tarball, verifies
its sha256, and installs the binary to `/usr/bin`. Versions and checksums are
pinned in [`vars/exporter_versions.yml`](vars/exporter_versions.yml).

Re-running is a no-op: the installed binary's `--version` is checked first, so
a converged host downloads nothing.

To update, query upstream first, then update the record:

```bash
./bin/check_exporter_releases.py            # report drift
./bin/check_exporter_releases.py --update   # rewrite the pins
```

[`docs/reference/exporters.md`](../../docs/reference/exporters.md) covers what
each exporter is and why these were chosen over `pgexporter` and Percona PMM.

## Idempotence

Second run is zero-change: packages present, exporter units
content-templated, the collection's agent roles diff their own config,
firewalld services content-compared.

## Tags

- `monitoring` — full role
- `monitoring,install` — exporters only
- `monitoring,config` — agent configs only
- `monitoring,firewall` — firewalld only
