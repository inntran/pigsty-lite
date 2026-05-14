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
```

Patroni passwords are not auto-generated in P2a. For now, override
`patroni_superuser_password`, `patroni_replication_password`, and
`patroni_rewind_password` via vault-encrypted inventory vars or
`group_vars/response.yml`.

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
