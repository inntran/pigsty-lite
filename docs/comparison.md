# pigsty-lite vs pigsty: Feature Comparison

pigsty-lite covers the critical PostgreSQL HA operational path with tighter code quality (smaller roles, Galaxy collection reuse, Molecule test coverage, strict SELinux and vendor-path alignment). Everything outside that path is intentionally excluded.

## What pigsty-lite has that pigsty lacks

| Capability | Notes |
| --- | --- |
| Molecule CI test coverage | 11 automated scenarios across all roles; pigsty relies on manual VM testing |
| SELinux enforcing | Required by pigsty-lite; pigsty disables it by default |
| Galaxy-first role design | Heavy reuse of `community.postgresql`, `community.crypto`, `victoriametrics.cluster`, `grafana.grafana`; pigsty is mostly self-contained |
| Vendor path defaults | `/var/lib/pgsql/...` throughout; pigsty uses custom `/pg/...` paths |
| Small, focused roles | 11 roles, each with 1–3 task files; pigsty's `pgsql` role alone is 16 task files and ~1,400 lines |

## Core PostgreSQL HA parity

These are the features that matter for a PostgreSQL operator. pigsty-lite covers all of them; P4 and P5 are planned phases, not missing features.

| Feature | pigsty-lite | pigsty |
| --- | --- | --- |
| PostgreSQL HA (Patroni + etcd) | ✅ | ✅ |
| pgBouncer connection pooling | ✅ | ✅ |
| HAProxy load balancing | ✅ | ✅ |
| vip-manager L2 VIP | ✅ | ✅ |
| pgBackRest backups | planned (P4) | ✅ |
| VictoriaMetrics + Grafana monitoring | planned (P5) | ✅ |
| VictoriaLogs | planned (P5) | ✅ |
| vmalert + Alertmanager | planned (P5) | ✅ |

## What pigsty has that pigsty-lite intentionally excludes

These are out of scope by design — pigsty-lite is a PostgreSQL operator, not an all-in-one platform.

| Feature | Reason excluded |
| --- | --- |
| Redis | Out of scope |
| MinIO (S3-compatible object storage) | Out of scope |
| MongoDB / FerretDB | Out of scope |
| Docker runtime + 23 pre-built apps | Out of scope |
| MSSQL / MySQL compatibility gateways | Out of scope |
| Custom `pig` CLI | Out of scope |
| 20+ configuration profiles | pigsty-lite ships `single` and `ha` only |
| Offline yum cache builder | Out of scope |
| Major-version PG upgrades (playbook) | Documented DBA runbook, not a playbook |
| dnsmasq / infra services | Out of scope |
| Regional mirror support (China, EU) | Out of scope |

## Architecture philosophy

| Dimension | pigsty-lite | pigsty |
| --- | --- | --- |
| Role count | 11 | 28 |
| Largest role size | ~150 lines | ~1,400 lines (pgsql) |
| External collection dependencies | Heavy (Galaxy-first) | Light (mostly self-contained) |
| Configuration variables | ~62 scalars in `group_vars/all.yml` | 200+ across 28 roles and 20+ profiles |
| Test strategy | Molecule (CI-friendly) | Manual sandbox VMs |
| OS posture | SELinux enforcing, firewalld, vendor paths | SELinux disabled, custom paths |

## Scope ratio

pigsty-lite is roughly **10–15% of pigsty's total feature count**, but covers ~100% of what a pure PostgreSQL HA operator needs. The delta is Redis, MinIO, Docker apps, and platform-as-a-service concerns — not PostgreSQL itself.
