# backup_store

The pgBackRest backup store host. Targets the `backup_store` group (one
host; by default colocated with `monitor`). Owns the backup store at
`/var/lib/pgbackrest`, accepts SSH public keys from the postgres nodes,
and runs scheduled backups via systemd timers that SSH into the current
Patroni leader.

## Inputs (from response file, via group_vars)

| Variable | Meaning | Default |
| --- | --- | --- |
| `backup_store_path` | backup store directory | `/var/lib/pgbackrest` |
| `backup_stanza` | stanza name (one per cluster) | `{{ cluster_name }}` |
| `backup_store_user` | OS account owning the store | `pgbackrest` |
| `backup_retention_full` | full backups to keep | `4` |
| `backup_secondary_store` | optional S3 store config | unset |

## What this role owns

- The `pgbackrest` store user and its `~/.ssh/authorized_keys`.
- `/var/lib/pgbackrest` (directory, ownership, SELinux label).
- `/etc/pgbackrest/pgbackrest.conf` (server-side).
- The stanza (`pgbackrest stanza-create`, run on the backup store host; the
  store is local here, so this is where stanza-create belongs).
- `pgbackrest-full`, `pgbackrest-diff`, `pgbackrest-expire`,
  `pgbackrest-check` systemd service + timer units.

## What this role does NOT own

- PostgreSQL's `archive_command` - that's `backup_client` (set via Patroni).
- The dedicated SSH keypair - `backup_client` generates it; this role
  only authorizes the public half.
- Restore / PITR - see `playbooks/restore.yml` (separate plan).

## Ordering

`_assert` -> `_install` -> `_ssh` -> `_configure` -> `_firewall` ->
`_stanza` -> `_timers`. The `_ssh` step reads `backup_client_ssh_pubkey`
facts that `backup_client` set earlier in the same `make deploy` run.
`_stanza` runs after `_configure` so the server-side config (with the
`pgN-host` SSH entries) exists, and after `_ssh`/`_firewall` so the store
host can reach the postgres nodes.

## Idempotence

Second run is zero-change: package present, user present,
`authorized_keys` content-compared, config templated by content, timer
units templated by content.

## Tags

- `backup` - full role
- `backup,install` - package + store dir only
- `backup,config` - render config only
- `backup,firewall` - firewalld only
- `backup,service` - timer units only
