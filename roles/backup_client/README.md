# backup_client

The pgBackRest client side. Targets the `postgres` group. On every
postgres node it installs pgBackRest, generates a dedicated SSH keypair
(owned by the `postgres` OS user), and renders the client config. On the
current Patroni leader only, it sets PostgreSQL's `archive_command`
through Patroni's dynamic config.

## Inputs (from response file, via group_vars)

| Variable | Meaning | Default |
| --- | --- | --- |
| `backup_stanza` | stanza name | `{{ cluster_name }}` |
| `backup_store_user` | store user on the store host | `pgbackrest` |
| `postgres_osdba` | OS account running PostgreSQL | `postgres` |

## What this role owns

- The `pgbackrest` RPM on each postgres node.
- `/var/lib/pgsql/.ssh/id_pgbackrest{,.pub}` (the dedicated keypair).
- `/etc/pgbackrest/pgbackrest.conf` (client-side).
- PostgreSQL `archive_mode` / `archive_command` (via Patroni dynamic config).

## What this role does NOT own

- The backup store directory or `authorized_keys` - that's `backup_store`.
- Stanza creation - that's `backup_store` (the store is remote from the
  client's perspective, so `stanza-create` runs on the backup store host).
- Scheduled backup timers - that's `backup_store`.
- Restore / PITR - separate plan.

## Ordering

`_assert` -> `_install` -> `_ssh` -> `_configure` -> `_archive`. Runs
**before** `backup_store` in `site.yml` so the
`backup_client_ssh_pubkey` fact exists when the store builds
`authorized_keys`.

## Idempotence

Second run is zero-change: package present, keypair present (not
regenerated), config content-compared, `archive_command` already set in
Patroni config, stanza-create tolerates "already exists".

## Tags

- `backup` - full role
- `backup,install` - package only
- `backup,config` - config + SSH key only
- `backup,service` - archive_command + stanza only
