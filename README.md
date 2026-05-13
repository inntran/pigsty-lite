# pigsty-lite

A turn-key Ansible deployment for production-grade PostgreSQL with HA, monitoring, and backups on RHEL-family Linux. A lean reinterpretation of [Pigsty](https://github.com/pgsty/pigsty) that drops the scope creep, follows Ansible best practices, reuses high-quality community collections, and respects the host OS — SELinux stays enforcing, paths stay vendor-default, firewalld stays in charge.

**Status:** P0 (Foundation), P1 (etcd), and P2a (PostgreSQL + Patroni) are
complete. Subsequent sub-plans (P2b connection layer, P3 provisioning, P4
backups, P5 monitoring, P6 lifecycle/portability) are pending. The architecture
and scope are defined in
[`docs/superpowers/specs/2026-05-12-pigsty-lite-design.md`](docs/superpowers/specs/2026-05-12-pigsty-lite-design.md).

## What you get

- **HA PostgreSQL 18** with Patroni on etcd, streaming replication over TLS, local HAProxy + pgBouncer on every database node.
- **pgBackRest** with a dedicated repository host (colocated with monitoring by default), continuous WAL archiving, scheduled full + differential backups, optional S3-compatible offsite repo.
- **VictoriaMetrics + VictoriaLogs** for metrics and logs via the official `victoriametrics.cluster` collection.
- **Grafana** dashboards (PG, HAProxy, node, Patroni, pgBouncer, pgBackRest) behind an nginx TLS reverse proxy.
- **vmalert + Alertmanager** with sensible default rules.
- **Two reference profiles:** `single` (1 monitor + 1 postgres) for dev or small prod, `ha` (1 monitor + 3 postgres with colocated etcd quorum) for production. Read replicas scale by inventory edit, not a new profile.
- **Operator response file** in Oracle silent-install style: one YAML file is the contract between operator and tooling.
- **Portable** — `make export` produces a single AES256-encrypted bundle of all operator state (CA, certs, passwords, response file, inventory) that can be moved to any new control node with `make import`.

## What you don't get (out of scope, on purpose)

MinIO, Redis, MongoDB, FerretDB, Citus, MSSQL/MySQL compatibility, Docker apps, pgAdmin, dnsmasq, a custom CLI, and the rest of pigsty's "everything in one box" surface. Major-version PG upgrades are a documented DBA runbook, not a playbook.

## Requirements

**Control node (the machine you run `ansible-playbook` from):**

- Linux or macOS
- `ansible` Python package
- `git`, `make`, `gpg`, Python 3
- `make init` to fetch upstream Galaxy collections and roles

**Target hosts:**

- RHEL 10, Rocky Linux 10, or AlmaLinux 10
- SELinux in `enforcing` mode
- firewalld present and not masked
- Storage layout pre-provisioned by the operator:
  - PostgreSQL data dir on its own LV/PV
  - etcd data dir on a *different* block device than PG data
  - Backup repo host has a separate mount sized for retention
- SSH access from the control node with `become` privileges

Playbooks never run `parted`, `mkfs`, `pvcreate`, `lvcreate`, or `mount`. Storage is operator responsibility; preflight checks warn on layout, fail fast on SELinux state.

## Quick start (planned UX)

```bash
git clone <this repo>
cd pigsty-lite
make init                    # install Galaxy collections + roles

./configure                  # interactive: profile, IPv4/IPv6 IPs, passwords
make plan                    # ansible --check --diff
make deploy
```

Set `network.ip_version: ipv6` in the response file to enforce IPv6
single-stack inputs. In that mode, node IPs, firewall CIDRs, and HBA CIDR
sources must be IPv6, and generated bind defaults use `::1`/`::`.

Day-2 changes (add a database, rotate a password, tune a parameter) mean: edit `responses/site.rsp.yml`, then `make deploy`.

See the full design document for details: [docs/superpowers/specs/2026-05-12-pigsty-lite-design.md](docs/superpowers/specs/2026-05-12-pigsty-lite-design.md)

## Roadmap

| Sub-plan | Scope | Status |
| --- | --- | --- |
| P0 | Foundation: scaffolding, configure CLI, preflight/repos/node/ca/certs | done |
| P1 | etcd cluster | done |
| P2a | PostgreSQL + Patroni (HA cluster bootstrap) | done |
| P2b | Connection layer: pgBouncer + HAProxy + vip-manager | pending |
| P2c | Integration tests + RTO measurement | pending |
| P3 | Provisioning (users, databases, extensions, HBA) | pending |
| P4 | Backups (pgBackRest, repo host, S3 offsite, PITR) | pending |
| P5 | Monitoring stack (VictoriaMetrics, VictoriaLogs, Grafana, nginx_proxy) | pending |
| P6 | Lifecycle ops + portability bundle | pending |
| P7 | Integration tests (libvirt, chaos) | pending |

## Credit

This project draws inspiration from [Pigsty](https://github.com/pgsty/pigsty) by Feng Ruohang (Vonng). Pigsty is an enterprise-grade open-source PostgreSQL distribution that solves a broad set of problems — including region-specific challenges for users in China — and packages a wide range of databases and tools beyond PostgreSQL.

**pigsty-lite** takes a narrower path: PostgreSQL only, RHEL-family only, vendor-default everything, with the operational discipline (SELinux, firewalld, package vendor paths, community Galaxy collections) that fits a global audience of system administrators and DBAs.

## License

Apache License 2.0 — see [LICENSE](LICENSE).
