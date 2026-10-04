# pgbackrest

Installs and configures pgBackRest. The `_pgbackrest.yml` playbook selects
`pgbackrest_mode` from inventory membership:

- `server` — the `backup_server` host. Runs `pgbackrest server` daemon, owns repo, creates stanza, installs backup timers, sets `archive_command` on each postgres node, connects to postgres nodes over TLS to pull WAL and run backups.
- `client` — postgres node. Runs `pgbackrest server` daemon so the server can reach back to read PG data; points `repo1-host` at the server.
- `local` — a postgres node that is also in `backup_server`. Keeps repo1 and the stanza on that host, creates the stanza, and installs backup timers without a TLS daemon or 8432 firewall rule.

On `postgres`, the mode is `local` when the host is also in `backup_server`,
otherwise `client`. A `backup_server` host outside `postgres` uses `server`.
Local mode requires exactly one postgres host.

## Requirements

- `roles/certs` must run first (deploys PKI certs to `pigsty_pki_dir`).
- `roles/patroni` must run first on postgres nodes (provides `postgres_extra_parameters` injection point).
- Inventory group `backup_server` must exist with exactly one host.

## Key Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `pgbackrest_mode` | _(required)_ | `server`, `client`, or `local`; selected from inventory by the playbook |
| `pgbackrest_stanza` | `pigsty` | Stanza name |
| `pgbackrest_repo_path` | `/var/lib/pgbackrest` | Repository path |
| `pgbackrest_retention_full` | `4` | Number of full backups to retain |
| `pgbackrest_schedule_full` | `Sun *-*-* 01:00:00` | systemd OnCalendar for full backups |
| `pgbackrest_schedule_diff` | `Mon..Sat *-*-* 01:00:00` | systemd OnCalendar for differential backups |
| `pigsty_pki_dir` | `/etc/pki/pigsty` | Path where certs role deployed certs (shared, not pgbackrest-specific) |
| `pgbackrest_s3_enabled` | `false` | Enable S3 secondary repo |
| `pgbackrest_tls_port` | `8432` | pgBackRest TLS server port |

## S3 Secondary Repo

Configure S3 in the response file's `backup.secondary_store` block. Its
enabled flag and bucket, endpoint, region, and path populate the pgBackRest
repo2 settings. Supply these operator-managed values in the Ansible vault:

- `vault_pgbackrest_s3_key`
- `vault_pgbackrest_s3_key_secret`

Repo2 is configured on every host, including PostgreSQL hosts, because WAL
`archive-push` runs there. Scheduled full and differential backups run once
against repo1 and, when S3 is enabled, once against repo2. The deploy-time
initial backup goes to repo1 only.
