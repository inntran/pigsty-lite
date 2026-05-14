# First run

This guide walks through the minimal pigsty-lite P0+P1+P2a workflow on a fresh
control node. P0 ships the cross-cutting pieces: preflight, repos, node
baseline, CA, and per-host certs. P1 adds the etcd cluster. P2a adds
PostgreSQL and Patroni. Roles for monitoring, backups, and reverse proxy ship
in later sub-plans.

## Prerequisites

Control node:

- Linux or macOS
- Python 3.12+
- `ansible` Python package
- git, make, gpg
- One-time: `make init` (installs Galaxy collections and roles)

Target hosts:

- RHEL 10, Rocky 10, or Alma 10
- SELinux in `enforcing` mode
- firewalld installed
- SSH access from the control node with `become` privileges

## Steps

1. Generate a response file.

   ```bash
   cp responses/single.rsp.yml.example responses/site.rsp.yml
   $EDITOR responses/site.rsp.yml
   ```

   Use `network.ip_version: dual` for mixed/default behavior, `ipv4` to require
   IPv4 inputs, or `ipv6` to require IPv6 node IPs, firewall CIDRs, and HBA CIDR
   sources. IPv6 single-stack mode also switches generated bind defaults to
   `::1` for local services and `::` for wildcard listeners.

2. Validate it.

   ```bash
   ./configure --validate responses/site.rsp.yml
   ```

3. Generate inventory and variables.

   ```bash
   ./configure -s -f responses/site.rsp.yml
   ```

   This writes `inventory/site.yml` and `group_vars/response.yml`.

4. Dry-run.

   ```bash
   make plan
   ```

5. Deploy.

   ```bash
   make deploy
   ```

After P0+P1, every host has PGDG enabled, baseline firewalld, sysctl tuning,
`/etc/pki/pigsty-lite/<host>.{crt,key}` plus `ca.crt`, and every `etcd` group
member runs a healthy mTLS-secured etcd member. Verify with:

```bash
ansible etcd -i inventory/site.yml -m command -b -a \
  'etcdctl --endpoints=https://127.0.0.1:2379 \
    --cacert=/etc/pki/pigsty-lite/ca.crt \
    --cert=/etc/pki/pigsty-lite/{{ inventory_hostname }}.crt \
    --key=/etc/pki/pigsty-lite/{{ inventory_hostname }}.key \
    endpoint health'
```

### postgres + patroni (P2a)

After `_etcd.yml` succeeds, two playbooks run against the `postgres` group:

- `_postgres_install.yml` installs `postgresql18-server` and
  `postgresql18-contrib` from PGDG, prepares `/var/lib/pgsql/18/data`, and
  masks `postgresql-18.service` so Patroni owns the PostgreSQL lifecycle.
- `_postgres_bootstrap.yml` installs Patroni, renders `/etc/patroni/patroni.yml`
  with etcd/REST/PostgreSQL TLS backed by the pigsty-lite CA, opens firewalld
  `patroni-rest` on 8008/tcp, starts `patroni.service`, and gates on a running
  member plus exactly one cluster leader.

Profile mapping:

- `single`: 1 PG host with `postgres_role=primary`.
- `ha`: 1 primary + replicas. Patroni elects the leader via etcd.

Useful checks:

```bash
sudo -u postgres patronictl -c /etc/patroni/patroni.yml list
curl -sk https://$(hostname -i):8008/cluster
sudo -u postgres /usr/pgsql-18/bin/psql -h 127.0.0.1 -U postgres -c '\l'
# In dual-stack or IPv6 mode, raw PostgreSQL also listens on ::1.
sudo -u postgres /usr/pgsql-18/bin/psql -h ::1 -U postgres -c '\l'
```

Patroni passwords are not auto-generated in P2a. For now, override
`patroni_superuser_password`, `patroni_replication_password`, and
`patroni_rewind_password` via vault-encrypted inventory vars or
`group_vars/response.yml`.

### connection layer (P2b)

After `_postgres_bootstrap.yml` succeeds, three playbooks run on the
`postgres` group:

- `_pgbouncer.yml` (pgbouncer role) installs pgBouncer 1.21+ from PGDG,
  renders `/etc/pgbouncer/{pgbouncer.ini,userlist.txt}`, enables
  `pgbouncer.service`, and waits for port 6432. The pgbouncer firewalld
  service ships but is disabled — clients reach pgBouncer indirectly,
  via HAProxy.
- `_haproxy.yml` (haproxy role) installs HAProxy from the vendor repo,
  renders `/etc/haproxy/haproxy.cfg` with three frontend/backend pairs
  health-checked against Patroni REST (`/leader` for 5432/5433,
  `/replica` for 5434), enables `haproxy.service`, opens the built-in
  `postgresql` firewalld service (5432) and the custom `haproxy-postgres`
  service (5433+5434), binds dedicated local service address
  `127.0.0.2`, and toggles the `haproxy_connect_any` SELinux boolean so
  HAProxy can reach Patroni REST on peer hosts. Tests and local clients
  that intentionally target HAProxy should use this dedicated local
  address rather than raw PostgreSQL's `127.0.0.1`.
- `_vip_manager.yml` (vip-manager role) is a no-op unless the operator
  sets `connection_layer.vip_manager.enabled: true` in the response
  file. When enabled, it installs `vip-manager` from PGDG-extras,
  renders `/etc/vip-manager.yml` pointing at the etcd cluster, and
  binds the configured VIP to the configured interface on whichever
  host is currently the Patroni leader. HAProxy binds the configured
  default interface addresses plus the VIP service addresses on every
  postgres node, including IPv6 addresses, and writes
  `/etc/sysctl.d/90-pigsty-lite-haproxy-vip.conf` so those non-local
  binds are valid before the VIP moves.

Try the cluster:

```bash
# Generic (5432) - routes to leader
psql "host=pgnode01 port=5432 dbname=postgres user=postgres"

# Explicit RW (5433) - leader only
psql "host=pgnode01 port=5433 dbname=postgres user=postgres"

# Explicit RO (5434) - replicas (round-robin)
psql "host=pgnode01 port=5434 dbname=postgres user=postgres"
```

A failover triggered by `patronictl switchover` is invisible to clients
hitting 5432 or 5433 after a few seconds (HAProxy detects the leader
change via Patroni REST and re-routes). RTO target ~45s under the
default `norm` profile; tighten via
`connection_layer.haproxy.rto_profile: tight` if you want sub-15s at
the cost of more false-positive health-check flapping.

### vip-manager (optional)

To enable:

```yaml
# In responses/site.rsp.yml
connection_layer:
  vip_manager:
    enabled: true
    vip_cidr: "10.20.30.20/24"
    interface: "eth0"
```

Then `./configure -s -f responses/site.rsp.yml && make deploy`. After
deployment, `ip addr show eth0` on the current leader will show
`10.20.30.20/24` as a secondary address; the other hosts will not have
it. After a Patroni switchover the address migrates within ~3–5 seconds.
Clients should use `10.20.30.20:5432` as the stable default service.
For local troubleshooting, use `127.0.0.1:5432` or `[::1]:5432` for raw
PostgreSQL, `127.0.0.2:5432` for local HAProxy, and `127.0.0.1:6432`
for local pgBouncer.

## Troubleshooting

- `preflight` fails on SELinux: confirm `getenforce` returns `Enforcing` on
  every host. Reboot or `setenforce 1` if it was disabled at runtime.
- `dnf install` fails on PGDG repo: confirm internet access to
  `download.postgresql.org` or proxy via inventory environment settings.
- `certs` task hangs on CSR fetch: control-node user lacks read access to
  `pki/ca/`. Run from the user that ran `_ca.yml`.
- `etcd` start fails with "no such host": peer URLs use `ansible_host` for
  each member; ensure every host in the `etcd` group has a reachable
  `ansible_host` value, or override `etcd_advertise_address` in
  `host_vars/<host>.yml`.

## After P3 (provisioning)

Once `make deploy` runs end-to-end, your declared databases, users,
extensions, and HBA rules from `responses/site.rsp.yml` are applied on
the Patroni leader and propagate to replicas via streaming replication.

Verify:

```bash
ssh pgnode01 sudo -iu postgres psql -d <your-db> -c "SELECT current_user;"
ssh pgnode01 sudo grep -v '^#' /var/lib/pgsql/18/data/pg_hba.conf
```

To add a database, user, or extension day-2, see
[docs/operations/day2-provisioning.md](day2-provisioning.md).

## After P4 (backups)

`make deploy` installs pgBackRest on the backup store host and every
postgres node, creates the stanza, wires PostgreSQL's `archive_command`
through Patroni, and installs systemd timers for weekly full + daily
differential backups.

Verify:

```bash
# On any postgres node, as the postgres user:
ssh pgnode01 sudo -iu postgres pgbackrest --stanza=<cluster_name> info

# On the backup store host, confirm the timers are active:
ssh pgmon01 systemctl list-timers 'pgbackrest-*'
```

If `archive_mode` flipped on for the first time, Patroni reports
`pending_restart`. Apply it during a maintenance window:

```bash
ssh pgnode01 sudo patronictl restart <cluster_name> --pending
```

To trigger a manual backup, change retention, or enable an S3 second
store, see [docs/operations/day2-backups.md](day2-backups.md).
