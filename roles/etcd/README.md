# etcd

Install etcd from PGDG, render `/etc/etcd/etcd.conf.yml`, open firewalld
services `etcd-client` and `etcd-server` to peers + clients, and gate on
`etcdctl endpoint health` before returning.

## Profile shapes

- `single`: 1 member, no quorum, `initial-cluster-state: new`. Adequate
  for `single` deployments where the host is the failure domain anyway.
- `ha`: 3 members colocated on the postgres hosts, on a different block
  device than `/var/lib/pgsql/*/data` per the design.

## TLS

Both peer (2380) and client (2379) endpoints require TLS and client
certificate authentication. The role reuses the per-host cert issued by
the P0 `certs` role; CN = `inventory_hostname`. Peer-to-peer auth works
because each peer presents its own host cert and trusts the same CA.

## Variables

See `defaults/main.yml`. Override paths only with strong reason; SELinux
fcontext rules are tied to the vendor default `/var/lib/etcd`.

## Firewalld

Opens built-in services `etcd-client` and `etcd-server` with rich rules
restricting source addresses to the `etcd` group (peer) and `etcd` plus
`postgres` group (client). Nothing else.

## Backups (intentionally none)

There is no etcd snapshot timer in this stack. Everything in etcd is
Patroni DCS state: leader lease, member list, dynamic config, failover
history. Leases are ephemeral by definition; member list and dynamic
config are reconstructible from `pg_controldata` plus the static
`patroni.yml`; failover history is observability, not recoverability.

### Recovery from total etcd loss

1. Stop Patroni on every postgres node (`systemctl stop patroni`).
2. Wipe and reinstall etcd on the etcd hosts (re-run the `etcd` role).
   For `ha`, start all three members in `initial-cluster-state: new`.
3. Start Patroni on every postgres node (`systemctl start patroni`).
   Each node bootstraps from its on-disk data directory; one wins the
   leader race within a few seconds and the others rejoin as replicas.
4. Verify with `patronictl list` and confirm `archive_command` is still
   in effect (`SHOW archive_command` on the leader).

This is faster than restoring an etcd snapshot would be, and it cannot
restore stale state (e.g. a snapshot that names a leader that has since
been replaced).
