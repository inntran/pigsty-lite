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
role. Replication and rewind connect over TLS (`sslmode: verify-ca` against the
pigsty-lite CA) and authenticate with `scram-sha-256` passwords.

## pg_hba.conf

Patroni is the only writer of `pg_hba.conf`, on every member. The role renders
`postgresql.pg_hba` from three lists, in order:
`patroni_hba_system_rules` (replication, rewind and local access;
address-family aware), `patroni_hba_monitor_rules`, and
`patroni_hba_operator_rules` (`postgres.hba_rules` from the response file).
Patroni rewrites the file at start and on every reload, so hand edits revert.
The file is not replicated: a rule written on one member only would be
missing after a failover.

The system rules allow `postgres` over the Unix socket via `peer` (OS
identity), and any user over loopback (`127.0.0.1`, `::1`) with
`scram-sha-256`; pooled connections through pgBouncer rely on loopback TCP.
Replication and rewind from other members use TLS with `scram-sha-256`.
There are no `trust` rules.

A changed `patroni.yml` is applied with `systemctl reload patroni` (SIGHUP),
never a restart. If a reload leaves PostgreSQL settings pending a restart,
the play warns; apply them one member at a time.

## Firewall

The role opens `patroni-rest` (8008/tcp) and admits PostgreSQL (5432) from
each other cluster member with a firewalld rich rule. Each member's address
is resolved from `patroni_advertise_address`, then `ansible_host`, then its
gathered IP, and the rule's family (IPv4 or IPv6) follows that address. A
member with no address fails the play and is named. Using a member's address
as the rule's source relies on cluster members sharing one network, with no
NAT or multi-homing between peers.

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

The role never restarts Patroni for a drop-in change: handlers run on every
member together, and a restart stops PostgreSQL. It reloads systemd and
warns; the new settings apply at each member's next restart, done one member
at a time.

## What this role does NOT do

- No business databases or users. P3 (roles/provision) handles those via
  `community.postgresql` modules.
- No pgBouncer, HAProxy, or VIP. P2b adds those.
- No backups. P4 wires pgBackRest.

## Variables

See `defaults/main.yml`. The most important contract:

- etcd endpoints are computed from `groups['etcd']`. Override only for external
  etcd deployments not represented in inventory.
- `patroni_scope` defaults to `cluster_name`; this becomes the etcd key prefix
  and Patroni cluster name.
