# vip_manager

Optional. Watches the Patroni REST `/leader` key in etcd and, on the host
that wins the leader election, binds a single L2 VIP (e.g.
`10.20.30.20/24`) to a named interface (e.g. `eth0`). Other hosts release
the VIP.

This role is **gated off by default** (`vip_manager_enabled: false`).
The `vip-manager` package is installed unconditionally so the binary is
present and ready; only the config rendering and service activation are
gated. When disabled, the role removes `/etc/vip-manager.yml` and
ensures the `vip-manager.service` unit is stopped and disabled.

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

`vip_manager_package` pins `vip-manager >= 5.0.0`. v5 removed the legacy
`hostingtype`/`hosting_type` config-key aliases that older 4.x builds
accepted as synonyms for `manager-type`; only `manager-type` binds on
5.x, which is the key this role renders.

## Required vars when enabled

- `vip_manager_enabled: true`
- `vip_manager_vip_cidr: "10.20.30.20/24"` — the VIP and its netmask.
- `vip_manager_interface: "eth0"` — the interface on the postgres hosts.
  Optional: leave empty (the default) to auto-detect the interface that
  carries the host's own inventory/`ansible_host` address from gathered
  facts. The role fails clearly if no interface matches, rather than
  guessing.

The role asserts `vip_manager_vip_cidr` is set when `enabled` is true. It
refuses to bind a "default" address.

## What this role does NOT do

- No multi-VIP support. One VIP per cluster.
- No external load balancer integration (Hetzner mode is plumbed but
  untested in pigsty-lite; treat it as v2).
- No reverse-ARP probing. vip-manager itself handles ARP announcements
  on takeover.

## Testing

Molecule tests in this project verify the **disabled** path: package
installs, config is absent, and service is inactive when
`vip_manager_enabled: false`. Enabling it requires a real L2 network and
a routable VIP, which podman doesn't model. Use the smoke test in
Task 23 for that.
