# vip_manager

Optional. Watches the Patroni REST `/leader` key in etcd and, on the host
that wins the leader election, binds a single L2 VIP (e.g.
`10.20.30.20/24`) to a named interface (e.g. `eth0`). Other hosts release
the VIP.

This role is **gated off by default** (`vip_manager_enabled: false`). It
is a no-op unless the operator opts in by setting it true in the
response file.

## When to enable

- You have a spare IP address on the same L2 segment as the postgres
  hosts.
- Applications cannot use HAProxy on every node (e.g. you have one app
  IP and can't deploy a client-side load balancer).
- You're OK with the trade-off that VIP failover takes ~3–5s (etcd TTL +
  vip-manager loop wait).

If none of the above apply, leave this disabled. HAProxy on every node
(P2b's `haproxy` role) already provides client-transparent failover.

## Packaging

vip-manager is published in the PGDG-extras YUM repository, which is
enabled by the P0 `repos` role. The role installs `vip-manager` from
there directly; no third-party tarball.

## Required vars when enabled

- `vip_manager_enabled: true`
- `vip_manager_vip_cidr: "10.20.30.20/24"` — the VIP and its netmask.
- `vip_manager_interface: "eth0"` — the interface on the postgres hosts.

The role asserts both are set when `enabled` is true. It refuses to bind
a "default" address.

## What this role does NOT do

- No multi-VIP support. One VIP per cluster.
- No external load balancer integration (Hetzner mode is plumbed but
  untested in pigsty-lite; treat it as v2).
- No reverse-ARP probing. vip-manager itself handles ARP announcements
  on takeover.

## Testing

Molecule tests in this project verify the **disabled** path (role is a
no-op when `vip_manager_enabled: false`). Enabling it requires a real L2
network and a routable VIP, which podman doesn't model. Use the smoke
test in Task 23 for that.
