# First run

This guide walks through the minimal pigsty-lite P0 workflow on a fresh control
node. P0 ships the cross-cutting pieces: preflight, repos, node baseline, CA,
and per-host certs. Roles for etcd, PostgreSQL, monitoring, backups, and reverse
proxy ship in later sub-plans.

## Prerequisites

Control node:

- Linux or macOS
- Python 3.11+
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

After P0, every host has PGDG enabled, baseline firewalld, sysctl tuning, and
`/etc/pki/pigsty-lite/<host>.{crt,key}` plus `ca.crt`.

## Troubleshooting

- `preflight` fails on SELinux: confirm `getenforce` returns `Enforcing` on
  every host. Reboot or `setenforce 1` if it was disabled at runtime.
- `dnf install` fails on PGDG repo: confirm internet access to
  `download.postgresql.org` or proxy via inventory environment settings.
- `certs` task hangs on CSR fetch: control-node user lacks read access to
  `pki/ca/`. Run from the user that ran `_ca.yml`.
