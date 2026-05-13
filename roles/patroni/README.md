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
