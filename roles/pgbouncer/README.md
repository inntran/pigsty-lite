# pgbouncer

Per-host pgBouncer sidecar listening on port 6432. Pools client
connections to the local Patroni-managed PostgreSQL instance on
`127.0.0.1:5432`. pgBouncer never talks to a remote PG — it always
points at the local PG, and HAProxy upstream decides which node clients
hit. This keeps the "no pooler split-brain on failover" property:
pgBouncer doesn't have to know about leader changes; it just dies when
PG dies and is recreated when PG comes back.

## Auth

`auth_type = hba`, driven by a rendered `pgbouncer_hba.conf`
(`auth_hba_file`). The hba file has two kinds of rules:

- A `peer` rule for `pgbouncer_peer_console_user` (defaults to
  `postgres_osdba`, i.e. the OS user the `pgbouncer_exporter` runs as)
  connecting to the special `pgbouncer` database over the Unix socket.
  This lets the exporter authenticate without a password so it can run
  `SHOW` commands against the admin/stats console. That user must be
  listed in `pgbouncer_stats_users` or `pgbouncer_admin_users`, or the
  role fails an assert during configuration.
- `scram-sha-256` for everything else (all databases, all users, local
  or remote), verified via `auth_query` against `pg_shadow`, so
  pgBouncer checks client credentials using the SCRAM verifier stored
  by PostgreSQL. The `auth_user` entry is still rendered into the
  userlist so pgBouncer can connect upstream to run the query. Add
  additional userlist entries only if you intentionally bypass
  `auth_query`.

## Firewall

The `pgbouncer` firewalld service ships with the project but is
**kept disabled zone-wide**. Clients normally connect via HAProxy; the
haproxy role admits 6432 only from postgres members because HAProxy on each
member dials every member's pgBouncer. To allow direct client access, set
`pgbouncer_firewalld_enabled: true`; the role then adds service rich rules
for `pgbouncer_client_cidrs`, which defaults to `postgres_client_cidrs`.

## Reload vs restart

Most settings reload via `pgbouncer -R`; this role uses
`systemctl reload` which sends `SIGHUP`. A handful of settings require
restart (port, listen_addr) — those are gated to fire the
`Restart pgbouncer` handler explicitly in `tasks/main.yml`.
