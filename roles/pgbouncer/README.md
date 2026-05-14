# pgbouncer

Per-host pgBouncer sidecar listening on port 6432. Pools client
connections to the local Patroni-managed PostgreSQL instance on
`127.0.0.1:5432`. pgBouncer never talks to a remote PG — it always
points at the local PG, and HAProxy upstream decides which node clients
hit. This keeps the "no pooler split-brain on failover" property:
pgBouncer doesn't have to know about leader changes; it just dies when
PG dies and is recreated when PG comes back.

## Auth

`scram-sha-256` with `auth_query` against `pg_shadow`, so pgBouncer
verifies client credentials using the SCRAM verifier stored by
PostgreSQL. The `auth_user` entry is still rendered into the userlist so
pgBouncer can connect upstream to run the query. Add additional
userlist entries only if you intentionally bypass `auth_query`.

## Firewall

The `pgbouncer` firewalld service ships with the project but is
**disabled by default**. Clients connect via HAProxy on 5432; pgBouncer
is reached only locally over `127.0.0.1:6432`. If you really want
external pgBouncer access, set `pgbouncer_firewalld_enabled: true`.

## Reload vs restart

Most settings reload via `pgbouncer -R`; this role uses
`systemctl reload` which sends `SIGHUP`. A handful of settings require
restart (port, listen_addr) — those are gated to fire the
`Restart pgbouncer` handler explicitly in `_configure.yml`.
