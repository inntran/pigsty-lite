# patroni

Install Patroni from PGDG, render `/etc/patroni/patroni.yml`, hand PostgreSQL
lifecycle control to Patroni, and gate on a healthy cluster state before
returning.

## Cluster bootstrap

Patroni's own bootstrap runs on the first node to claim the leader key in etcd.
The role does not pre-seed `postgres_role=primary` as the leader; Patroni
decides via DCS election.

## Replication slots

Managed by Patroni. We do not pre-create them.

## TLS

REST API and PG `ssl=on` both use the per-host cert issued by the P0 certs
role. Replication uses certificate authentication via the `replicator` system
role; passwords remain the secondary credential.

## systemd customizations

The role installs a drop-in at
`/etc/systemd/system/patroni.service.d/10-pigsty-lite.conf`. Two things
live there worth knowing about:

- `LimitNOFILE`, `Restart`, `RestartSec`, `TimeoutStartSec` tuning.
- On hosts where etcd is colocated (every host in `groups['etcd']`),
  `After=etcd.service` + `Requires=etcd.service`. This makes systemd
  bring etcd up before patroni at boot and tear patroni down before
  etcd at shutdown, so patroni can release its leader lease cleanly.

Since the drop-in lives in a subdirectory, `systemctl status patroni`
won't show our additions inline. Use `systemctl cat patroni` to see
the merged unit, or `systemctl show patroni | grep -E '^(After|Requires)='`
to inspect ordering directly.

## What this role does NOT do

- No business databases, users, or runtime HBA rules. P3 handles that via
  `community.postgresql` modules.
- No pgBouncer, HAProxy, or VIP. P2b adds those.
- No backups. P4 wires pgBackRest.

## Variables

See `defaults/main.yml`. The most important contract:

- etcd endpoints are computed from `groups['etcd']`. Override only for external
  etcd deployments not represented in inventory.
- `patroni_scope` defaults to `cluster_name`; this becomes the etcd key prefix
  and Patroni cluster name.
