# pgbackrest

Installs and configures pgBackRest. Two modes selected via `pgbackrest_mode`:

- `server` — the `backup_server` host. Runs `pgbackrest server` daemon, owns repo, creates stanza, installs backup timers, sets `archive_command` on each postgres node, connects to postgres nodes over TLS to pull WAL and run backups.
- `client` — postgres node. Runs `pgbackrest server` daemon so the server can reach back to read PG data; points `repo1-host` at the server.

There is no single-host mode. See §1.1 of the main design doc.

## Requirements

- `roles/certs` must run first (deploys PKI certs to `pki_dir`).
- `roles/patroni` must run first on postgres nodes (provides `postgres_extra_parameters` injection point).
- Inventory group `backup_server` must exist with exactly one host.

## Key Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `pgbackrest_mode` | _(required)_ | `server` or `client` |
| `pgbackrest_stanza` | `pigsty` | Stanza name |
| `pgbackrest_repo_path` | `/var/lib/pgbackrest` | Repository path |
| `pgbackrest_retention_full` | `4` | Number of full backups to retain |
| `pgbackrest_schedule_full` | `Sun *-*-* 01:00:00` | systemd OnCalendar for full backups |
| `pgbackrest_schedule_diff` | `Mon..Sat *-*-* 01:00:00` | systemd OnCalendar for differential backups |
| `pgbackrest_pki_dir` | `/etc/pki/pigsty` | Path where certs role deployed certs |
| `pgbackrest_s3_enabled` | `false` | Enable S3 secondary repo |
| `pgbackrest_tls_port` | `8432` | pgBackRest TLS server port |

## S3 Secondary Repo

Set `pgbackrest_s3_enabled: true` and supply vault-encrypted values for:

- `pgbackrest_s3_key`
- `pgbackrest_s3_key_secret`
- `pgbackrest_s3_bucket`, `pgbackrest_s3_endpoint`, `pgbackrest_s3_region`, `pgbackrest_s3_path`
