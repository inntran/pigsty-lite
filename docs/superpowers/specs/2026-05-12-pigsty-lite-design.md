# pigsty-lite — Design
## Credit
We extend our sincere gratitude to Feng Ruohang, known as Vonng, and his foundational work on the [Pigsty project](https://github.com/pgsty/pigsty). His project, which is an enterprise-grade open-source PostgreSQL distribution, served as a great source of inspiration for this endeavor.

However, the original Pigsty was designed to satisfy a very broad base of users, requiring Vonng to overcome specific technical challenges that, while impressive, were often regional (particularly for users in China) and diminished the project's utility for the rest of the world. Therefore, we decided to create a new project with a streamlined approach, specifically focused on the essential needs of open-minded system administrators and DBAs globally.

## Overview
A turn-key Ansible deployment for production-grade PostgreSQL with HA, monitoring, and backups on RHEL-family Linux. A lean reinterpretation of pigsty that drops the scope creep, follows Ansible best practices, reuses high-quality community collections, and respects the host OS (SELinux enforcing, vendor paths, firewalld).

- **Version:** v1
- **Date:** 2026-05-12
- **Status:** design approved, plan pending
- **Author:** Yinchuan Song with Claude, using [Superpowers](https://github.com/obra/superpowers)

---

## 1 — Why this project exists

Pigsty is a thorough PostgreSQL deployment system but it bundles a lot beyond running PostgreSQL well in production: MinIO, Redis, MongoDB, Greenplum, Citus, MSSQL/MySQL compatibility, Docker apps, a YUM repo builder, custom CLI tools, FerretDB, and more. It also disables SELinux, relocates standard OS paths under `/pg`, ships a monolithic `pgsql` role with fifteen task files, and avoids Galaxy collections.

pigsty-lite keeps what makes pigsty's PG offering strong (Patroni, pgBackRest, exporter stack, declarative HBA/users/databases) and discards the rest. The result is:

- A small set of focused Ansible roles, each doing one thing.
- Vendor-default OS paths so SELinux works without disabling it.
- Operator-owned storage layout — playbooks check, never `mkfs`.
- Reuse of mature community collections (VictoriaMetrics, Grafana, community.postgresql, community.crypto) instead of reinventing them.
- A response-file UX modeled on Oracle's silent install pattern: one file is the contract between operator and tooling.

---

## 2 — Scope

### In scope

- **OS:** RHEL 10, Rocky 10, Alma 10. SELinux enforcing. firewalld.
- **Profiles:** `single` (1 monitor + 1 postgres + 1 colocated etcd), `ha` (1 monitor + 3 postgres with colocated etcd quorum). Read replicas scale by inventory edit, not new profile.
- **PostgreSQL:** PGDG RPM, version 18 by default. Pinning to specific minor versions supported.
- **HA:** Patroni on etcd. Streaming replication required to be TLS.
- **Connection layer:** local HAProxy per postgres node (Patroni REST health checks) + pgBouncer sidecar. Optional L2 VIP via vip-manager.
- **Backups:** pgBackRest with dedicated repo host (default colocated with monitor; splittable to its own host). Optional S3-compatible second repo. Weekly full + daily differential + continuous WAL.
- **PITR:** operator-invoked `restore.yml` with explicit confirmation.
- **Monitoring metrics:** VictoriaMetrics single-node (`vmsingle` + `vmagent`) via official `victoriametrics.cluster` collection. node/postgres/pgbouncer/pgbackrest exporters + Patroni REST + HAProxy stats.
- **Monitoring logs:** VictoriaLogs (`vlsingle` + `vlagent`) — same collection, same vendor.
- **Alerting:** vmalert + Alertmanager.
- **Dashboards:** Grafana via `grafana.grafana` collection (install) + `community.grafana` modules (config). SQLite-backed; no Postgres dependency.
- **Reverse proxy:** nginx on monitor host (`nginx_proxy` role) terminates TLS and fronts Grafana/Alertmanager/vmalert UIs.
- **TLS:** self-signed internal CA for inter-service mTLS, BYO accepted; user-facing endpoints accept BYO certs (CA-signed or operator-supplied); HTTPS encouraged but not forced.
- **Provisioning:** declarative roles/users/databases/extensions/HBA from response file via `community.postgresql` modules.
- **Minor PG upgrades:** automated rolling playbook with backup-freshness precondition.
- **Major PG upgrades:** plain documented runbook (`docs/operations/major-upgrade.md`); no playbook.
- **Portability:** `make export` / `make import` produce a single AES256-encrypted `tar.gz.gpg` bundle of operator-controlled state (CA, certs, passwords, response file, inventory). Operator transfers it to any new control node.

### Out of scope (explicitly excluded from v1)

MinIO bundling, Redis, MongoDB, FerretDB, Greenplum, Citus, Polar, MSSQL/MySQL compatibility, pgsodium, Docker, app marketplace, custom YUM repo builder, dnsmasq, vector (superseded by vlagent), pigsty's `bin/` CLI scripts, vmcluster, replicated Grafana, multiple infra-component HA, cross-region failover, cloud-provider Terraform, performance benchmarking gates, migration *from* pigsty.

---

## 3 — Architecture

### 3.1 Reference topologies

```
═══════════════════════════════════════════════════════════════════════
  PROFILE: single        (dev / small prod / staging — no PG HA)
═══════════════════════════════════════════════════════════════════════

   ┌─────────────────────────────┐    ┌─────────────────────────────┐
   │      pgmon01                │    │      pgnode01               │
   │ ─────────────────────────── │    │ ─────────────────────────── │
   │  vmsingle (TSDB)            │    │  postgresql-18 (PGDG RPM)   │
   │  vlsingle (logs)            │    │  patroni                    │
   │  vmagent  (scrape self)     │    │  etcd (1-node, no quorum)   │
   │  vlagent  (logs ship self)  │    │  pgbouncer                  │
   │  vmalert  + alertmanager    │    │  pgbackrest (client)        │
   │  grafana                    │    │  haproxy (local)            │
   │  nginx_proxy (TLS)          │    │  vmagent / vlagent          │
   │  backup_store (pgbackrest)  │◀──┤  node/pg/pgb/pgbr exporters │
   │  /var/lib/pgbackrest        │SSH │                             │
   │  /var/lib/victoria-logs     │    │                             │
   └─────────────────────────────┘    └─────────────────────────────┘
                                                  │
                                       (optional) ▼
                                       ┌─────────────────────────┐
                                       │  S3 / GCS / on-prem     │
                                       │  pgbackrest repo2       │
                                       │  (asynchronous)         │
                                       └─────────────────────────┘

═══════════════════════════════════════════════════════════════════════
  PROFILE: ha            (production — tolerates 1 node loss)
═══════════════════════════════════════════════════════════════════════

           ┌──────────────────────────────────────┐
           │           pgmon01 (1 VM)             │
           │  vmsingle  vlsingle  vmagent vlagent │
           │  vmalert  alertmanager  grafana      │
           │  nginx_proxy (TLS)                   │
           │  backup_store /var/lib/pgbackrest    │
           └──┬─────────────────┬─────────────────┘
              │SSH pull         │scrape /metrics
              │ backup          ▼
   ┌──────────┴───────┐    ┌──────────────────┐    ┌──────────────────┐
   │     pgnode01     │    │     pgnode02     │    │     pgnode03     │
   │ ───────────────  │    │ ───────────────  │    │ ───────────────  │
   │ postgresql-18    │    │ postgresql-18    │    │ postgresql-18    │
   │ patroni (leader) │◀▶│ patroni (repl)   │◀▶│ patroni (repl)   │
   │ etcd member-1    │◀▶│ etcd member-2    │◀▶│ etcd member-3    │
   │ pgbouncer        │    │ pgbouncer        │    │ pgbouncer        │
   │ haproxy (local)  │    │ haproxy (local)  │    │ haproxy (local)  │
   │ pgbackrest cli   │    │ pgbackrest cli   │    │ pgbackrest cli   │
   │ vmagent vlagent  │    │ vmagent vlagent  │    │ vmagent vlagent  │
   │ node/pg exporter │    │ node/pg exporter │    │ node/pg exporter │
   └──────────────────┘    └──────────────────┘    └──────────────────┘

   Scaling reads: add pgnode04 (replica) to inventory. No new profile.
   Optional L2 VIP via vip-manager binds to current Patroni leader.
```

### 3.2 Inventory groups (same shape for both profiles)

```yaml
all:
  children:
    monitor:        # vmsingle/vlsingle/vmalert/alertmanager/grafana/nginx_proxy
    backup_store:   # pgbackrest server-side; pulls from postgres nodes
    etcd:           # 1 or 3 hosts (Patroni DCS)
    postgres:       # 1 or 3+ hosts (PG + Patroni + pgbouncer + haproxy + backup_client)
```

By default `monitor` and `backup_store` resolve to the same host (`pgmon01`). Operators can split them onto separate hosts purely by editing inventory; no playbook changes required.

### 3.3 Storage assumptions (operator-provisioned, playbook preflight-checked)

- Every postgres host: `/var/lib/pgsql/<ver>` is a separate mount on its own LV/PV.
- Every postgres host: `/var/lib/etcd` is on a different block device than `/var/lib/pgsql/<ver>`.
- Backup store host: `/var/lib/pgbackrest` is a separate mount sized for the configured retention.

Preflight checks warn (not fail) on storage layout — many valid single-disk dev setups exist. SELinux mode is `enforcing` — preflight fails fast if not.

Playbooks NEVER run `parted`, `mkfs`, `pvcreate`, `lvcreate`, or `mount`. Storage is operator responsibility.

### 3.4 Module dependency graph (deploy order)

Arrows show "must run before"; not every arrow is a hard data dependency, but the order is what `site.yml` enforces.

```
preflight ─► ca (localhost) ─► node (repos, OS, firewalld baseline, certs distributed)
                                    │
                                    ├─► etcd
                                    │     │
                                    │     ▼
                                    ├─► postgres_install ─► postgres_bootstrap (patroni; needs etcd)
                                    │                         │
                                    │                         ▼
                                    │                     pgbouncer, haproxy, vip_manager (optional)
                                    │                         │
                                    │                         ▼
                                    │                     provision (primary only)
                                    │
                                    ├─► backup_store ─► backup_client (needs postgres up)
                                    │
                                    └─► monitoring_server ─► monitoring_agents ─► grafana ─► nginx_proxy
```

`monitoring_server`, `grafana`, and `nginx_proxy` have no hard dependency on the postgres path — they only need `node` complete on the monitor host. `site.yml` puts them after the postgres path so the first deploy reports a fully working stack at the end.

---

## 4 — Roles

Each role does one thing.

| Role | Targets | Responsibility |
|---|---|---|
| `preflight` | all | OS version, SELinux=enforcing, swap off, time sync, mounts present, block-device separation, fail fast with actionable errors |
| `repos` | all | Install `pgdg-redhat-repo` RPM, manage repo priorities (PGDG > vendor > EPEL > pigsty); EPEL opt-in only; pigsty repo opt-in via `repos_pigsty_packages` |
| `node` | all | Hostname, `/etc/hosts` from inventory, firewalld baseline, sysctl tuning, limits.d, journald sizing, time sync verification |
| `ca` | localhost | Generate self-signed CA in `pki/ca/`; idempotent; uses `community.crypto`; never regenerates if present |
| `certs` | all | Issue per-host certs from CA (postgres-server, patroni-rest, etcd-peer, etcd-client). Renew if `notAfter < cert_renewal_window` |
| `etcd` | etcd | Install etcd RPM, render `/etc/etcd/etcd.conf.yml`, systemd unit, TLS, post-install `etcdctl endpoint health` gate. Modeled on kubespray's role |
| `postgres` | postgres | Install postgresql-18 from PGDG, baseline `postgresql.auto.conf`; no initdb (Patroni owns bootstrap) |
| `patroni` | postgres | Install Patroni, render `/etc/patroni/patroni.yml`, systemd unit, wait for leader election |
| `pgbouncer` | postgres | Sidecar on every postgres node, userlist generation, HBA, listens on 6432 |
| `backup_client` | postgres | Install pgbackrest, configure stanza pointing at `backup_store` host over SSH, register WAL archive command |
| `backup_store` | backup_store | Install pgbackrest server-side, repo at `/var/lib/pgbackrest`, accept SSH keys from postgres nodes, systemd timers for scheduled backups, optional `repo2` for S3 |
| `haproxy` | postgres | Local HAProxy: service `default` (5432→primary), `primary` (5433→leader), `replica` (5434→replicas). Health via Patroni REST |
| `vip_manager` | postgres (optional) | Install vip-manager, watch Patroni REST, bind L2 VIP to leader |
| `monitoring_agents` | all | vmagent + vlagent + node_exporter + postgres_exporter + pgbouncer_exporter + pgbackrest_exporter |
| `monitoring_server` | monitor | vmsingle + vlsingle + vmalert + alertmanager (own role; alertmanager has no first-party VM Galaxy role) |
| `grafana` | monitor | Install via `grafana.grafana`; configure via `community.grafana`: VM/VL datasources, dashboards from `files/grafana-dashboards/` |
| `nginx_proxy` | monitor | nginx reverse proxy with TLS termination; routes `/grafana/`, `/alertmanager/`, `/vmalert/` |
| `provision` | postgres (primary only) | Apply HBA + roles + databases + extensions via `community.postgresql` modules |

### 4.1 Things explicitly NOT a role

Storage provisioning, YUM mirror, MinIO, Redis, Mongo, FerretDB, Citus, MSSQL, Docker, app marketplace, dnsmasq, vector, pgsodium key management, custom CLI tools.

### 4.2 Role design principles

- Each role has a `README.md` listing required vars, optional vars with defaults, dependencies, tags.
- Every role supports `--check --diff` cleanly.
- No role does identity computation; identity lives in inventory.
- No `tags: always`; orchestration is the play's job.
- No vendored binaries.
- Handlers reload, not restart, unless config genuinely requires restart.
- Idempotent: second run is a no-op.
- All shell/command tasks have explicit `changed_when` based on output.
- Variable names prefixed by role/domain (no bare `port:` or `data_dir:`).

---

## 5 — Repository layout and orchestration

```
pigsty-lite/
├── ansible.cfg                       # forks, pipelining, retry_files_enabled=false
├── requirements.yml                  # community collections, pinned versions
├── Makefile                          # configure | plan | deploy | export | import | lint | test
├── README.md
│
├── configure                         # Python script (stdlib only)
│                                     #   ./configure                  → interactive wizard
│                                     #   ./configure -c single|ha     → profile preset
│                                     #   ./configure -s -f file.yml   → silent (CI / scripts)
│                                     #   ./configure --validate FILE  → schema-check a response
│                                     # Emits: inventory/site.yml + responses/site.rsp.yml
│
├── responses/
│   ├── single.rsp.yml.example
│   ├── ha.rsp.yml.example
│   └── site.rsp.yml                  # generated (gitignored)
│
├── inventory/
│   ├── site.yml                      # generated (gitignored)
│   └── examples/
│       ├── single.yml
│       └── ha.yml
│
├── group_vars/
│   ├── all.yml
│   ├── monitor.yml
│   ├── backup_store.yml
│   ├── etcd.yml
│   ├── postgres.yml
│   └── response.yml                  # generated by configure; never hand-edit
│
├── host_vars/                        # empty by default
│
├── playbooks/
│   ├── site.yml                      # operator entry: full deploy
│   ├── _preflight.yml                # internal — one job each, underscored
│   ├── _ca.yml
│   ├── _node.yml
│   ├── _etcd.yml
│   ├── _postgres_install.yml
│   ├── _postgres_bootstrap.yml
│   ├── _pgbouncer.yml
│   ├── _haproxy.yml
│   ├── _vip_manager.yml
│   ├── _provision.yml
│   ├── _backup_store.yml
│   ├── _backup_client.yml
│   ├── _monitoring_server.yml
│   ├── _monitoring_agents.yml
│   ├── _grafana.yml
│   ├── _nginx_proxy.yml
│   ├── preflight.yml                 # operator alias for _preflight
│   ├── switchover.yml                # operator entry: controlled switchover
│   ├── failover.yml                  # operator entry: manual failover with confirm
│   ├── restore.yml                   # operator entry: PITR
│   ├── minor_upgrade.yml             # operator entry: rolling minor PG upgrade
│   ├── scale_add_replica.yml
│   ├── scale_remove_replica.yml
│   └── tags.md
│
├── roles/                            # one directory per role; see section 4
│
├── files/
│   ├── firewalld/services/           # custom service XMLs (see §6)
│   ├── grafana-dashboards/
│   ├── alerts/                       # vmalert rule groups (operator-extensible)
│   └── alertmanager/
│
├── pki/                              # CA + issued certs (gitignored)
│   ├── ca/
│   └── certs/
│
├── artifacts/                        # generated outputs (gitignored)
│   ├── hosts.lite                    # workstation /etc/hosts snippet
│   └── credentials.txt               # generated passwords, mode 0600
│
├── bin/
│   ├── export-bundle                 # python: tar + manifest + gpg
│   └── import-bundle                 # python: gpg + extract + verify
│
├── dist/                             # bundle output (gitignored)
│
├── tests/
│   ├── molecule/                     # per-role scenarios
│   └── integration/                  # libvirt VMs, local-only
│
└── docs/
    ├── architecture.md
    ├── operations/                   # switchover, failover, restore, scale, major-upgrade
    ├── reference/                    # variable reference, tag reference, ports
    └── superpowers/specs/            # this document
```

### 5.1 site.yml

```yaml
- import_playbook: _preflight.yml
- import_playbook: _ca.yml
- import_playbook: _node.yml
- import_playbook: _etcd.yml
- import_playbook: _postgres_install.yml
- import_playbook: _postgres_bootstrap.yml
- import_playbook: _pgbouncer.yml
- import_playbook: _haproxy.yml
- import_playbook: _vip_manager.yml
- import_playbook: _provision.yml
- import_playbook: _backup_store.yml
- import_playbook: _backup_client.yml
- import_playbook: _monitoring_server.yml
- import_playbook: _monitoring_agents.yml
- import_playbook: _grafana.yml
- import_playbook: _nginx_proxy.yml
```

### 5.2 One-job-per-playbook rule

- Install and bootstrap are different playbooks (`_postgres_install` vs `_postgres_bootstrap`) because their preconditions differ.
- Per-host-group playbooks split when the same component has both client and server sides on different hosts (`_backup_client` on postgres, `_backup_store` on backup_store; `_monitoring_agents` on all, `_monitoring_server` on monitor).
- State that needs to cross playbooks lands on disk (CA cert, credentials.txt), not in memory.

### 5.3 Tag taxonomy

One canonical list in `playbooks/tags.md`.

- Module tags: `preflight`, `ca`, `node`, `etcd`, `postgres`, `patroni`, `pgbouncer`, `haproxy`, `backup`, `monitoring`, `nginx_proxy`.
- Action tags: `install`, `config`, `restart`, `provision`.
- Combine: `--tags postgres,config` reloads PG config on the postgres group.

---

## 6 — Network exposure, firewalld, SELinux

### 6.1 Exposure model

Every service that is only consumed by another service on the same host binds to `network_loopback_address` and is not opened in firewalld. Every cross-host service binds to `network_any_address` and is firewalled to specific source groups via rich rules. In the default dual-stack mode these resolve to `127.0.0.1` and `0.0.0.0`; with `network.ip_version: ipv6` they resolve to `::1` and `::`. `nginx_proxy` is the only inbound for user-facing UIs.

**Monitor host:**

| Service | Listen | Firewall | Source |
|---|---|---|---|
| nginx_proxy | `network_any_address:80,443` | `http`, `https` | `operator_cidrs` |
| vmsingle | `network_any_address:8428` | `victoriametrics` | `postgres` + `monitor` |
| vlsingle | `network_any_address:9428` | `victorialogs` | `postgres` + `monitor` |
| vmalert | `network_loopback_address:8880` | — | local only |
| alertmanager | `network_loopback_address:9093` | — | local only |
| grafana | `network_loopback_address:3000` | — | local only |

**Postgres node:**

| Service | Listen | Firewall | Source |
|---|---|---|---|
| haproxy:5432/5433/5434 | `network_any_address` | `postgresql`, `haproxy-postgres` | `postgres_client_cidrs` |
| pgbouncer:6432 | `network_any_address` | `pgbouncer` (off by default — clients go via haproxy) | `postgres_client_cidrs` |
| haproxy stats:7000 | `network_loopback_address` | — | local |
| patroni REST:8008 | `network_any_address` | `patroni-rest` | `postgres` + `monitor` |
| etcd:2379, 2380 | `network_any_address` | `etcd-client`, `etcd-server` | `postgres`, `etcd` group |
| node_exporter:9100 | `network_any_address` | `prometheus-node-exporter` | `monitor` |
| postgres_exporter:9187 | `network_any_address` | `postgres-exporter` | `monitor` |
| pgbouncer_exporter:9127 | `network_any_address` | `pgbouncer-exporter` | `monitor` |
| pgbackrest_exporter:9854 | `network_any_address` | `pgbackrest-exporter` | `monitor` |
| vmagent:8429 | `network_loopback_address` | — | local |
| vlagent:9429 | `network_loopback_address` | — | local |

`nmap` from outside `operator_cidrs` sees only `22, 80, 443` on monitor host and only `22` on postgres hosts.

### 6.2 firewalld

Use built-in firewalld services where they exist and are actually opened: `ssh`, `http`, `https`, `postgresql`, `etcd-client`, `etcd-server`, `prometheus-node-exporter`. (firewalld also ships a `grafana` service, but we don't open it — Grafana binds loopback-only behind `nginx_proxy`.)

Ship custom XML only where firewalld lacks a definition AND we actually open the port:

```
files/firewalld/services/
├── patroni-rest.xml          # 8008
├── pgbouncer.xml             # 6432 (off by default)
├── haproxy-postgres.xml      # 5433 + 5434
├── victoriametrics.xml       # 8428
├── victorialogs.xml          # 9428
├── postgres-exporter.xml     # 9187
├── pgbouncer-exporter.xml    # 9127
└── pgbackrest-exporter.xml   # 9854
```

vmalert (8880), alertmanager (9093), grafana (3000), and haproxy stats (7000) all bind loopback-only behind `nginx_proxy` and have no firewalld entry. If a future operator wants to expose them directly, that's a v2 feature; we don't ship dormant XMLs for it.

Custom services install to `/etc/firewalld/services/` only — never `/usr/lib/firewalld/services/`. Source restrictions use firewalld rich rules driven by inventory groups.

### 6.3 SELinux

Stay enforcing. Never `setenforce 0`. For paths and ports outside vendor defaults, declare context persistently with `semanage` (via `community.general.sefcontext` and `community.general.seport`), then `restorecon`.

- Default discipline: prefer vendor paths (PG's `/var/lib/pgsql/<ver>/data`, etcd's `/var/lib/etcd`, pgbackrest's `/var/lib/pgbackrest`) so context is inherited.
- Explicit fcontext rules needed where we create new directories (`/etc/pki/etcd/`, `/etc/pki/nginx/`, custom postgres data dirs if operator overrides).
- Custom port labels needed for exporters and Patroni REST (port type `unreserved_port_t` or relevant typed port).
- Booleans: `httpd_can_network_connect` on monitor (nginx → loopback backends).
- Each role's `verify.yml` runs `ausearch -m AVC -ts boot` and fails on any AVC denial. No silent SELinux fudging.
- We do NOT auto-generate policy with `audit2allow`. Anything needing a custom policy module is a real design issue.

---

## 7 — Configuration and variables

### 7.1 Precedence (low → high)

1. role defaults (literals only)
2. group_vars (cross-role coordination)
3. inventory host vars (identity only)
4. `group_vars/response.yml` (generated; the operator-facing layer)
5. `--extra-vars` (emergency overrides; discouraged)

Operators edit the response file only. `group_vars/response.yml` carries a banner saying "regenerate via `./configure -f responses/site.rsp.yml`."

### 7.2 Naming

Every variable prefixed by role/domain: `postgres_*`, `patroni_*`, `etcd_*`, `pgbouncer_*`, `haproxy_*`, `pgbackrest_*`, `vmsingle_*`, `nginx_proxy_*`, `ca_*`, `cert_*`, `firewalld_*`, `selinux_*`, `operator_*`. Inventory identity variables: `ansible_host`, `postgres_role`, `postgres_seq`, `etcd_seq`. Nothing else in host_vars.

### 7.3 Response file (Oracle-`.rsp`-style)

```yaml
profile: ha
cluster:
  name: pg-prod
  domain: example.internal

nodes:
  # `role` here is the response-file role assignment, mapped by `configure`
  # to inventory groups + postgres_role. Allowed values:
  #   monitor          → group: monitor (and backup_store by default)
  #   backup_store     → group: backup_store (only if splitting from monitor)
  #   pg_primary       → group: postgres + etcd; postgres_role=primary
  #   pg_replica       → group: postgres + etcd; postgres_role=replica
  pgmon01:  { ip: 10.20.30.10, role: monitor }
  pgnode01: { ip: 10.20.30.11, role: pg_primary }
  pgnode02: { ip: 10.20.30.12, role: pg_replica }
  pgnode03: { ip: 10.20.30.13, role: pg_replica }

postgres:
  version: 18
  pin_version: ""                      # e.g., "18.3" to freeze on a minor
  port: 5432
  tune: oltp                           # oltp | olap | tiny
  shared_buffer_ratio: 0.25
  extra_parameters: {}                 # always-wins overrides
  extensions: [pg_stat_statements, pgvector]
  databases: [{ name: app, owner: app }]
  users:
    - { name: app, password: !vault | ..., roles: [dbrole_readwrite] }
  hba_rules:
    - { db: app, user: app, source: 10.20.40.0/24, method: scram-sha-256 }

pgbackrest:
  enabled: true
  schedule: { full: "0 1 * * 0", differential: "0 1 * * 1-6" }
  retention: { full: 4 }
  repo2:
    enabled: false
    # type: s3, bucket, endpoint, access_key, secret_key

tls:
  internal_ca: generate                # generate | existing | byo
  user_facing: { mode: ca_signed }     # ca_signed | byo | http

monitoring:
  vmsingle_retention: 90d
  vlsingle_retention: 30d
  alertmanager:
    receivers: [{ name: default, type: slack, webhook: !vault | ... }]

repos:
  pigsty: { enabled: false, packages: [] }

firewall:
  operator_cidrs: ["10.0.0.0/8"]
  postgres_client_cidrs: ["10.20.40.0/24"]
```

### 7.4 Secrets handling

| Category | Storage | Source |
|---|---|---|
| System-internal passwords (replicator, patroni REST, dbsu, monitor user) | `artifacts/credentials.txt` (0600, owner=invoker) | Auto-generated 32-char by `configure`, stable across runs |
| Operator-supplied (business user passwords, S3 keys, webhook URLs) | Response file, Ansible-Vault-encrypted blocks | Operator-supplied; `configure` accepts plaintext and offers to encrypt |
| TLS private keys | `pki/ca/ca.key`, `pki/certs/*` | Generated locally; never leave the control node |

Vault password supplied via `ANSIBLE_VAULT_PASSWORD_FILE` or `--ask-vault-pass`. We do not manage the vault password.

### 7.5 Postgres parameter management

Three layers:

1. **Tuning profile** (`postgres.tune: oltp|olap|tiny`) — curated ~30-param baseline from `roles/postgres/files/tuning/<profile>.conf`.
2. **Memory-derived params** computed at deploy time from `ansible_memtotal_mb` (`shared_buffers`, `effective_cache_size`, `work_mem`, `maintenance_work_mem`).
3. **`postgres.extra_parameters`** — operator overrides, always win.

No raw `postgres_parameters: {}` top-level dict. Operators discover params by reading `tuning/*.conf`.

### 7.6 pg_hba.conf

Rendered in order: system rules (postgres dbsu peer, local socket, cluster replication with cert auth), monitor rule, operator rules. Fully managed — hand edits revert.

### 7.7 CA and cert renewal

- `_ca.yml` runs on localhost. Generates `pki/ca/ca.key` (0600) + `pki/ca/ca.crt` (0644) using `community.crypto`. Idempotent.
- `certs` role: per-host private key generated on target, CSR signed centrally, distributed to `/etc/pki/<component>/`. Renews if `notAfter < cert_renewal_window` (default 30 days).
- BYO: set `tls.internal_ca: byo` and supply paths; `_ca.yml` and `certs` skipped.

---

## 8 — Data flow

### 8.1 First-time deploy

`make deploy` runs `site.yml`:

1. `_preflight` — validate OS, SELinux, mounts, block-device separation, time sync. Fail fast on issues.
2. `_ca` — generate self-signed CA on localhost if absent.
3. `_node` — repos, hostname, sysctl, firewalld baseline, distribute certs.
4. `_etcd` — install + configure; `etcdctl endpoint health` gate before continuing.
5. `_postgres_install` — PGDG packages.
6. `_postgres_bootstrap` — render Patroni config, start on primary, wait for leader, start replicas, poll until N members healthy.
7. `_pgbouncer`, `_haproxy`, `_vip_manager` (optional) — connection layer.
8. `_provision` — HBA, roles, dbs, extensions on primary.
9. `_backup_store` → `_backup_client` — repo first, then clients, then initial full backup.
10. `_monitoring_server` → `_monitoring_agents` → `_grafana` → `_nginx_proxy`.
11. Emit `artifacts/hosts.lite` and `artifacts/credentials.txt`.

Target runtime: under 10 minutes on `ha` profile with warm package cache.

### 8.2 Patroni HA control loop

Patroni instances coordinate via etcd. Leader holds a key with TTL=30s, renewed every 10s. Failure modes handled automatically:

- Primary PG crash → promote sync replica.
- Primary node crash → key expires → election.
- Network partition of primary → primary self-demotes (watchdog optional).
- Replica falls behind → marked unhealthy in REST → haproxy stops routing.

Target RTO under default `haproxy_rto_profile: norm`: ~45s end-to-end.

### 8.3 Client connection path

Apps connect to the PostgreSQL service on 5432. When vip-manager is enabled,
the canonical client address is the VIP: `VIP:5432` routes through HAProxy to
pgBouncer and then to PostgreSQL. HAProxy also exposes `VIP:5433` for explicit
RW traffic and `VIP:5434` for RO replica traffic. HAProxy runs on every
postgres node and binds both the configured default interface addresses and the
VIP addresses; IPv6 bind addresses are rendered in bracket form such as
`[2001:db8::20]:5432`. Linux non-local bind is enabled from
`/etc/sysctl.d/90-pigsty-lite-haproxy-vip.conf` so HAProxy can bind VIP
addresses before the local host owns them. vip-manager decides which node
receives packets by moving the VIP to the current Patroni leader. Raw
PostgreSQL remains on port 5432 for replication and local DBA checks, but it
listens on the node address plus loopback instead of wildcard so it does not
compete with the VIP service bind.

During failover, Patroni lets the old leader key expire, promotes a replica,
vip-manager attaches the VIP to the new leader, and existing client sessions
reconnect to the same `VIP:5432` endpoint. HAProxy health checks continue to use
Patroni REST (`/leader` for 5432/5433, `/replica` for 5434), while pgBouncer
pools connections to local PG. No client-visible port change is required.

### 8.4 Backup and PITR

- Continuous WAL archive: PG `archive_command` → pgbackrest ssh push to `backup_store` (async, batched).
- Scheduled: weekly full (Sun 01:00), daily differential (Mon-Sat 01:00), hourly expire, nightly check.
- Optional `repo2` — async push to S3-compatible store on each backup operation.
- PITR via `playbooks/restore.yml`: pause patroni → stop services → `pgbackrest restore --type=time` → reinit other replicas → resume.

### 8.5 Metrics and logs

vmagent on every node scrapes local exporters every 15s and `remote_write`s to `vmsingle` on monitor (disk-buffered if monitor down). vlagent tails journald + PG logs + Patroni logs to `vlsingle`. vmalert evaluates rules every 30s → alertmanager → slack/email/pagerduty per response file.

### 8.6 Re-running site.yml (idempotency)

Every role MUST be safe to re-run. Bootstrap tasks gate on Patroni REST cluster state, not on file existence. Templates compare content, not just modification time. Steady-state `make deploy` reports zero changes.

### 8.7 Error handling

| Failure | Behavior |
|---|---|
| Preflight check fails | Stop everything, print check + remediation |
| Package install fails | Fail with dnf error verbatim |
| etcd unhealthy at end of `_etcd` | Fail before Patroni starts |
| Patroni doesn't elect leader in `patroni_bootstrap_timeout` (default 5min) | Fail with `patronictl list` output |
| `_provision` SQL fails | Fail; operator inspects |
| `_backup_client` initial backup fails | Warn, do not fail (cluster up; alert will fire) |
| `_nginx_proxy` cert renewal fails | Warn, keep old cert; alert fires |
| Any runtime AVC denial | Fail with AVC details |

No silent fallbacks. No "try with TLS, fall back to plain."

---

## 9 — Operator workflows

### 9.1 First deploy

```bash
./configure                   # interactive: profile, IPs, passwords
make plan                     # ansible-playbook --check --diff
make deploy
```

### 9.2 Common day-2 changes

| Task | Steps |
|---|---|
| Add database | edit response, `make deploy` |
| Add user | edit response (with vault-encrypted password), `make deploy` |
| Change `max_connections` | edit `postgres.extra_parameters`, `make deploy` |
| Add a replica | edit `nodes` in response, `./configure -s -f responses/site.rsp.yml`, `make deploy` |
| Rotate dbsu password | delete entry from `artifacts/credentials.txt`, `./configure -s -f ...`, `make deploy` |
| Switch to BYO CA | edit `tls.internal_ca: byo` and paths, `make deploy` |
| Tune scrape interval | edit `monitoring.scrape_interval`, `make deploy` |
| Add SLO alert | drop YAML in `files/alerts/`, `make deploy` |

### 9.3 Lifecycle operations

- `make switchover` — controlled primary switchover (Patroni-driven).
- `make failover` — manual failover with operator confirmation.
- `make restore TARGET_TIME=...` — PITR.
- `make minor-upgrade` — rolling minor PG upgrade (see §10).
- `make scale-add-replica HOST=pgnode04` — add a replica.
- `make scale-remove-replica HOST=pgnode04` — remove a replica.

### 9.4 Portability

- `make export` — produces `dist/pigsty-lite-<cluster>-<UTC>.tar.gz.gpg`. AES256 symmetric, passphrase prompted. Whitelist-based bundle with SHA256 manifest. Includes response file, inventory, CA + certs, credentials.txt, custom alerts. EXCLUDES playbook code, roles, requirements, SSH keys, vault password.
- `make import FROM=path` — decrypts, verifies hashes, restores mode bits, refuses to overwrite a different cluster's CA without `--force`, prints sanity report.
- Prerequisites for a fresh control node are documented in README (operator installs `ansible-core`, `gpg`, `git`, `make`, runs `ansible-galaxy collection install -r requirements.yml`). No `make bootstrap-operator` target.

Bundle is one artifact; passphrase travels separately.

---

## 10 — Upgrades

### 10.1 Minor PG upgrade — automated

`playbooks/minor_upgrade.yml` (via `make minor-upgrade`):

1. Read current minor version per node.
2. Read target from `postgres.pin_version` or `postgres.version`.
3. Refuse if target major != current major.
4. Refuse if no successful backup within `postgres_minor_upgrade.require_recent_backup_hours` (default 24).
5. Per-replica, one at a time: patroni pause → stop services → `dnf update` → start services → wait for healthy member + replication lag <1MB → resume.
6. Switchover from current primary to a freshly-upgraded replica.
7. Repeat per-replica steps on the demoted old primary.
8. Verify cluster healthy.

Pinning: `postgres.pin_version: "18.3"` freezes the minor. Without pinning, `dnf update` picks latest available.

### 10.2 Major PG upgrade — runbook only

`docs/operations/major-upgrade.md` provides:

- Logical-replication cutover (zero-downtime) path with concrete copy/paste commands.
- `pg_upgrade` path with downtime estimate and rollback plan.

No playbook, no generator. DBA responsibility.

---

## 11 — Repos and packages

- **Default enabled:** PGDG (via `pgdg-redhat-repo` RPM), RHEL/Rocky/Alma vendor repos.
- **Opt-in:** EPEL — disabled by default, enabled only if a declared package needs it.
- **Opt-in:** Pigsty's upstream YUM repo — disabled by default. `repos.pigsty.packages` lists package names; the role enables the repo and `dnf install --enablerepo=pigsty <pkg>` for each.
- **No local repo builder.** Distros do this fine; airgap operators bring their own mirror.
- **Repo priority:** PGDG > vendor > EPEL > pigsty, enforced via `dnf-plugins-core` priority weights.

---

## 12 — Reuse of community collections

| Need | Source |
|---|---|
| VictoriaMetrics + VictoriaLogs | `victoriametrics.cluster` collection (official) |
| Grafana install | `grafana.grafana` collection (official) |
| Grafana config (datasources, dashboards) | `community.grafana` modules |
| PostgreSQL provisioning (users, dbs, extensions, hba, privs) | `community.postgresql` modules |
| TLS / CA / certs | `community.crypto` modules |
| firewalld | `ansible.posix.firewalld` |
| SELinux | `community.general.sefcontext`, `community.general.seport`, `ansible.posix.seboolean` |
| etcd | Own thin role, modeled on kubespray's etcd role |
| Patroni | Own thin role |
| HAProxy | Own thin role (single config template) |
| pgBouncer | Own thin role |
| pgBackRest (client + store) | Own thin roles |
| nginx | Own thin role (own config snippets) |

`requirements.yml` pins exact versions. CI runs against pinned versions only.

---

## 13 — Testing

Three layers, slow to fast.

### 13.1 Lint (seconds; GitHub CI on every push)

`make lint` runs: yamllint, ansible-lint (with custom rule banning `command:`/`shell:` without `changed_when`), variable-naming-convention checker, response-file JSON Schema validator, shellcheck, ruff, xmllint on firewalld XMLs, markdownlint.

### 13.2 Molecule per role (minutes per role; GitHub CI on PR for container-safe scenarios only)

One scenario per role: `prepare.yml` → `converge.yml` → `verify.yml` → `cleanup.yml`. Drivers split by need:

- **Podman + init containers** for roles that don't need real systemd quirks or real firewalld (preflight, repos, ca, certs, monitoring_agents config, monitoring_server config, grafana config, nginx_proxy config). These run on GitHub Actions.
- **Libvirt** for roles that need real systemd, real firewalld, real SELinux (etcd, postgres, patroni, pgbouncer, haproxy, backup_client, backup_store). These are local-only and excluded from GitHub matrices.

Every Molecule scenario runs `converge.yml` twice and asserts the second run is zero-change (idempotency). Every `verify.yml` runs `ausearch -m AVC -ts boot` and fails on AVC denials.

### 13.3 Integration on libvirt VMs (~30-45 min; LOCAL ONLY, never in GitHub CI)

`make test-integration PROFILE=ha` spins up a 4-VM libvirt environment (3 postgres + 1 monitor), runs `./configure -c ha -s -f tests/integration/ha.rsp.yml`, runs `make deploy`, then exercises:

- Connectivity (psql to VIP/HAProxy, port 5433 read/write, 5434 read-only replication check).
- Patroni state (`patronictl list` matches expected).
- etcd quorum (`etcdctl endpoint status --cluster`).
- Backups (manual full backup; `pgbackrest info` shows fresh entry; force WAL switch and check archive within 60s).
- Monitoring (grafana `/api/health`; `pg_up{cluster=...}==1` for every postgres host).
- Logs (psql generates known line; visible in vlsingle within 30s).
- Firewall (nmap from outside `operator_cidrs` to monitor sees only 22/80/443; nmap to postgres node from non-`postgres_client_cidrs` sees only 22).
- SELinux (`ausearch -m AVC -ts boot` per host: zero).
- Idempotency (re-run `make deploy`: zero changed tasks).

Additional integration scenarios: `single-profile`, `split-infra`, `byo-tls`, `repos-pigsty`, `import-export` (round-trip), `minor-upgrade-rolling`, `minor-upgrade-backup-gate`.

Chaos suite (opt-in, `make test-chaos`): stop primary PG; iptables-DROP primary; kill etcd member; fill data disk; reboot primary. Each chaos test has its own cleanup.

### 13.4 CI vs local responsibility

- GitHub CI runs: lint + Molecule container-safe matrix.
- Local beefy machines run: full Molecule (including libvirt) + integration + chaos.
- Release tags require an operator-attached local integration report.

### 13.5 Acceptance gates (merge)

`make lint` clean; affected roles' Molecule scenarios green; idempotency green; no new ansible-lint warnings.

### 13.6 Release gates

Above plus: `make test-integration PROFILE=single` and `PROFILE=ha` green locally; chaos suite green locally; `make export`/`make import` round-trip green; manual smoke of interactive `./configure`.

### 13.7 Out of test scope (v1)

Cross-major-version upgrades, pigsty→pigsty-lite migration, cloud Terraform glue, performance benchmark gates, multi-region failover, large-scale backup restore (>100GB).

---

## 14 — Open questions and risks

- **community.postgresql module version compatibility with PG 18.** The collection generally tracks PG releases promptly; pin and verify in `requirements.yml`. Mitigation: integration test against PG 18 from PGDG.
- **etcd RPM availability and quality on RHEL 10.** Verify before committing; fall back to upstream tarball install only if no clean RPM path exists (would require an additional role pattern for binary download + systemd unit; kubespray's role can serve as reference for that mode too).
- **pgbackrest_exporter** is a third-party project; verify its packaging story on RHEL 10 (PGDG RPM, EPEL, or build-from-source).
- **VictoriaLogs maturity** for Grafana dashboards covering PG logs at the level Loki+promtail provides today — verify in integration tests; if gaps exist, document them in `docs/architecture.md` rather than abandoning the choice.
- **firewalld custom service names** must not collide with future firewalld upstream additions. Mitigation: scoped prefix (`haproxy-postgres`, `victoriametrics`, `victorialogs`) and a release-checklist item to diff our service names against the current firewalld services directory before tagging.

---

## 15 — What we deliberately discard from pigsty

- The `/pg/*` paths (custom symlinks) and the implicit `setenforce 0` they require.
- The monolithic 15-file `pgsql/` role.
- Identity computation inside roles (`pg_id`, `node_id`).
- The wizard-mutates-pigsty.yml-in-place flow.
- Local YUM mirror builder.
- MinIO, Redis, MongoDB, FerretDB, MSSQL/MySQL compat modes, Greenplum, Citus, Polar, AgensGraph.
- Docker + app marketplace + pgAdmin deployment.
- dnsmasq.
- Vector → replaced by vlagent.
- pgsodium key management.
- Custom `pig` CLI and `bin/` shell scripts.
- Tags-as-orchestration via `tags: always`.
- Mixed managed/unmanaged sections in HBA.
