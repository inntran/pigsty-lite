# pgBackRest Role Design

**Date:** 2026-05-15  
**Status:** Approved

## Goal

Replace `roles/bad_backup_client` and `roles/bad_backup_store` with a single `roles/pgbackrest` role that handles both sides of the pigsty-lite backup topology. The role name matches the tool name so it stays meaningful if the backup engine is ever swapped.

## Modes

Controlled by `pgbackrest_mode` (no default — `meta/main.yml` lists it as required, so a missing value fails fast at role entry):

- **`server`** — dedicated pgBackRest repository host (the `backup_server` group). Owns the repo, runs `pgbackrest server` daemon, runs backup/expire timers, connects to postgres nodes over TLS to pull WAL and run backups.
- **`client`** — postgres node. Points `repo1-host` at the server, runs `pgbackrest server` daemon so the server can reach back to read PG data, sets `archive_command` via `postgres_extra_parameters`.

There is **no `standalone` mode** for single-host all-in-one deployments. Per the pigsty-lite design principles (§1.1 of the main design doc), the backup repo is never colocated with PostgreSQL — a backup on the same disk as the data it backs up is not a backup. Operators who want off-host durability on the `single` profile enable the optional S3 secondary store on the `backup_server` host.

## Variables (`defaults/main.yml`)

```yaml
pgbackrest_mode: ~                   # required: server | client (no default — fail fast)
pgbackrest_stanza: pigsty
pgbackrest_repo_path: /var/lib/pgbackrest
pgbackrest_log_path: /var/log/pgbackrest
pgbackrest_config_file: /etc/pgbackrest/pgbackrest.conf
pgbackrest_tls_port: 8432
pgbackrest_retention_full: 4
pgbackrest_schedule_full: "Sun *-*-* 01:00:00"
pgbackrest_schedule_diff: "Mon..Sat *-*-* 01:00:00"

# PKI — certs role deploys to this dir as <hostname>.crt / <hostname>.key / ca.crt
pgbackrest_pki_dir: "{{ pki_dir | default('/etc/pki/pigsty') }}"

# Server host — used by client mode to point repo1-host
pgbackrest_server_host: "{{ groups['backup_server'][0] }}"

# PostgreSQL paths — used by server mode to render per-node pg<N>-path entries
pgbackrest_pg_path: "{{ postgres_data_dir }}"
pgbackrest_pg_port: "{{ postgres_port | default(5432) }}"

# S3 secondary repo (optional)
pgbackrest_s3_enabled: false
pgbackrest_s3_bucket: ~
pgbackrest_s3_endpoint: ~
pgbackrest_s3_region: us-east-1
pgbackrest_s3_path: /pgbackrest
pgbackrest_s3_key: ~           # from Ansible Vault
pgbackrest_s3_key_secret: ~    # from Ansible Vault
pgbackrest_s3_retention_full: "{{ pgbackrest_retention_full }}"
```

## File Layout

```
roles/pgbackrest/
  defaults/main.yml
  handlers/main.yml
  meta/main.yml
  tasks/
    main.yml          # import_tasks based on mode
    _install.yml      # dnf install pgbackrest + selinux helpers
    _config.yml       # render pgbackrest.conf
    _service.yml      # deploy + start pgbackrest.service
    _stanza.yml       # stanza-create + check
    _archive.yml      # set archive_mode/archive_command via postgres_extra_parameters + patroni reload
    _timers.yml       # deploy full/diff systemd timers
    _firewall.yml     # open tls_port from postgres nodes (server mode only)
  templates/
    pgbackrest.conf.j2
    pgbackrest.service.j2
    pgbackrest-backup@.service.j2
    pgbackrest-backup@.timer.j2
  README.md
```

## Task Flow per Mode

| Task file      | server | client |
|----------------|--------|--------|
| `_install.yml` | ✓ | ✓ |
| `_config.yml`  | ✓ | ✓ |
| `_service.yml` | ✓ | ✓ |
| `_firewall.yml`| ✓ | — |
| `_stanza.yml`  | ✓ | — |
| `_archive.yml` | ✓ | — |
| `_timers.yml`  | ✓ | — |

## Config Template Logic

Both modes use `/etc/pgbackrest/pgbackrest.conf` (owner: postgres, mode: 0640).

**server:**

```ini
[global]
repo1-path=/var/lib/pgbackrest
repo1-retention-full=4
log-path=/var/log/pgbackrest
start-fast=y
tls-server-address=*
tls-server-port=8432
tls-server-ca-file=/etc/pki/pigsty/ca.crt
tls-server-cert-file=/etc/pki/pigsty/<server-hostname>.crt
tls-server-key-file=/etc/pki/pigsty/<server-hostname>.key
tls-server-auth=<pg-node-cn>=<stanza>   # one per postgres node

# S3 repo2 block when pgbackrest_s3_enabled
repo2-type=s3
repo2-s3-bucket=...
...

[pigsty]
# one pg entry per postgres node
pg1-host=pg-node-1
pg1-host-type=tls
pg1-host-ca-file=/etc/pki/pigsty/ca.crt
pg1-host-cert-file=/etc/pki/pigsty/<server-hostname>.crt
pg1-host-key-file=/etc/pki/pigsty/<server-hostname>.key
pg1-host-port=8432
pg1-path=/var/lib/pgsql/18/data
pg1-port=5432
```

**client:**

```ini
[global]
repo1-host=<server-hostname>
repo1-host-type=tls
repo1-host-ca-file=/etc/pki/pigsty/ca.crt
repo1-host-cert-file=/etc/pki/pigsty/<client-hostname>.crt
repo1-host-key-file=/etc/pki/pigsty/<client-hostname>.key
repo1-host-port=8432
log-path=/var/log/pgbackrest
tls-server-address=*
tls-server-port=8432
tls-server-ca-file=/etc/pki/pigsty/ca.crt
tls-server-cert-file=/etc/pki/pigsty/<client-hostname>.crt
tls-server-key-file=/etc/pki/pigsty/<client-hostname>.key
tls-server-auth=<server-cn>=<stanza>
archive-async=y
spool-path=/var/spool/pgbackrest

[pigsty]
pg1-path=/var/lib/pgsql/18/data
pg1-port=5432
```

## Key Decisions

- **No pgbackrest OS user** — all operations run as `postgres`. The `pgbackrest.service` unit uses `User=postgres`.
- **Certs referenced directly** from `pgbackrest_pki_dir` (`/etc/pki/pigsty`) — no copy or symlink into a pgbackrest-specific cert dir.
- **`archive_command` via `postgres_extra_parameters`** — the pgbackrest role sets `archive_mode` and `archive_command` by merging into `postgres_extra_parameters`, which the patroni template already renders. Patroni is reloaded to apply.
- **S3 credentials from Ansible Vault** — rendered directly into `pgbackrest.conf` with `no_log: true`. No separate secrets file.
- **`tls-server-auth` uses cert CN = `inventory_hostname`** — the certs role sets CN to `inventory_hostname`, so server authorizes clients by hostname.
- **Stanza-create runs from the server** — idempotent (`failed_when` ignores "already exists").
- **`pgbackrest check` runs after stanza-create** — validates archiving end-to-end.
- **Systemd timers** — two timer+service pairs: `pgbackrest-backup@full.timer` and `pgbackrest-backup@diff.timer`. Instance name passed to `--type` flag.
