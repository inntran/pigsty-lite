# Day-2 backups

Backups are declarative through the response file's generic `backup:`
block. P4 supports `tool: pgbackrest`; future plans may add additional
tools under the same contract. Edit the response file, then
`make deploy`. To target only the backup step, pass `--tags backup`.

## Trigger a manual backup

Backups run on the backup store host, which SSHes into the current
Patroni leader. To run one by hand:

```bash
# On the backup store host, as the pgbackrest user:
sudo -iu pgbackrest pgbackrest --stanza=<cluster_name> --type=full backup
sudo -iu pgbackrest pgbackrest --stanza=<cluster_name> --type=diff backup
```

Or trigger the systemd unit directly:

```bash
sudo systemctl start pgbackrest-backup@full.service
```

## Read backup status

```bash
sudo -iu pgbackrest pgbackrest --stanza=<cluster_name> info
```

The `archive` section shows the WAL range; `backup` lists each full and
differential with timestamps and sizes.

## Change retention

```yaml
backup:
  enabled: true
  tool: pgbackrest
  retention:
    full: 8        # keep 8 full backups instead of 4
```

`make deploy` re-renders the server-side config. The next `expire` timer
run, or a manual `pgbackrest expire`, enforces the new policy.

## Change the schedule

```yaml
backup:
  enabled: true
  tool: pgbackrest
  schedule:
    full: "0 2 * * 0"          # Sundays at 02:00
    differential: "0 2 * * 1-6"
```

The configure generator maps these onto systemd `OnCalendar`
expressions on the backup store host.

## Enable an S3-compatible second store

```yaml
backup:
  enabled: true
  tool: pgbackrest
  secondary_store:
    enabled: true
    type: s3
    bucket: my-pg-backups
    endpoint: s3.us-east-1.amazonaws.com
    region: us-east-1
    access_key: !vault | ...
    secret_key: !vault | ...
```

`make deploy` writes the secondary store settings into the server-side
config and a `0600` credentials file owned by the `pgbackrest` user.
Backups then push to both stores.

## Common gotchas

- **`archive_mode` pending restart**: the first time archiving is
  enabled, Patroni needs a restart. Run
  `patronictl restart <cluster_name> --pending` in a maintenance window.
- **TLS handshake fails**: pgbackrest uses mutual TLS via the cluster
  PKI from `roles/certs`. Each postgres node's cert CN must appear in
  the server's `tls-server-auth=<CN>=<stanza>` list. If you rebuilt a
  postgres node or re-issued its cert, re-run `--tags backup` so the
  server config picks up the new CN and the daemon reloads.
- **Store disk full**: `/var/lib/pgbackrest` must be sized for
  `retention.full` full backups plus the WAL between them. The
  `pgbackrest` role (server mode) warns if it is not a directory when
  it starts.
- **Restore / PITR**: not covered here; handled by `playbooks/restore.yml`
  and a separate runbook.
