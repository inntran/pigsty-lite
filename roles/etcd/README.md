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
