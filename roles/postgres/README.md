# postgres

Install PostgreSQL `{{ postgres_version }}` from PGDG, prepare vendor-default
filesystem layout, register SELinux fcontext for non-vendor data dirs, and
mask the vendor `postgresql-<ver>.service` unit so Patroni is the only process
that starts or stops PG.

## What this role does NOT do

- No `initdb`. Patroni owns cluster bootstrap.
- No `postgresql.conf` editing. Tuning profile is rendered by the `patroni`
  role into `patroni.yml -> postgresql.parameters`.
- No replication slot management. Patroni handles slots at the DCS layer.

## Variables

See `defaults/main.yml`. The most important downstream contract is that
`postgres_data_dir` matches the path Patroni writes into its own
`postgresql.data_dir`. Both roles consume `group_vars/postgres.yml` for this;
do not override per-host unless you really mean it.

## SELinux

Vendor data dir `/var/lib/pgsql/<ver>/data` carries `postgresql_db_t` by
default. If `postgres_data_dir` is overridden, this role registers an fcontext
rule and runs `restorecon`. We never `setenforce 0`.

## Firewalld

This role opens nothing. The `patroni-rest` service is opened by the patroni
role. Postgres port 5432 is exposed via HAProxy in P2b.
