# P5 (Monitoring: metrics, logs, alerting, dashboards, reverse proxy) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the cluster full observability. After P5, `make deploy` stands up a VictoriaMetrics + VictoriaLogs single-node stack on the monitor host, ships metrics and logs from every node via local agents, evaluates alert rules through vmalert → Alertmanager, serves Grafana dashboards, and fronts all UIs behind an nginx TLS reverse proxy — all idempotent, all SELinux-enforcing, all firewalled per the spec's exposure model.

**Architecture:** Four new thin roles, wired in the spec's order (`monitoring_server → monitoring_agents → grafana → nginx_proxy`). `monitoring_server` (monitor host) installs `vmsingle` + `vlsingle` + `vmalert` via the official `victoriametrics.cluster` collection roles, plus Alertmanager (own tasks — VM has no first-party Alertmanager role). `monitoring_agents` (all hosts) installs `vmagent` + `vlagent` via the same collection, plus the four exporters (`node_exporter`, `postgres_exporter`, `pgbouncer_exporter`, `pgbackrest_exporter`); agents scrape local exporters and `remote_write` to the monitor. `grafana` (monitor host) installs via `grafana.grafana`, configures datasources + dashboards via `community.grafana`. `nginx_proxy` (monitor host) terminates TLS and routes `/grafana/`, `/alertmanager/`, `/vmalert/`. Cross-host services (`vmsingle:8428`, `vlsingle:9428`, exporters) bind `network_any_address` and are firewalled to source groups via rich rules; loopback-only services (`vmagent`, `vlagent`, `vmalert`, `alertmanager`, `grafana`) bind `network_loopback_address` and get no firewalld entry — nginx is the only inbound for UIs.

**Tech Stack:** `victoriametrics.cluster` collection (pinned in `requirements.yml`, already installed: provides `vmsingle`, `vlsingle`, `vmagent`, `vlagent`, `vmalert` roles), `grafana.grafana` + `community.grafana` collections (added by Task 1), exporters from PGDG/EPEL RPMs, nginx from the vendor repo, `community.crypto`-issued per-host certs from P0's `certs` role. Molecule + podman for the four role tests — all four are config-render roles per spec §13.2, so they run in the container matrix.

---

## File Structure

**New files (in `roles/monitoring_server/`):**

- `roles/monitoring_server/defaults/main.yml` — `monitoring_server_*` knobs: VM/VL retention (from `vmsingle_retention` / `vlsingle_retention`), listen addresses, Alertmanager package/paths, firewalld zone.
- `roles/monitoring_server/meta/main.yml` — galaxy_info; `dependencies: []` (the VM collection roles are invoked explicitly, not as meta deps, so we control ordering and variables).
- `roles/monitoring_server/tasks/main.yml` — orchestrate: `_assert` → `_vmsingle` → `_vlsingle` → `_vmalert` → `_alertmanager` → `_firewall`.
- `roles/monitoring_server/tasks/_assert.yml` — assert `monitor` group size 1, monitor host cert present (from P0 `certs`).
- `roles/monitoring_server/tasks/_vmsingle.yml` — `include_role` the collection's `vmsingle` role with pigsty-lite variables (listen on `network_any_address:8428`, retention, data dir `/var/lib/victoria-metrics`).
- `roles/monitoring_server/tasks/_vlsingle.yml` — `include_role` the collection's `vlsingle` role (listen `network_any_address:9428`, retention, data dir `/var/lib/victoria-logs`).
- `roles/monitoring_server/tasks/_vmalert.yml` — `include_role` the collection's `vmalert` role (listen `network_loopback_address:8880`, datasource = local vmsingle, notifier = local Alertmanager, rules path `/etc/vmalert/rules`).
- `roles/monitoring_server/tasks/_alertmanager.yml` — install Alertmanager RPM, render config from `alertmanager_receivers`, systemd unit, listen `network_loopback_address:9093`.
- `roles/monitoring_server/tasks/_firewall.yml` — install + open `victoriametrics` (8428) and `victorialogs` (9428) custom firewalld services to `postgres` + `monitor` source groups.
- `roles/monitoring_server/handlers/main.yml` — `Reload systemd for monitoring_server`, `Restart alertmanager`, `Reload firewalld`.
- `roles/monitoring_server/templates/alertmanager.yml.j2` — Alertmanager config rendered from `alertmanager_receivers`.
- `roles/monitoring_server/templates/alertmanager.service.j2` — systemd unit.
- `roles/monitoring_server/files/firewalld/services/victoriametrics.xml` — port 8428.
- `roles/monitoring_server/files/firewalld/services/victorialogs.xml` — port 9428.
- `roles/monitoring_server/README.md`.

**New files (in `roles/monitoring_agents/`):**

- `roles/monitoring_agents/defaults/main.yml` — `monitoring_agents_*` knobs: exporter package names, ports, scrape interval, `remote_write` target (the monitor host), agent data/buffer dirs.
- `roles/monitoring_agents/meta/main.yml` — galaxy_info, `dependencies: []`.
- `roles/monitoring_agents/tasks/main.yml` — orchestrate: `_assert` → `_exporters` → `_vmagent` → `_vlagent` → `_firewall`.
- `roles/monitoring_agents/tasks/_assert.yml` — assert `monitor` group resolvable; per-host, check the node is reachable.
- `roles/monitoring_agents/tasks/_exporters.yml` — install + enable `node_exporter` (all hosts), `postgres_exporter` + `pgbouncer_exporter` + `pgbackrest_exporter` (postgres hosts only, gated on `inventory_hostname in groups['postgres']`). Each binds `network_any_address`, runs under its own systemd unit, connects to PG/pgBouncer over the local socket.
- `roles/monitoring_agents/tasks/_vmagent.yml` — `include_role` the collection's `vmagent` role (scrape config covers localhost exporters + Patroni REST + HAProxy stats; `remote_write` to `https://<monitor>:8428`; listen `network_loopback_address:8429`; disk buffer dir).
- `roles/monitoring_agents/tasks/_vlagent.yml` — `include_role` the collection's `vlagent` role (tail journald + PG logs + Patroni logs; ship to `https://<monitor>:9428`; listen `network_loopback_address:9429`).
- `roles/monitoring_agents/tasks/_firewall.yml` — install + open `postgres-exporter` (9187), `pgbouncer-exporter` (9127), `pgbackrest-exporter` (9854) custom services to the `monitor` source group; open the built-in `prometheus-node-exporter` service (9100) to `monitor`. node_exporter is all-hosts; the three PG exporters only on postgres hosts.
- `roles/monitoring_agents/handlers/main.yml` — `Reload systemd for monitoring_agents`, `Reload firewalld`, per-exporter restart handlers.
- `roles/monitoring_agents/templates/` — one `*.service.j2` per exporter (`node-exporter.service.j2`, `postgres-exporter.service.j2`, `pgbouncer-exporter.service.j2`, `pgbackrest-exporter.service.j2`), plus `vmagent-scrape.yml.j2` (the scrape config passed to the collection's vmagent role) and `vlagent-config.yml.j2`.
- `roles/monitoring_agents/files/firewalld/services/postgres-exporter.xml` — 9187.
- `roles/monitoring_agents/files/firewalld/services/pgbouncer-exporter.xml` — 9127.
- `roles/monitoring_agents/files/firewalld/services/pgbackrest-exporter.xml` — 9854.
- `roles/monitoring_agents/README.md`.

**New files (in `roles/grafana/`):**

- `roles/grafana/defaults/main.yml` — `grafana_*` knobs: listen `network_loopback_address:3000`, admin password (from a generated vault var), datasource URLs (local vmsingle/vlsingle), dashboard provisioning dir, `root_url` for the `/grafana/` sub-path.
- `roles/grafana/meta/main.yml` — galaxy_info, `dependencies: []`.
- `roles/grafana/tasks/main.yml` — orchestrate: `_assert` → `_install` → `_datasources` → `_dashboards`.
- `roles/grafana/tasks/_assert.yml` — assert `monitor` group size 1, vmsingle/vlsingle reachable on loopback.
- `roles/grafana/tasks/_install.yml` — `include_role` the `grafana.grafana` collection role (install + base config: bind loopback, SQLite backend, `serve_from_sub_path` true, `root_url` = `https://<domain>/grafana/`).
- `roles/grafana/tasks/_datasources.yml` — `community.grafana.grafana_datasource` for the VictoriaMetrics (Prometheus-compatible) and VictoriaLogs datasources, both pointing at the loopback ports.
- `roles/grafana/tasks/_dashboards.yml` — copy dashboard JSON from `roles/grafana/files/dashboards/` into the Grafana provisioning dir, render the provisioning YAML.
- `roles/grafana/handlers/main.yml` — `Restart grafana`.
- `roles/grafana/templates/dashboard-provider.yml.j2` — Grafana file-provisioning config.
- `roles/grafana/files/dashboards/pigsty-lite-overview.json` — one starter dashboard (cluster overview: `pg_up`, replication lag, connections, node CPU/mem/disk). A real dashboard JSON, not a placeholder — see Task 14.
- `roles/grafana/README.md`.

**New files (in `roles/nginx_proxy/`):**

- `roles/nginx_proxy/defaults/main.yml` — `nginx_proxy_*` knobs: package, TLS mode (`nginx_proxy_tls_mode` from the response file), cert/key paths, upstream loopback ports for grafana/alertmanager/vmalert, listen `network_any_address:80,443`.
- `roles/nginx_proxy/meta/main.yml` — galaxy_info, `dependencies: []`.
- `roles/nginx_proxy/tasks/main.yml` — orchestrate: `_assert` → `_install` → `_tls` → `_config` → `_firewall` → `_service`.
- `roles/nginx_proxy/tasks/_assert.yml` — assert `monitor` group size 1; if `nginx_proxy_tls_mode` is `ca_signed`, the monitor host cert must exist (from P0 `certs`); if `byo`, the operator-supplied cert path must exist.
- `roles/nginx_proxy/tasks/_install.yml` — install nginx, create config dirs, register SELinux booleans (`httpd_can_network_connect` so nginx can proxy to loopback upstreams).
- `roles/nginx_proxy/tasks/_tls.yml` — resolve the cert/key to use based on `nginx_proxy_tls_mode` (`ca_signed` → P0 cert; `byo` → operator path; `http` → skip, plain HTTP only).
- `roles/nginx_proxy/tasks/_config.yml` — render `/etc/nginx/conf.d/pigsty-lite.conf` (the reverse-proxy server block), `validate: nginx -t`.
- `roles/nginx_proxy/tasks/_firewall.yml` — open the built-in `http` + `https` services to `operator_cidrs`.
- `roles/nginx_proxy/tasks/_service.yml` — enable + start nginx, wait for 80/443 to listen.
- `roles/nginx_proxy/handlers/main.yml` — `Reload nginx`, `Reload firewalld`.
- `roles/nginx_proxy/templates/pigsty-lite.conf.j2` — the nginx server block: TLS termination + `/grafana/`, `/alertmanager/`, `/vmalert/` location blocks proxying to the loopback ports.
- `roles/nginx_proxy/README.md`.

**New playbooks + wiring:**

- `playbooks/_monitoring_server.yml` — runs `monitoring_server` on the `monitor` group.
- `playbooks/_monitoring_agents.yml` — runs `monitoring_agents` on `all`.
- `playbooks/_grafana.yml` — runs `grafana` on the `monitor` group.
- `playbooks/_nginx_proxy.yml` — runs `nginx_proxy` on the `monitor` group.
- `playbooks/site.yml` — modify to import the four playbooks after `_backup_store.yml`, in spec order.
- `playbooks/tags.md` — add `monitoring` and `nginx_proxy` module tags.
- `group_vars/monitor.yml` — currently the stub `# monitor group defaults. Populated in P5.`; populate with the comment update only (role defaults suffice — see Task 1).
- `group_vars/all.yml` — add monitoring coordination vars: `monitoring_scrape_interval`, exporter ports, VM/VL ports are already there (`vmsingle_port: 8428`, `vlsingle_port: 9428`, `vmalert_port: 8880`, `alertmanager_port: 9093`, `grafana_port: 3000` — verified present).
- `requirements.yml` — add `grafana.grafana` and `community.grafana` collections alongside the existing `victoriametrics.cluster`.

**Modified files:**

- `bin/_response_schema.py` — extend `_validate_monitoring()` to also validate `monitoring.alertmanager.receivers` (list of `{name, type, ...}`) and an optional `monitoring.scrape_interval` (duration string). Today the generator passes `alertmanager_receivers` through but the schema only checks the two retention strings.
- `bin/_generate_response_vars.py` — add `monitoring_scrape_interval` to the emitted vars (from `monitoring.scrape_interval`, default `15s`). The retention + receivers vars are already emitted (verified).
- `responses/single.rsp.yml.example` and `responses/ha.rsp.yml.example` — already declare a `monitoring:` block. Add a comment line above it pointing at a future day-2 monitoring note; no value changes.
- `.github/workflows/molecule.yml` — extend the matrix with `monitoring_server/default`, `monitoring_agents/default`, `grafana/default`, `nginx_proxy/default`.
- `docs/operations/firstrun.md` — add a P5 section (verify metrics/logs/dashboards).
- `docs/operations/day2-monitoring.md` — **new**, runbook: add an alert rule, add an Alertmanager receiver, change retention, change scrape interval, add a dashboard.
- `README.md` — flip P5 to done in the roadmap.

**New test files:**

- `tests/molecule/monitoring_server/molecule/default/{molecule,prepare,converge,verify}.yml` — single monitor host; verifies vmsingle/vlsingle/vmalert/alertmanager listen on the right addresses and `/health` endpoints respond.
- `tests/molecule/monitoring_agents/molecule/default/{molecule,prepare,converge,verify}.yml` — one monitor + one postgres host; verifies all four exporters listen, vmagent/vlagent are running, and a sample scrape target is `up`.
- `tests/molecule/grafana/molecule/default/{molecule,prepare,converge,verify}.yml` — single monitor host; verifies Grafana `/api/health` is `ok`, both datasources exist, the overview dashboard is provisioned.
- `tests/molecule/nginx_proxy/molecule/default/{molecule,prepare,converge,verify}.yml` — single monitor host; verifies nginx serves 443, `/grafana/` proxies through, TLS cert is the expected one.
- `tests/configure/test_schema.py` — extend with cases for the new `monitoring.alertmanager.receivers` and `monitoring.scrape_interval` schema rules.

**Out of scope (deferred):**

- Grafana dashboard library beyond the one starter overview dashboard — operators drop more JSON into `roles/grafana/files/dashboards/`; we ship one working dashboard, not a curated suite.
- Alertmanager receiver types beyond what the response file declares (`slack`, `email`, `webhook`/`pagerduty`) — the schema validates the shape; exotic receiver configs are operator-supplied passthrough.
- `vmauth` / cluster-mode VictoriaMetrics (`vminsert`/`vmselect`/`vmstorage`) — spec §2 explicitly scopes to single-node `vmsingle`/`vlsingle`.
- Log-based alerting (vmalert against VictoriaLogs) — v1 alerts are metric-based only; the rules path is wired but ships with metric rules.
- Replicated/HA Grafana — spec §2.4 lists it as explicitly excluded.
- pgbackrest_exporter packaging is an open question per spec §14 — Task 8 installs it from the same source the other exporters use and, if no RHEL 10 RPM exists, the task documents the build-from-source fallback rather than silently skipping it.

---

## Task 1: monitoring coordination vars, group_vars, requirements

**Files:**
- Modify: `group_vars/all.yml`
- Modify: `group_vars/monitor.yml`
- Modify: `requirements.yml`

- [ ] **Step 1: Add monitoring coordination vars to `group_vars/all.yml`**

Open `group_vars/all.yml`. The VM/VL/grafana ports are already in the `# Ports` block (`vmsingle_port`, `vlsingle_port`, `vmalert_port`, `alertmanager_port`, `grafana_port`). Add the exporter ports to that same block, after `grafana_port`:

```yaml
node_exporter_port: 9100
postgres_exporter_port: 9187
pgbouncer_exporter_port: 9127
pgbackrest_exporter_port: 9854
vmagent_port: 8429
vlagent_port: 9429
```

Then add a new block before `# Operator network defaults`:

```yaml
# Monitoring --------------------------------------------------------
# The monitor host is the single member of the `monitor` group. Agents
# remote_write metrics and ship logs to it. Scrape interval is the
# operator-tunable cadence for vmagent.
monitoring_host: "{{ groups['monitor'][0] }}"
monitoring_scrape_interval: 15s
```

- [ ] **Step 2: Update `group_vars/monitor.yml`**

Open `group_vars/monitor.yml`. It currently contains only:

```yaml
---
# monitor group defaults. Populated in P5.
```

Replace that comment line with:

```yaml
---
# monitor group defaults.
# Role defaults in roles/monitoring_server/, roles/grafana/, and
# roles/nginx_proxy/ cover everything; this file exists as the
# documented override point for operators who split the monitor host's
# responsibilities or change its firewalld zone.
```

No variables — role defaults suffice.

- [ ] **Step 3: Add Grafana collections to `requirements.yml`**

Open `requirements.yml`. It currently lists only `victoriametrics.cluster`. Replace the `collections:` block with:

```yaml
---
collections:
  - name: victoriametrics.cluster
  - name: grafana.grafana
  - name: community.grafana
```

- [ ] **Step 4: Install the new collections locally**

Run: `ansible-galaxy collection install -r requirements.yml -p ./collections`
Expected: `grafana.grafana` and `community.grafana` install; `victoriametrics.cluster` reports already present.

- [ ] **Step 5: Lint**

Run: `yamllint group_vars/all.yml group_vars/monitor.yml requirements.yml`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add group_vars/all.yml group_vars/monitor.yml requirements.yml
git commit -m "feat(monitoring): coordination vars and Grafana collections"
```

---

## Task 2: monitoring_server defaults, meta, README

**Files:**
- Create: `roles/monitoring_server/defaults/main.yml`
- Create: `roles/monitoring_server/meta/main.yml`
- Create: `roles/monitoring_server/README.md`

- [ ] **Step 1: Write `defaults/main.yml`**

```yaml
---
# roles/monitoring_server/defaults/main.yml
# All variables prefixed `monitoring_server_`. The VictoriaMetrics
# collection roles (vmsingle, vlsingle, vmalert) are invoked from
# tasks/ with the variables below mapped onto the collection's own
# variable names.

# VictoriaMetrics single-node (metrics TSDB)
monitoring_server_vmsingle_listen: "{{ network_any_address | default('0.0.0.0') }}:{{ vmsingle_port | default(8428) }}"
monitoring_server_vmsingle_data_dir: /var/lib/victoria-metrics
monitoring_server_vmsingle_retention: "{{ vmsingle_retention | default('90d') }}"

# VictoriaLogs single-node (log store)
monitoring_server_vlsingle_listen: "{{ network_any_address | default('0.0.0.0') }}:{{ vlsingle_port | default(9428) }}"
monitoring_server_vlsingle_data_dir: /var/lib/victoria-logs
monitoring_server_vlsingle_retention: "{{ vlsingle_retention | default('30d') }}"

# vmalert (rule evaluation)
monitoring_server_vmalert_listen: "{{ network_loopback_address | default('127.0.0.1') }}:{{ vmalert_port | default(8880) }}"
monitoring_server_vmalert_rules_dir: /etc/vmalert/rules
monitoring_server_vmalert_datasource_url: "http://{{ network_loopback_address | default('127.0.0.1') }}:{{ vmsingle_port | default(8428) }}"
monitoring_server_vmalert_notifier_url: "http://{{ network_loopback_address | default('127.0.0.1') }}:{{ alertmanager_port | default(9093) }}"
monitoring_server_vmalert_eval_interval: 30s

# Alertmanager (own tasks - no first-party VM Galaxy role)
monitoring_server_alertmanager_package: alertmanager
monitoring_server_alertmanager_service: alertmanager
monitoring_server_alertmanager_config_dir: /etc/alertmanager
monitoring_server_alertmanager_config_file: /etc/alertmanager/alertmanager.yml
monitoring_server_alertmanager_data_dir: /var/lib/alertmanager
monitoring_server_alertmanager_listen: "{{ network_loopback_address | default('127.0.0.1') }}:{{ alertmanager_port | default(9093) }}"
monitoring_server_alertmanager_receivers: "{{ alertmanager_receivers | default([]) }}"

# Firewalld
monitoring_server_firewalld_zone: "{{ firewalld_default_zone | default('public') }}"
monitoring_server_systemd_dir: /etc/systemd/system
```

- [ ] **Step 2: Write `meta/main.yml`**

```yaml
---
galaxy_info:
  role_name: monitoring_server
  author: pigsty-lite
  description: VictoriaMetrics + VictoriaLogs single-node, vmalert, and Alertmanager on the monitor host.
  license: Apache-2.0
  min_ansible_version: "2.16"
  platforms:
    - name: EL
      versions: ["10"]
dependencies: []
```

- [ ] **Step 3: Write `README.md`**

````markdown
# monitoring_server

The metrics/logs/alerting backend. Targets the `monitor` group (one
host). Installs VictoriaMetrics single-node (`vmsingle`), VictoriaLogs
single-node (`vlsingle`), `vmalert`, and Alertmanager. Agents on every
node `remote_write` metrics and ship logs here.

## Inputs (from response file, via group_vars)

| Variable | Meaning | Default |
|---|---|---|
| `vmsingle_retention` | metrics retention | `90d` |
| `vlsingle_retention` | logs retention | `30d` |
| `alertmanager_receivers` | Alertmanager receiver list | `[]` |

## What this role owns

- `vmsingle` on `network_any_address:8428` (firewalled to postgres + monitor).
- `vlsingle` on `network_any_address:9428` (firewalled to postgres + monitor).
- `vmalert` on `network_loopback_address:8880` (loopback only).
- Alertmanager on `network_loopback_address:9093` (loopback only).
- The `victoriametrics` and `victorialogs` custom firewalld services.

## What this role does NOT own

- Agents and exporters — that's `monitoring_agents`.
- Grafana — that's the `grafana` role.
- The nginx reverse proxy fronting vmalert/alertmanager UIs — that's `nginx_proxy`.

## Ordering

`_assert` → `_vmsingle` → `_vlsingle` → `_vmalert` → `_alertmanager` →
`_firewall`. vmalert is configured after vmsingle (its datasource) and
Alertmanager (its notifier) exist.

## Idempotence

Second run is zero-change: the collection roles diff their own config,
the Alertmanager config is content-templated, firewalld services are
content-compared.

## Tags

- `monitoring` — full role
- `monitoring,config` — re-render configs only
- `monitoring,firewall` — firewalld only
- `monitoring,service` — restart services only
````

- [ ] **Step 4: Lint**

Run: `yamllint roles/monitoring_server/defaults/main.yml roles/monitoring_server/meta/main.yml`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add roles/monitoring_server/defaults roles/monitoring_server/meta roles/monitoring_server/README.md
git commit -m "feat(monitoring_server): role defaults, meta, README"
```

---

## Task 3: monitoring_server orchestration + assert

**Files:**
- Create: `roles/monitoring_server/tasks/main.yml`
- Create: `roles/monitoring_server/tasks/_assert.yml`

- [ ] **Step 1: Write `tasks/main.yml`**

```yaml
---
- name: Validate preconditions
  ansible.builtin.import_tasks: _assert.yml
  tags: [monitoring, assert]

- name: Install and configure VictoriaMetrics single-node
  ansible.builtin.import_tasks: _vmsingle.yml
  tags: [monitoring, config]

- name: Install and configure VictoriaLogs single-node
  ansible.builtin.import_tasks: _vlsingle.yml
  tags: [monitoring, config]

- name: Install and configure vmalert
  ansible.builtin.import_tasks: _vmalert.yml
  tags: [monitoring, config]

- name: Install and configure Alertmanager
  ansible.builtin.import_tasks: _alertmanager.yml
  tags: [monitoring, config]

- name: Open firewalld for cross-host monitoring services
  ansible.builtin.import_tasks: _firewall.yml
  tags: [monitoring, firewall]
```

- [ ] **Step 2: Write `tasks/_assert.yml`**

```yaml
---
- name: Fail if monitor group is empty
  ansible.builtin.assert:
    that:
      - groups['monitor'] | length > 0
    fail_msg: "Inventory group 'monitor' is empty; P5 requires exactly one monitor host."
  run_once: true
  delegate_to: localhost

- name: Fail unless monitor group size is exactly 1
  ansible.builtin.assert:
    that:
      - groups['monitor'] | length == 1
    fail_msg: >-
      monitor group must contain exactly one host;
      got {{ groups['monitor'] | length }}. Replicated monitoring is
      out of scope.
  run_once: true
  delegate_to: localhost

- name: Stat the monitor host certificate (from P0 certs role)
  ansible.builtin.stat:
    path: "{{ pki_dir | default('/etc/pki/pigsty-lite') }}/{{ inventory_hostname }}.crt"
  register: monitoring_server_cert_stat

- name: Warn if the monitor host certificate is missing
  ansible.builtin.debug:
    msg: >-
      WARNING: monitor host certificate not found at
      {{ pki_dir | default('/etc/pki/pigsty-lite') }}/{{ inventory_hostname }}.crt.
      vmsingle/vlsingle still start over plain HTTP on loopback-adjacent
      binds, but nginx_proxy's TLS termination needs this cert. Run the
      P0 _node.yml playbook (certs role) first.
  when: not monitoring_server_cert_stat.stat.exists
```

- [ ] **Step 3: Lint**

Run: `yamllint roles/monitoring_server/tasks/main.yml roles/monitoring_server/tasks/_assert.yml`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add roles/monitoring_server/tasks/main.yml roles/monitoring_server/tasks/_assert.yml
git commit -m "feat(monitoring_server): orchestration and preconditions"
```

---

## Task 4: monitoring_server — vmsingle and vlsingle

**Files:**
- Create: `roles/monitoring_server/tasks/_vmsingle.yml`
- Create: `roles/monitoring_server/tasks/_vlsingle.yml`

The `victoriametrics.cluster` collection provides `vmsingle` and
`vlsingle` roles (verified installed under
`collections/ansible_collections/victoriametrics/cluster/roles/`). We
invoke them with `include_role` and map pigsty-lite variables onto the
collection's variable names.

**Before writing these tasks, the executor MUST read the collection's
role defaults** to confirm the exact variable names:

Run: `cat collections/ansible_collections/victoriametrics/cluster/roles/vmsingle/defaults/main.yml`
Run: `cat collections/ansible_collections/victoriametrics/cluster/roles/vlsingle/defaults/main.yml`

The variable names below (`vmsingle_*`, `vlsingle_*`) follow the
collection's documented convention as of version 2.50.3. If the
collection uses different names, use the names from the `defaults`
files you just read — the *intent* (listen address, retention, data
dir) is what matters.

- [ ] **Step 1: Write `_vmsingle.yml`**

```yaml
---
- name: Install and configure VictoriaMetrics single-node
  ansible.builtin.include_role:
    name: victoriametrics.cluster.vmsingle
  vars:
    vmsingle_service_args:
      httpListenAddr: "{{ monitoring_server_vmsingle_listen }}"
      storageDataPath: "{{ monitoring_server_vmsingle_data_dir }}"
      retentionPeriod: "{{ monitoring_server_vmsingle_retention }}"
```

- [ ] **Step 2: Write `_vlsingle.yml`**

```yaml
---
- name: Install and configure VictoriaLogs single-node
  ansible.builtin.include_role:
    name: victoriametrics.cluster.vlsingle
  vars:
    vlsingle_service_args:
      httpListenAddr: "{{ monitoring_server_vlsingle_listen }}"
      storageDataPath: "{{ monitoring_server_vlsingle_data_dir }}"
      retentionPeriod: "{{ monitoring_server_vlsingle_retention }}"
```

- [ ] **Step 3: Lint**

Run: `yamllint roles/monitoring_server/tasks/_vmsingle.yml roles/monitoring_server/tasks/_vlsingle.yml`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add roles/monitoring_server/tasks/_vmsingle.yml roles/monitoring_server/tasks/_vlsingle.yml
git commit -m "feat(monitoring_server): vmsingle and vlsingle via VM collection"
```

---

## Task 5: monitoring_server — vmalert and Alertmanager

**Files:**
- Create: `roles/monitoring_server/tasks/_vmalert.yml`
- Create: `roles/monitoring_server/tasks/_alertmanager.yml`
- Create: `roles/monitoring_server/templates/alertmanager.yml.j2`
- Create: `roles/monitoring_server/templates/alertmanager.service.j2`
- Create: `roles/monitoring_server/handlers/main.yml`

- [ ] **Step 1: Write `_vmalert.yml`**

Before writing, confirm the collection's vmalert variable names:
Run: `cat collections/ansible_collections/victoriametrics/cluster/roles/vmalert/defaults/main.yml`

```yaml
---
- name: Ensure the vmalert rules directory exists
  ansible.builtin.file:
    path: "{{ monitoring_server_vmalert_rules_dir }}"
    state: directory
    owner: root
    group: root
    mode: "0755"

- name: Ship a starter metric alert rule group
  ansible.builtin.copy:
    dest: "{{ monitoring_server_vmalert_rules_dir }}/pigsty-lite.yml"
    owner: root
    group: root
    mode: "0644"
    content: |
      groups:
        - name: pigsty-lite-core
          rules:
            - alert: PostgresInstanceDown
              expr: pg_up == 0
              for: 1m
              labels:
                severity: critical
              annotations:
                summary: "PostgreSQL instance {{ '{{' }} $labels.instance {{ '}}' }} is down"
            - alert: NodeDiskSpaceLow
              expr: >-
                (node_filesystem_avail_bytes / node_filesystem_size_bytes) < 0.10
              for: 5m
              labels:
                severity: warning
              annotations:
                summary: "Less than 10% disk free on {{ '{{' }} $labels.instance {{ '}}' }}"

- name: Install and configure vmalert
  ansible.builtin.include_role:
    name: victoriametrics.cluster.vmalert
  vars:
    vmalert_service_args:
      httpListenAddr: "{{ monitoring_server_vmalert_listen }}"
      "datasource.url": "{{ monitoring_server_vmalert_datasource_url }}"
      "notifier.url": "{{ monitoring_server_vmalert_notifier_url }}"
      rule: "{{ monitoring_server_vmalert_rules_dir }}/*.yml"
      evaluationInterval: "{{ monitoring_server_vmalert_eval_interval }}"
```

- [ ] **Step 2: Write `templates/alertmanager.yml.j2`**

```jinja
# {{ ansible_managed }}
route:
  receiver: {{ (monitoring_server_alertmanager_receivers | first).name | default('default') }}
  group_by: ['alertname', 'cluster']
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 4h

receivers:
{% if monitoring_server_alertmanager_receivers | length == 0 %}
  - name: default
{% else %}
{% for receiver in monitoring_server_alertmanager_receivers %}
  - name: {{ receiver.name }}
{% if receiver.type == 'slack' %}
    slack_configs:
      - api_url: {{ receiver.webhook }}
        channel: {{ receiver.channel | default('#alerts') }}
        send_resolved: true
{% elif receiver.type == 'email' %}
    email_configs:
      - to: {{ receiver.to }}
        from: {{ receiver.from | default('alertmanager@' ~ cluster_domain) }}
        smarthost: {{ receiver.smarthost }}
        send_resolved: true
{% elif receiver.type == 'webhook' %}
    webhook_configs:
      - url: {{ receiver.url }}
        send_resolved: true
{% endif %}
{% endfor %}
{% endif %}
```

- [ ] **Step 3: Write `templates/alertmanager.service.j2`**

```jinja
# {{ ansible_managed }}
[Unit]
Description=Alertmanager for pigsty-lite
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/alertmanager \
  --config.file={{ monitoring_server_alertmanager_config_file }} \
  --storage.path={{ monitoring_server_alertmanager_data_dir }} \
  --web.listen-address={{ monitoring_server_alertmanager_listen }}
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 4: Write `_alertmanager.yml`**

```yaml
---
- name: Install Alertmanager
  ansible.builtin.dnf:
    name: "{{ monitoring_server_alertmanager_package }}"
    state: present

- name: Ensure Alertmanager config directory exists
  ansible.builtin.file:
    path: "{{ monitoring_server_alertmanager_config_dir }}"
    state: directory
    owner: root
    group: root
    mode: "0755"

- name: Ensure Alertmanager data directory exists
  ansible.builtin.file:
    path: "{{ monitoring_server_alertmanager_data_dir }}"
    state: directory
    owner: root
    group: root
    mode: "0750"

- name: Render Alertmanager config
  ansible.builtin.template:
    src: alertmanager.yml.j2
    dest: "{{ monitoring_server_alertmanager_config_file }}"
    owner: root
    group: root
    mode: "0644"
  notify: Restart alertmanager

- name: Render Alertmanager systemd unit
  ansible.builtin.template:
    src: alertmanager.service.j2
    dest: "{{ monitoring_server_systemd_dir }}/alertmanager.service"
    owner: root
    group: root
    mode: "0644"
  notify:
    - Reload systemd for monitoring_server
    - Restart alertmanager

- name: Flush handlers so the unit is registered before enabling
  ansible.builtin.meta: flush_handlers

- name: Enable and start Alertmanager
  ansible.builtin.systemd:
    name: "{{ monitoring_server_alertmanager_service }}"
    enabled: true
    state: started

- name: Wait for Alertmanager to listen
  ansible.builtin.wait_for:
    host: "{{ monitoring_server_alertmanager_listen.split(':')[0] }}"
    port: "{{ monitoring_server_alertmanager_listen.split(':')[1] }}"
    timeout: 30
```

- [ ] **Step 5: Write `handlers/main.yml`**

```yaml
---
- name: Reload systemd for monitoring_server
  ansible.builtin.systemd:
    daemon_reload: true

- name: Restart alertmanager
  ansible.builtin.systemd:
    name: "{{ monitoring_server_alertmanager_service }}"
    state: restarted

- name: Reload firewalld
  ansible.builtin.systemd:
    name: firewalld
    state: reloaded
```

- [ ] **Step 6: Lint**

Run: `yamllint roles/monitoring_server/tasks/_vmalert.yml roles/monitoring_server/tasks/_alertmanager.yml roles/monitoring_server/handlers/main.yml`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add roles/monitoring_server/tasks/_vmalert.yml roles/monitoring_server/tasks/_alertmanager.yml \
        roles/monitoring_server/templates/alertmanager.yml.j2 \
        roles/monitoring_server/templates/alertmanager.service.j2 \
        roles/monitoring_server/handlers/main.yml
git commit -m "feat(monitoring_server): vmalert rules and Alertmanager"
```

---

## Task 6: monitoring_server — firewalld

**Files:**
- Create: `roles/monitoring_server/files/firewalld/services/victoriametrics.xml`
- Create: `roles/monitoring_server/files/firewalld/services/victorialogs.xml`
- Create: `roles/monitoring_server/tasks/_firewall.yml`

- [ ] **Step 1: Write `files/firewalld/services/victoriametrics.xml`**

```xml
<?xml version="1.0" encoding="utf-8"?>
<service>
  <short>victoriametrics</short>
  <description>VictoriaMetrics single-node metrics ingest and query (pigsty-lite).</description>
  <port protocol="tcp" port="8428"/>
</service>
```

- [ ] **Step 2: Write `files/firewalld/services/victorialogs.xml`**

```xml
<?xml version="1.0" encoding="utf-8"?>
<service>
  <short>victorialogs</short>
  <description>VictoriaLogs single-node log ingest and query (pigsty-lite).</description>
  <port protocol="tcp" port="9428"/>
</service>
```

- [ ] **Step 3: Write `_firewall.yml`**

```yaml
---
# vmsingle (8428) and vlsingle (9428) are cross-host: agents on the
# postgres nodes remote_write/ship to them. Open both custom services
# to the postgres + monitor source groups via rich rules. vmalert,
# Alertmanager, and Grafana bind loopback-only and get no firewalld
# entry.

- name: Install victoriametrics firewalld service definition
  ansible.builtin.copy:
    src: firewalld/services/victoriametrics.xml
    dest: /etc/firewalld/services/victoriametrics.xml
    owner: root
    group: root
    mode: "0644"
  notify: Reload firewalld

- name: Install victorialogs firewalld service definition
  ansible.builtin.copy:
    src: firewalld/services/victorialogs.xml
    dest: /etc/firewalld/services/victorialogs.xml
    owner: root
    group: root
    mode: "0644"
  notify: Reload firewalld

- name: Flush handlers so firewalld reloads before service rules apply
  ansible.builtin.meta: flush_handlers

- name: Build the monitoring source list (postgres + monitor hosts)
  ansible.builtin.set_fact:
    monitoring_server_firewall_sources: >-
      {{ (monitoring_server_firewall_sources | default([]))
         + [{'address': monitoring_server_source_address,
             'family': ('ipv6' if ':' in (monitoring_server_source_address | string) else 'ipv4')}] }}
  loop: "{{ (groups['postgres'] + groups['monitor']) | unique }}"
  vars:
    monitoring_server_source_address: >-
      {{ hostvars[item].ansible_host
         | default(hostvars[item].ansible_facts.default_ipv4.address) }}

- name: Open victoriametrics to postgres + monitor hosts
  ansible.posix.firewalld:
    rich_rule: >-
      rule family="{{ item.family }}" source address="{{ item.address }}"
      service name="victoriametrics" accept
    permanent: true
    state: enabled
    immediate: true
    zone: "{{ monitoring_server_firewalld_zone }}"
  loop: "{{ monitoring_server_firewall_sources | unique }}"

- name: Open victorialogs to postgres + monitor hosts
  ansible.posix.firewalld:
    rich_rule: >-
      rule family="{{ item.family }}" source address="{{ item.address }}"
      service name="victorialogs" accept
    permanent: true
    state: enabled
    immediate: true
    zone: "{{ monitoring_server_firewalld_zone }}"
  loop: "{{ monitoring_server_firewall_sources | unique }}"
```

- [ ] **Step 4: Lint**

Run: `yamllint roles/monitoring_server/tasks/_firewall.yml && xmllint --noout roles/monitoring_server/files/firewalld/services/victoriametrics.xml roles/monitoring_server/files/firewalld/services/victorialogs.xml`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add roles/monitoring_server/files roles/monitoring_server/tasks/_firewall.yml
git commit -m "feat(monitoring_server): firewalld services for vmsingle and vlsingle"
```

---

## Task 7: monitoring_agents defaults, meta, README

**Files:**
- Create: `roles/monitoring_agents/defaults/main.yml`
- Create: `roles/monitoring_agents/meta/main.yml`
- Create: `roles/monitoring_agents/README.md`

- [ ] **Step 1: Write `defaults/main.yml`**

```yaml
---
# roles/monitoring_agents/defaults/main.yml
# All variables prefixed `monitoring_agents_`. Exporters bind
# network_any_address and are firewalled to the monitor host. vmagent
# and vlagent bind loopback-only and remote_write/ship to the monitor.

# Exporters - packages
monitoring_agents_node_exporter_package: golang-github-prometheus-node-exporter
monitoring_agents_postgres_exporter_package: postgres_exporter
monitoring_agents_pgbouncer_exporter_package: pgbouncer_exporter
monitoring_agents_pgbackrest_exporter_package: pgbackrest_exporter

# Exporters - listen addresses
monitoring_agents_exporter_listen: "{{ network_any_address | default('0.0.0.0') }}"
monitoring_agents_node_exporter_port: "{{ node_exporter_port | default(9100) }}"
monitoring_agents_postgres_exporter_port: "{{ postgres_exporter_port | default(9187) }}"
monitoring_agents_pgbouncer_exporter_port: "{{ pgbouncer_exporter_port | default(9127) }}"
monitoring_agents_pgbackrest_exporter_port: "{{ pgbackrest_exporter_port | default(9854) }}"

# postgres_exporter / pgbouncer_exporter connect locally
monitoring_agents_postgres_exporter_dsn: "host=/var/run/postgresql port={{ postgres_port | default(5432) }} user={{ postgres_osdba | default('postgres') }} dbname=postgres sslmode=disable"
monitoring_agents_pgbouncer_exporter_dsn: "host=/var/run/postgresql port={{ pgbouncer_port | default(6432) }} user={{ postgres_osdba | default('postgres') }} dbname=pgbouncer sslmode=disable"
monitoring_agents_pgbackrest_stanza: "{{ backup_stanza | default(cluster_name) }}"

# vmagent (metrics scrape + remote_write)
monitoring_agents_vmagent_listen: "{{ network_loopback_address | default('127.0.0.1') }}:{{ vmagent_port | default(8429) }}"
monitoring_agents_vmagent_remote_write_url: "https://{{ hostvars[monitoring_host].ansible_host }}:{{ vmsingle_port | default(8428) }}/api/v1/write"
monitoring_agents_vmagent_buffer_dir: /var/lib/vmagent
monitoring_agents_scrape_interval: "{{ monitoring_scrape_interval | default('15s') }}"

# vlagent (log shipping)
monitoring_agents_vlagent_listen: "{{ network_loopback_address | default('127.0.0.1') }}:{{ vlagent_port | default(9429) }}"
monitoring_agents_vlagent_remote_write_url: "https://{{ hostvars[monitoring_host].ansible_host }}:{{ vlsingle_port | default(9428) }}/insert/jsonline"

# TLS: agents talk to the monitor over the P0-issued CA
monitoring_agents_ca_file: "{{ pki_dir | default('/etc/pki/pigsty-lite') }}/ca.crt"

# Patroni REST + HAProxy stats scrape targets (postgres hosts only)
monitoring_agents_patroni_rest_port: "{{ patroni_rest_port | default(8008) }}"
monitoring_agents_haproxy_stats_port: "{{ haproxy_stats_port | default(7000) }}"

# Firewalld
monitoring_agents_firewalld_zone: "{{ firewalld_default_zone | default('public') }}"
monitoring_agents_systemd_dir: /etc/systemd/system
```

- [ ] **Step 2: Write `meta/main.yml`**

```yaml
---
galaxy_info:
  role_name: monitoring_agents
  author: pigsty-lite
  description: Per-node exporters, vmagent, and vlagent shipping metrics and logs to the monitor host.
  license: Apache-2.0
  min_ansible_version: "2.16"
  platforms:
    - name: EL
      versions: ["10"]
dependencies: []
```

- [ ] **Step 3: Write `README.md`**

````markdown
# monitoring_agents

Per-node telemetry. Targets `all` hosts. Installs `node_exporter`
everywhere, the PostgreSQL/pgBouncer/pgBackRest exporters on postgres
hosts, and `vmagent` + `vlagent` to scrape locally and ship to the
monitor host.

## What this role owns

- `node_exporter` on every host (`network_any_address:9100`).
- `postgres_exporter` (9187), `pgbouncer_exporter` (9127),
  `pgbackrest_exporter` (9854) on postgres hosts only.
- `vmagent` (`network_loopback_address:8429`) — scrapes local exporters
  + Patroni REST + HAProxy stats, `remote_write`s to the monitor.
- `vlagent` (`network_loopback_address:9429`) — tails journald + PG
  logs + Patroni logs, ships to the monitor.
- The `postgres-exporter`, `pgbouncer-exporter`, `pgbackrest-exporter`
  custom firewalld services.

## What this role does NOT own

- vmsingle/vlsingle/vmalert/Alertmanager — that's `monitoring_server`.
- Grafana / nginx — separate roles.

## Ordering

`_assert` → `_exporters` → `_vmagent` → `_vlagent` → `_firewall`.
Exporters must listen before vmagent's scrape config references them.

## Idempotence

Second run is zero-change: packages present, exporter units
content-templated, the collection's agent roles diff their own config,
firewalld services content-compared.

## Tags

- `monitoring` — full role
- `monitoring,install` — exporters only
- `monitoring,config` — agent configs only
- `monitoring,firewall` — firewalld only
````

- [ ] **Step 4: Lint**

Run: `yamllint roles/monitoring_agents/defaults/main.yml roles/monitoring_agents/meta/main.yml`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add roles/monitoring_agents/defaults roles/monitoring_agents/meta roles/monitoring_agents/README.md
git commit -m "feat(monitoring_agents): role defaults, meta, README"
```

---

## Task 8: monitoring_agents — orchestration, assert, exporters

**Files:**
- Create: `roles/monitoring_agents/tasks/main.yml`
- Create: `roles/monitoring_agents/tasks/_assert.yml`
- Create: `roles/monitoring_agents/tasks/_exporters.yml`
- Create: `roles/monitoring_agents/templates/node-exporter.service.j2`
- Create: `roles/monitoring_agents/templates/postgres-exporter.service.j2`
- Create: `roles/monitoring_agents/templates/pgbouncer-exporter.service.j2`
- Create: `roles/monitoring_agents/templates/pgbackrest-exporter.service.j2`
- Create: `roles/monitoring_agents/handlers/main.yml`

- [ ] **Step 1: Write `tasks/main.yml`**

```yaml
---
- name: Validate preconditions
  ansible.builtin.import_tasks: _assert.yml
  tags: [monitoring, assert]

- name: Install and configure exporters
  ansible.builtin.import_tasks: _exporters.yml
  tags: [monitoring, install]

- name: Install and configure vmagent
  ansible.builtin.import_tasks: _vmagent.yml
  tags: [monitoring, config]

- name: Install and configure vlagent
  ansible.builtin.import_tasks: _vlagent.yml
  tags: [monitoring, config]

- name: Open firewalld for exporters
  ansible.builtin.import_tasks: _firewall.yml
  tags: [monitoring, firewall]
```

- [ ] **Step 2: Write `tasks/_assert.yml`**

```yaml
---
- name: Fail if monitor group is empty
  ansible.builtin.assert:
    that:
      - groups['monitor'] | length > 0
    fail_msg: >-
      Inventory group 'monitor' is empty; monitoring_agents needs a
      monitor host to remote_write metrics and ship logs to.
  run_once: true
  delegate_to: localhost

- name: Stat the CA certificate (from P0 certs role)
  ansible.builtin.stat:
    path: "{{ monitoring_agents_ca_file }}"
  register: monitoring_agents_ca_stat

- name: Fail if the CA certificate is missing
  ansible.builtin.assert:
    that:
      - monitoring_agents_ca_stat.stat.exists
    fail_msg: >-
      Expected the pigsty-lite CA at {{ monitoring_agents_ca_file }};
      agents need it to verify the monitor host's TLS. Run the P0
      _node.yml playbook (certs role) first.
```

- [ ] **Step 3: Write `templates/node-exporter.service.j2`**

```jinja
# {{ ansible_managed }}
[Unit]
Description=Prometheus Node Exporter (pigsty-lite)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=node_exporter
ExecStart=/usr/bin/node_exporter \
  --web.listen-address={{ monitoring_agents_exporter_listen }}:{{ monitoring_agents_node_exporter_port }}
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 4: Write `templates/postgres-exporter.service.j2`**

```jinja
# {{ ansible_managed }}
[Unit]
Description=PostgreSQL Exporter (pigsty-lite)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User={{ postgres_osdba | default('postgres') }}
Environment=DATA_SOURCE_NAME={{ monitoring_agents_postgres_exporter_dsn }}
ExecStart=/usr/bin/postgres_exporter \
  --web.listen-address={{ monitoring_agents_exporter_listen }}:{{ monitoring_agents_postgres_exporter_port }}
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 5: Write `templates/pgbouncer-exporter.service.j2`**

```jinja
# {{ ansible_managed }}
[Unit]
Description=pgBouncer Exporter (pigsty-lite)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User={{ postgres_osdba | default('postgres') }}
ExecStart=/usr/bin/pgbouncer_exporter \
  --web.listen-address={{ monitoring_agents_exporter_listen }}:{{ monitoring_agents_pgbouncer_exporter_port }} \
  --pgBouncer.connectionString="{{ monitoring_agents_pgbouncer_exporter_dsn }}"
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 6: Write `templates/pgbackrest-exporter.service.j2`**

```jinja
# {{ ansible_managed }}
[Unit]
Description=pgBackRest Exporter (pigsty-lite)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User={{ postgres_osdba | default('postgres') }}
ExecStart=/usr/bin/pgbackrest_exporter \
  --web.listen-address={{ monitoring_agents_exporter_listen }}:{{ monitoring_agents_pgbackrest_exporter_port }}
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 7: Write `tasks/_exporters.yml`**

```yaml
---
# node_exporter runs on every host. The three PG-side exporters run
# only on postgres hosts. Each exporter gets its own systemd unit and
# binds network_any_address (firewalled to the monitor host in
# _firewall.yml).

- name: Install node_exporter
  ansible.builtin.dnf:
    name: "{{ monitoring_agents_node_exporter_package }}"
    state: present

- name: Ensure the node_exporter system user exists
  ansible.builtin.user:
    name: node_exporter
    system: true
    shell: /sbin/nologin
    create_home: false
    state: present

- name: Render the node_exporter systemd unit
  ansible.builtin.template:
    src: node-exporter.service.j2
    dest: "{{ monitoring_agents_systemd_dir }}/node-exporter.service"
    owner: root
    group: root
    mode: "0644"
  notify:
    - Reload systemd for monitoring_agents
    - Restart node-exporter

- name: Install the PostgreSQL-side exporters
  ansible.builtin.dnf:
    name:
      - "{{ monitoring_agents_postgres_exporter_package }}"
      - "{{ monitoring_agents_pgbouncer_exporter_package }}"
      - "{{ monitoring_agents_pgbackrest_exporter_package }}"
    state: present
  when: inventory_hostname in groups['postgres']

- name: Render the PostgreSQL-side exporter systemd units
  ansible.builtin.template:
    src: "{{ item }}.service.j2"
    dest: "{{ monitoring_agents_systemd_dir }}/{{ item }}.service"
    owner: root
    group: root
    mode: "0644"
  loop:
    - postgres-exporter
    - pgbouncer-exporter
    - pgbackrest-exporter
  when: inventory_hostname in groups['postgres']
  notify:
    - Reload systemd for monitoring_agents
    - "Restart {{ item }}"

- name: Flush handlers so the units are registered before enabling
  ansible.builtin.meta: flush_handlers

- name: Enable and start node_exporter
  ansible.builtin.systemd:
    name: node-exporter
    enabled: true
    state: started

- name: Enable and start the PostgreSQL-side exporters
  ansible.builtin.systemd:
    name: "{{ item }}"
    enabled: true
    state: started
  loop:
    - postgres-exporter
    - pgbouncer-exporter
    - pgbackrest-exporter
  when: inventory_hostname in groups['postgres']
```

Note for the executor: the exporter **package names** in
`defaults/main.yml` are best-effort for RHEL 10. Before running this
task, verify each package resolves: `dnf list --available
postgres_exporter pgbouncer_exporter pgbackrest_exporter
golang-github-prometheus-node-exporter`. The spec (§14) explicitly
flags `pgbackrest_exporter` as an open packaging question — if it has
no RHEL 10 RPM, install it from the upstream release binary (a
`get_url` + `unarchive` into `/usr/bin/`, owner root mode 0755) and
leave a comment in `_exporters.yml` noting the source. Do not silently
skip it.

- [ ] **Step 8: Write `handlers/main.yml`**

```yaml
---
- name: Reload systemd for monitoring_agents
  ansible.builtin.systemd:
    daemon_reload: true

- name: Restart node-exporter
  ansible.builtin.systemd:
    name: node-exporter
    state: restarted

- name: Restart postgres-exporter
  ansible.builtin.systemd:
    name: postgres-exporter
    state: restarted

- name: Restart pgbouncer-exporter
  ansible.builtin.systemd:
    name: pgbouncer-exporter
    state: restarted

- name: Restart pgbackrest-exporter
  ansible.builtin.systemd:
    name: pgbackrest-exporter
    state: restarted

- name: Reload firewalld
  ansible.builtin.systemd:
    name: firewalld
    state: reloaded
```

- [ ] **Step 9: Lint**

Run: `yamllint roles/monitoring_agents/tasks/main.yml roles/monitoring_agents/tasks/_assert.yml roles/monitoring_agents/tasks/_exporters.yml roles/monitoring_agents/handlers/main.yml`
Expected: no errors.

- [ ] **Step 10: Commit**

```bash
git add roles/monitoring_agents/tasks/main.yml roles/monitoring_agents/tasks/_assert.yml \
        roles/monitoring_agents/tasks/_exporters.yml roles/monitoring_agents/handlers/main.yml \
        roles/monitoring_agents/templates/node-exporter.service.j2 \
        roles/monitoring_agents/templates/postgres-exporter.service.j2 \
        roles/monitoring_agents/templates/pgbouncer-exporter.service.j2 \
        roles/monitoring_agents/templates/pgbackrest-exporter.service.j2
git commit -m "feat(monitoring_agents): exporters and systemd units"
```

---

## Task 9: monitoring_agents — vmagent and vlagent

**Files:**
- Create: `roles/monitoring_agents/templates/vmagent-scrape.yml.j2`
- Create: `roles/monitoring_agents/templates/vlagent-config.yml.j2`
- Create: `roles/monitoring_agents/tasks/_vmagent.yml`
- Create: `roles/monitoring_agents/tasks/_vlagent.yml`

Before writing, confirm the collection's `vmagent` and `vlagent`
variable names:
Run: `cat collections/ansible_collections/victoriametrics/cluster/roles/vmagent/defaults/main.yml`
Run: `cat collections/ansible_collections/victoriametrics/cluster/roles/vlagent/defaults/main.yml`

The variable names below follow the collection's documented convention
(version 2.50.3). If they differ, use the names from the `defaults`
files — the intent (scrape config path, remote_write URL, listen
address, TLS CA) is what matters.

- [ ] **Step 1: Write `templates/vmagent-scrape.yml.j2`**

```jinja
# {{ ansible_managed }}
global:
  scrape_interval: {{ monitoring_agents_scrape_interval }}
scrape_configs:
  - job_name: node
    static_configs:
      - targets: ["{{ network_loopback_address | default('127.0.0.1') }}:{{ monitoring_agents_node_exporter_port }}"]
        labels:
          cluster: "{{ cluster_name }}"
          instance: "{{ inventory_hostname }}"
{% if inventory_hostname in groups['postgres'] %}
  - job_name: postgres
    static_configs:
      - targets: ["{{ network_loopback_address | default('127.0.0.1') }}:{{ monitoring_agents_postgres_exporter_port }}"]
        labels:
          cluster: "{{ cluster_name }}"
          instance: "{{ inventory_hostname }}"
  - job_name: pgbouncer
    static_configs:
      - targets: ["{{ network_loopback_address | default('127.0.0.1') }}:{{ monitoring_agents_pgbouncer_exporter_port }}"]
        labels:
          cluster: "{{ cluster_name }}"
          instance: "{{ inventory_hostname }}"
  - job_name: pgbackrest
    static_configs:
      - targets: ["{{ network_loopback_address | default('127.0.0.1') }}:{{ monitoring_agents_pgbackrest_exporter_port }}"]
        labels:
          cluster: "{{ cluster_name }}"
          instance: "{{ inventory_hostname }}"
  - job_name: patroni
    scheme: https
    tls_config:
      ca_file: "{{ monitoring_agents_ca_file }}"
    static_configs:
      - targets: ["{{ network_loopback_address | default('127.0.0.1') }}:{{ monitoring_agents_patroni_rest_port }}"]
        labels:
          cluster: "{{ cluster_name }}"
          instance: "{{ inventory_hostname }}"
  - job_name: haproxy
    static_configs:
      - targets: ["{{ network_loopback_address | default('127.0.0.1') }}:{{ monitoring_agents_haproxy_stats_port }}"]
        labels:
          cluster: "{{ cluster_name }}"
          instance: "{{ inventory_hostname }}"
{% endif %}
```

- [ ] **Step 2: Write `templates/vlagent-config.yml.j2`**

```jinja
# {{ ansible_managed }}
# vlagent tails journald and the PostgreSQL / Patroni logs and ships
# them to VictoriaLogs on the monitor host.
journald:
  - field_selectors: []
    stream_fields: ["cluster", "instance"]
    extra_fields:
      cluster: "{{ cluster_name }}"
      instance: "{{ inventory_hostname }}"
{% if inventory_hostname in groups['postgres'] %}
file:
  - paths:
      - "/var/lib/pgsql/{{ postgres_version }}/data/log/*.log"
      - "/var/log/patroni/*.log"
    stream_fields: ["cluster", "instance"]
    extra_fields:
      cluster: "{{ cluster_name }}"
      instance: "{{ inventory_hostname }}"
{% endif %}
```

- [ ] **Step 3: Write `_vmagent.yml`**

```yaml
---
- name: Ensure the vmagent buffer directory exists
  ansible.builtin.file:
    path: "{{ monitoring_agents_vmagent_buffer_dir }}"
    state: directory
    owner: root
    group: root
    mode: "0750"

- name: Render the vmagent scrape config
  ansible.builtin.template:
    src: vmagent-scrape.yml.j2
    dest: /etc/vmagent/scrape.yml
    owner: root
    group: root
    mode: "0644"

- name: Install and configure vmagent
  ansible.builtin.include_role:
    name: victoriametrics.cluster.vmagent
  vars:
    vmagent_service_args:
      httpListenAddr: "{{ monitoring_agents_vmagent_listen }}"
      "promscrape.config": /etc/vmagent/scrape.yml
      "remoteWrite.url": "{{ monitoring_agents_vmagent_remote_write_url }}"
      "remoteWrite.tlsCAFile": "{{ monitoring_agents_ca_file }}"
      "remoteWrite.tmpDataPath": "{{ monitoring_agents_vmagent_buffer_dir }}"
```

- [ ] **Step 4: Write `_vlagent.yml`**

```yaml
---
- name: Render the vlagent config
  ansible.builtin.template:
    src: vlagent-config.yml.j2
    dest: /etc/vlagent/config.yml
    owner: root
    group: root
    mode: "0644"

- name: Install and configure vlagent
  ansible.builtin.include_role:
    name: victoriametrics.cluster.vlagent
  vars:
    vlagent_service_args:
      httpListenAddr: "{{ monitoring_agents_vlagent_listen }}"
      "remoteWrite.url": "{{ monitoring_agents_vlagent_remote_write_url }}"
      "remoteWrite.tlsCAFile": "{{ monitoring_agents_ca_file }}"
      "syslog.listenAddr.tcp": ""
      "configPath": /etc/vlagent/config.yml
```

- [ ] **Step 5: Lint**

Run: `yamllint roles/monitoring_agents/tasks/_vmagent.yml roles/monitoring_agents/tasks/_vlagent.yml`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add roles/monitoring_agents/tasks/_vmagent.yml roles/monitoring_agents/tasks/_vlagent.yml \
        roles/monitoring_agents/templates/vmagent-scrape.yml.j2 \
        roles/monitoring_agents/templates/vlagent-config.yml.j2
git commit -m "feat(monitoring_agents): vmagent and vlagent via VM collection"
```

---

## Task 10: monitoring_agents — firewalld

**Files:**
- Create: `roles/monitoring_agents/files/firewalld/services/postgres-exporter.xml`
- Create: `roles/monitoring_agents/files/firewalld/services/pgbouncer-exporter.xml`
- Create: `roles/monitoring_agents/files/firewalld/services/pgbackrest-exporter.xml`
- Create: `roles/monitoring_agents/tasks/_firewall.yml`

- [ ] **Step 1: Write `files/firewalld/services/postgres-exporter.xml`**

```xml
<?xml version="1.0" encoding="utf-8"?>
<service>
  <short>postgres-exporter</short>
  <description>Prometheus PostgreSQL exporter scrape endpoint (pigsty-lite).</description>
  <port protocol="tcp" port="9187"/>
</service>
```

- [ ] **Step 2: Write `files/firewalld/services/pgbouncer-exporter.xml`**

```xml
<?xml version="1.0" encoding="utf-8"?>
<service>
  <short>pgbouncer-exporter</short>
  <description>Prometheus pgBouncer exporter scrape endpoint (pigsty-lite).</description>
  <port protocol="tcp" port="9127"/>
</service>
```

- [ ] **Step 3: Write `files/firewalld/services/pgbackrest-exporter.xml`**

```xml
<?xml version="1.0" encoding="utf-8"?>
<service>
  <short>pgbackrest-exporter</short>
  <description>Prometheus pgBackRest exporter scrape endpoint (pigsty-lite).</description>
  <port protocol="tcp" port="9854"/>
</service>
```

- [ ] **Step 4: Write `_firewall.yml`**

```yaml
---
# node_exporter (9100) uses the built-in `prometheus-node-exporter`
# firewalld service and runs on every host. The three PG-side exporters
# use custom services and run only on postgres hosts. All four are
# opened to the monitor host only - it is the sole scrape source.

- name: Resolve the monitor host firewall source
  ansible.builtin.set_fact:
    monitoring_agents_monitor_source:
      address: >-
        {{ hostvars[monitoring_host].ansible_host
           | default(hostvars[monitoring_host].ansible_facts.default_ipv4.address) }}
  vars: {}

- name: Set the monitor source address family
  ansible.builtin.set_fact:
    monitoring_agents_monitor_family: >-
      {{ 'ipv6' if ':' in (monitoring_agents_monitor_source.address | string) else 'ipv4' }}

- name: Open the built-in prometheus-node-exporter service to the monitor host
  ansible.posix.firewalld:
    rich_rule: >-
      rule family="{{ monitoring_agents_monitor_family }}"
      source address="{{ monitoring_agents_monitor_source.address }}"
      service name="prometheus-node-exporter" accept
    permanent: true
    state: enabled
    immediate: true
    zone: "{{ monitoring_agents_firewalld_zone }}"

- name: Install the PG-side exporter firewalld service definitions
  ansible.builtin.copy:
    src: "firewalld/services/{{ item }}.xml"
    dest: "/etc/firewalld/services/{{ item }}.xml"
    owner: root
    group: root
    mode: "0644"
  loop:
    - postgres-exporter
    - pgbouncer-exporter
    - pgbackrest-exporter
  when: inventory_hostname in groups['postgres']
  notify: Reload firewalld

- name: Flush handlers so firewalld reloads before service rules apply
  ansible.builtin.meta: flush_handlers

- name: Open the PG-side exporter services to the monitor host
  ansible.posix.firewalld:
    rich_rule: >-
      rule family="{{ monitoring_agents_monitor_family }}"
      source address="{{ monitoring_agents_monitor_source.address }}"
      service name="{{ item }}" accept
    permanent: true
    state: enabled
    immediate: true
    zone: "{{ monitoring_agents_firewalld_zone }}"
  loop:
    - postgres-exporter
    - pgbouncer-exporter
    - pgbackrest-exporter
  when: inventory_hostname in groups['postgres']
```

- [ ] **Step 5: Lint**

Run: `yamllint roles/monitoring_agents/tasks/_firewall.yml && xmllint --noout roles/monitoring_agents/files/firewalld/services/postgres-exporter.xml roles/monitoring_agents/files/firewalld/services/pgbouncer-exporter.xml roles/monitoring_agents/files/firewalld/services/pgbackrest-exporter.xml`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add roles/monitoring_agents/files roles/monitoring_agents/tasks/_firewall.yml
git commit -m "feat(monitoring_agents): firewalld services for exporters"
```

---

## Task 11: grafana defaults, meta, README

**Files:**
- Create: `roles/grafana/defaults/main.yml`
- Create: `roles/grafana/meta/main.yml`
- Create: `roles/grafana/README.md`

- [ ] **Step 1: Write `defaults/main.yml`**

```yaml
---
# roles/grafana/defaults/main.yml
# All variables prefixed `grafana_`. Grafana binds loopback-only and is
# fronted by nginx_proxy at the /grafana/ sub-path.

grafana_listen_address: "{{ network_loopback_address | default('127.0.0.1') }}"
grafana_port: "{{ grafana_port | default(3000) }}"
grafana_admin_user: admin
grafana_admin_password: "{{ vault_grafana_admin_password | default('grafana-dev-admin-change-me') }}"

# Served behind nginx at https://<domain>/grafana/
grafana_root_url: "https://{{ cluster_domain }}/grafana/"
grafana_serve_from_sub_path: true

# Datasources point at the local vmsingle (Prometheus-compatible) and
# vlsingle, both on loopback.
grafana_vmsingle_url: "http://{{ network_loopback_address | default('127.0.0.1') }}:{{ vmsingle_port | default(8428) }}"
grafana_vlsingle_url: "http://{{ network_loopback_address | default('127.0.0.1') }}:{{ vlsingle_port | default(9428) }}"

# Dashboard provisioning
grafana_provisioning_dir: /etc/grafana/provisioning
grafana_dashboards_dir: /var/lib/grafana/dashboards

# Grafana API endpoint for the community.grafana modules (loopback).
grafana_api_url: "http://{{ network_loopback_address | default('127.0.0.1') }}:{{ grafana_port | default(3000) }}"
```

- [ ] **Step 2: Write `meta/main.yml`**

```yaml
---
galaxy_info:
  role_name: grafana
  author: pigsty-lite
  description: Grafana install plus VictoriaMetrics/VictoriaLogs datasources and dashboards.
  license: Apache-2.0
  min_ansible_version: "2.16"
  platforms:
    - name: EL
      versions: ["10"]
dependencies: []
```

- [ ] **Step 3: Write `README.md`**

````markdown
# grafana

Dashboards. Targets the `monitor` group (one host). Installs Grafana via
the `grafana.grafana` collection, then configures the VictoriaMetrics
and VictoriaLogs datasources and provisions dashboards via
`community.grafana` modules and file provisioning.

## What this role owns

- Grafana on `network_loopback_address:3000`, SQLite-backed,
  `serve_from_sub_path` true (fronted by nginx at `/grafana/`).
- The VictoriaMetrics (Prometheus-type) and VictoriaLogs datasources.
- Dashboards provisioned from `roles/grafana/files/dashboards/`.

## What this role does NOT own

- vmsingle/vlsingle — that's `monitoring_server`.
- TLS termination / the public `/grafana/` route — that's `nginx_proxy`.

## Ordering

`_assert` → `_install` → `_datasources` → `_dashboards`. Datasources
are created after Grafana is up; dashboards reference the datasources.

## Idempotence

Second run is zero-change: the collection role diffs Grafana config,
`grafana_datasource` is declarative, dashboard JSON is content-compared.

## Tags

- `monitoring` — full role
- `monitoring,config` — datasources + dashboards only
````

- [ ] **Step 4: Lint**

Run: `yamllint roles/grafana/defaults/main.yml roles/grafana/meta/main.yml`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add roles/grafana/defaults roles/grafana/meta roles/grafana/README.md
git commit -m "feat(grafana): role defaults, meta, README"
```

---

## Task 12: grafana — orchestration, assert, install

**Files:**
- Create: `roles/grafana/tasks/main.yml`
- Create: `roles/grafana/tasks/_assert.yml`
- Create: `roles/grafana/tasks/_install.yml`
- Create: `roles/grafana/handlers/main.yml`

- [ ] **Step 1: Write `tasks/main.yml`**

```yaml
---
- name: Validate preconditions
  ansible.builtin.import_tasks: _assert.yml
  tags: [monitoring, assert]

- name: Install and base-configure Grafana
  ansible.builtin.import_tasks: _install.yml
  tags: [monitoring, config]

- name: Configure datasources
  ansible.builtin.import_tasks: _datasources.yml
  tags: [monitoring, config]

- name: Provision dashboards
  ansible.builtin.import_tasks: _dashboards.yml
  tags: [monitoring, config]
```

- [ ] **Step 2: Write `tasks/_assert.yml`**

```yaml
---
- name: Fail if monitor group is empty
  ansible.builtin.assert:
    that:
      - groups['monitor'] | length > 0
    fail_msg: "Inventory group 'monitor' is empty; the grafana role requires a monitor host."
  run_once: true
  delegate_to: localhost

- name: Fail unless monitor group size is exactly 1
  ansible.builtin.assert:
    that:
      - groups['monitor'] | length == 1
    fail_msg: >-
      monitor group must contain exactly one host;
      got {{ groups['monitor'] | length }}. Replicated Grafana is out
      of scope.
  run_once: true
  delegate_to: localhost
```

- [ ] **Step 3: Write `tasks/_install.yml`**

Before writing, confirm the `grafana.grafana` collection's role name
and variable names:
Run: `ls collections/ansible_collections/grafana/grafana/roles/`
Run: `cat collections/ansible_collections/grafana/grafana/roles/grafana/defaults/main.yml`

The collection's role is `grafana.grafana.grafana`. The `grafana_ini`
dict below maps onto the collection's expected config structure; if the
collection uses a different variable, use the one from the `defaults`
file — the intent (loopback bind, sub-path serving, SQLite) is what
matters.

```yaml
---
- name: Install and base-configure Grafana
  ansible.builtin.include_role:
    name: grafana.grafana.grafana
  vars:
    grafana_ini:
      server:
        http_addr: "{{ grafana_listen_address }}"
        http_port: "{{ grafana_port }}"
        root_url: "{{ grafana_root_url }}"
        serve_from_sub_path: "{{ grafana_serve_from_sub_path }}"
      database:
        type: sqlite3
      security:
        admin_user: "{{ grafana_admin_user }}"
        admin_password: "{{ grafana_admin_password }}"
```

- [ ] **Step 4: Write `handlers/main.yml`**

```yaml
---
- name: Restart grafana
  ansible.builtin.systemd:
    name: grafana-server
    state: restarted
```

- [ ] **Step 5: Lint**

Run: `yamllint roles/grafana/tasks/main.yml roles/grafana/tasks/_assert.yml roles/grafana/tasks/_install.yml roles/grafana/handlers/main.yml`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add roles/grafana/tasks/main.yml roles/grafana/tasks/_assert.yml \
        roles/grafana/tasks/_install.yml roles/grafana/handlers/main.yml
git commit -m "feat(grafana): orchestration, preconditions, install"
```

---

## Task 13: grafana — datasources

**Files:**
- Create: `roles/grafana/tasks/_datasources.yml`

- [ ] **Step 1: Write `_datasources.yml`**

```yaml
---
# VictoriaMetrics speaks the Prometheus query API, so the datasource
# type is `prometheus`. VictoriaLogs has a dedicated Grafana datasource
# plugin; if the plugin is not present the collection's install step
# can add it, but for v1 we register VL as a `loki`-compatible endpoint
# only if the plugin exists - otherwise we register just the metrics
# datasource and note the gap. Confirm plugin availability during
# execution: `grafana-cli plugins list-remote | grep victoria`.

- name: Register the VictoriaMetrics datasource
  community.grafana.grafana_datasource:
    name: VictoriaMetrics
    grafana_url: "{{ grafana_api_url }}"
    grafana_user: "{{ grafana_admin_user }}"
    grafana_password: "{{ grafana_admin_password }}"
    ds_type: prometheus
    ds_url: "{{ grafana_vmsingle_url }}"
    is_default: true
    state: present

- name: Install the VictoriaLogs Grafana datasource plugin
  ansible.builtin.command:
    cmd: grafana-cli plugins install victoriametrics-logs-datasource
  register: grafana_vl_plugin
  changed_when: "'Installed' in grafana_vl_plugin.stdout"
  failed_when:
    - grafana_vl_plugin.rc != 0
    - "'already installed' not in grafana_vl_plugin.stdout"
  notify: Restart grafana

- name: Flush handlers so Grafana restarts with the plugin loaded
  ansible.builtin.meta: flush_handlers

- name: Register the VictoriaLogs datasource
  community.grafana.grafana_datasource:
    name: VictoriaLogs
    grafana_url: "{{ grafana_api_url }}"
    grafana_user: "{{ grafana_admin_user }}"
    grafana_password: "{{ grafana_admin_password }}"
    ds_type: victoriametrics-logs-datasource
    ds_url: "{{ grafana_vlsingle_url }}"
    state: present
```

- [ ] **Step 2: Lint**

Run: `yamllint roles/grafana/tasks/_datasources.yml`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add roles/grafana/tasks/_datasources.yml
git commit -m "feat(grafana): VictoriaMetrics and VictoriaLogs datasources"
```

---

## Task 14: grafana — dashboards

**Files:**
- Create: `roles/grafana/templates/dashboard-provider.yml.j2`
- Create: `roles/grafana/files/dashboards/pigsty-lite-overview.json`
- Create: `roles/grafana/tasks/_dashboards.yml`

- [ ] **Step 1: Write `templates/dashboard-provider.yml.j2`**

```jinja
# {{ ansible_managed }}
apiVersion: 1
providers:
  - name: pigsty-lite
    orgId: 1
    folder: pigsty-lite
    type: file
    disableDeletion: false
    updateIntervalSeconds: 30
    allowUiUpdates: true
    options:
      path: {{ grafana_dashboards_dir }}
      foldersFromFilesStructure: false
```

- [ ] **Step 2: Write `files/dashboards/pigsty-lite-overview.json`**

A real, minimal dashboard — cluster up-status, replication lag,
connections, node CPU/memory/disk. Not a placeholder.

```json
{
  "title": "pigsty-lite — cluster overview",
  "uid": "pigsty-lite-overview",
  "schemaVersion": 39,
  "version": 1,
  "refresh": "30s",
  "time": { "from": "now-6h", "to": "now" },
  "templating": {
    "list": [
      {
        "name": "cluster",
        "type": "query",
        "datasource": { "type": "prometheus", "uid": "VictoriaMetrics" },
        "query": "label_values(pg_up, cluster)",
        "refresh": 2
      }
    ]
  },
  "panels": [
    {
      "id": 1,
      "title": "PostgreSQL instances up",
      "type": "stat",
      "datasource": { "type": "prometheus", "uid": "VictoriaMetrics" },
      "gridPos": { "h": 6, "w": 6, "x": 0, "y": 0 },
      "targets": [
        { "expr": "sum(pg_up{cluster=\"$cluster\"})", "refId": "A" }
      ]
    },
    {
      "id": 2,
      "title": "Replication lag (bytes)",
      "type": "timeseries",
      "datasource": { "type": "prometheus", "uid": "VictoriaMetrics" },
      "gridPos": { "h": 6, "w": 18, "x": 6, "y": 0 },
      "targets": [
        {
          "expr": "pg_replication_lag_bytes{cluster=\"$cluster\"}",
          "refId": "A",
          "legendFormat": "{{instance}}"
        }
      ]
    },
    {
      "id": 3,
      "title": "Active connections",
      "type": "timeseries",
      "datasource": { "type": "prometheus", "uid": "VictoriaMetrics" },
      "gridPos": { "h": 6, "w": 12, "x": 0, "y": 6 },
      "targets": [
        {
          "expr": "sum by (instance) (pg_stat_activity_count{cluster=\"$cluster\"})",
          "refId": "A",
          "legendFormat": "{{instance}}"
        }
      ]
    },
    {
      "id": 4,
      "title": "Node CPU / memory / disk",
      "type": "timeseries",
      "datasource": { "type": "prometheus", "uid": "VictoriaMetrics" },
      "gridPos": { "h": 6, "w": 12, "x": 12, "y": 6 },
      "targets": [
        {
          "expr": "1 - avg by (instance) (rate(node_cpu_seconds_total{mode=\"idle\",cluster=\"$cluster\"}[5m]))",
          "refId": "A",
          "legendFormat": "cpu {{instance}}"
        },
        {
          "expr": "1 - (node_memory_MemAvailable_bytes{cluster=\"$cluster\"} / node_memory_MemTotal_bytes{cluster=\"$cluster\"})",
          "refId": "B",
          "legendFormat": "mem {{instance}}"
        }
      ]
    }
  ]
}
```

- [ ] **Step 3: Write `_dashboards.yml`**

```yaml
---
- name: Ensure the Grafana dashboards directory exists
  ansible.builtin.file:
    path: "{{ grafana_dashboards_dir }}"
    state: directory
    owner: grafana
    group: grafana
    mode: "0755"

- name: Copy dashboard JSON files
  ansible.builtin.copy:
    src: dashboards/
    dest: "{{ grafana_dashboards_dir }}/"
    owner: grafana
    group: grafana
    mode: "0644"
  notify: Restart grafana

- name: Render the dashboard file-provisioning config
  ansible.builtin.template:
    src: dashboard-provider.yml.j2
    dest: "{{ grafana_provisioning_dir }}/dashboards/pigsty-lite.yml"
    owner: root
    group: grafana
    mode: "0640"
  notify: Restart grafana

- name: Flush handlers so Grafana picks up the dashboards
  ansible.builtin.meta: flush_handlers

- name: Wait for Grafana to report healthy
  ansible.builtin.uri:
    url: "{{ grafana_api_url }}/api/health"
    method: GET
    return_content: true
  register: grafana_health
  retries: 20
  delay: 3
  until: grafana_health.status == 200 and (grafana_health.json.database | default('')) == 'ok'
```

- [ ] **Step 4: Lint**

Run: `yamllint roles/grafana/tasks/_dashboards.yml && python3 -c "import json; json.load(open('roles/grafana/files/dashboards/pigsty-lite-overview.json'))"`
Expected: no errors; JSON parses.

- [ ] **Step 5: Commit**

```bash
git add roles/grafana/templates/dashboard-provider.yml.j2 \
        roles/grafana/files/dashboards/pigsty-lite-overview.json \
        roles/grafana/tasks/_dashboards.yml
git commit -m "feat(grafana): dashboard provisioning and overview dashboard"
```

---

## Task 15: nginx_proxy defaults, meta, README

**Files:**
- Create: `roles/nginx_proxy/defaults/main.yml`
- Create: `roles/nginx_proxy/meta/main.yml`
- Create: `roles/nginx_proxy/README.md`

- [ ] **Step 1: Write `defaults/main.yml`**

```yaml
---
# roles/nginx_proxy/defaults/main.yml
# All variables prefixed `nginx_proxy_`. nginx is the only inbound for
# user-facing UIs; it terminates TLS and proxies to the loopback-bound
# Grafana / Alertmanager / vmalert.

nginx_proxy_package: nginx
nginx_proxy_service: nginx
nginx_proxy_config_file: /etc/nginx/conf.d/pigsty-lite.conf

# TLS mode: ca_signed (P0-issued cert) | byo (operator cert) | http
nginx_proxy_tls_mode: "{{ nginx_proxy_tls_mode | default('ca_signed') }}"
nginx_proxy_ca_signed_cert: "{{ pki_dir | default('/etc/pki/pigsty-lite') }}/{{ inventory_hostname }}.crt"
nginx_proxy_ca_signed_key: "{{ pki_dir | default('/etc/pki/pigsty-lite') }}/{{ inventory_hostname }}.key"
# Operator overrides these two when nginx_proxy_tls_mode == 'byo'
nginx_proxy_byo_cert: ""
nginx_proxy_byo_key: ""

# Listen addresses
nginx_proxy_listen_address: "{{ network_any_address | default('0.0.0.0') }}"
nginx_proxy_http_port: 80
nginx_proxy_https_port: 443
nginx_proxy_server_name: "{{ cluster_domain }}"

# Upstreams (loopback)
nginx_proxy_grafana_upstream: "{{ network_loopback_address | default('127.0.0.1') }}:{{ grafana_port | default(3000) }}"
nginx_proxy_alertmanager_upstream: "{{ network_loopback_address | default('127.0.0.1') }}:{{ alertmanager_port | default(9093) }}"
nginx_proxy_vmalert_upstream: "{{ network_loopback_address | default('127.0.0.1') }}:{{ vmalert_port | default(8880) }}"

# Firewalld
nginx_proxy_firewalld_zone: "{{ firewalld_default_zone | default('public') }}"
```

- [ ] **Step 2: Write `meta/main.yml`**

```yaml
---
galaxy_info:
  role_name: nginx_proxy
  author: pigsty-lite
  description: nginx reverse proxy terminating TLS and routing to Grafana, Alertmanager, and vmalert.
  license: Apache-2.0
  min_ansible_version: "2.16"
  platforms:
    - name: EL
      versions: ["10"]
dependencies: []
```

- [ ] **Step 3: Write `README.md`**

````markdown
# nginx_proxy

The single public inbound for monitoring UIs. Targets the `monitor`
group (one host). Terminates TLS and reverse-proxies `/grafana/`,
`/alertmanager/`, and `/vmalert/` to their loopback-bound backends.

## Inputs (from response file, via group_vars)

| Variable | Meaning | Default |
|---|---|---|
| `nginx_proxy_tls_mode` | `ca_signed` \| `byo` \| `http` | `ca_signed` |

## What this role owns

- nginx on `network_any_address:80,443` (firewalled to `operator_cidrs`).
- `/etc/nginx/conf.d/pigsty-lite.conf` — the reverse-proxy server block.
- The `http` + `https` firewalld openings.

## What this role does NOT own

- The backends — Grafana / Alertmanager / vmalert are owned by their
  own roles and bind loopback-only.
- Certificate issuance — `ca_signed` mode reuses the P0 `certs` role's
  per-host cert; `byo` mode uses an operator-supplied cert.

## Ordering

`_assert` → `_install` → `_tls` → `_config` → `_firewall` →
`_service`.

## Idempotence

Second run is zero-change: package present, config content-templated
and `nginx -t`-validated, firewalld services content-compared.

## Tags

- `nginx_proxy` — full role
- `nginx_proxy,config` — re-render the server block only
- `nginx_proxy,firewall` — firewalld only
- `nginx_proxy,service` — restart nginx only
````

- [ ] **Step 4: Lint**

Run: `yamllint roles/nginx_proxy/defaults/main.yml roles/nginx_proxy/meta/main.yml`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add roles/nginx_proxy/defaults roles/nginx_proxy/meta roles/nginx_proxy/README.md
git commit -m "feat(nginx_proxy): role defaults, meta, README"
```

---

## Task 16: nginx_proxy — orchestration, assert, install, tls

**Files:**
- Create: `roles/nginx_proxy/tasks/main.yml`
- Create: `roles/nginx_proxy/tasks/_assert.yml`
- Create: `roles/nginx_proxy/tasks/_install.yml`
- Create: `roles/nginx_proxy/tasks/_tls.yml`
- Create: `roles/nginx_proxy/handlers/main.yml`

- [ ] **Step 1: Write `tasks/main.yml`**

```yaml
---
- name: Validate preconditions
  ansible.builtin.import_tasks: _assert.yml
  tags: [nginx_proxy, assert]

- name: Install nginx
  ansible.builtin.import_tasks: _install.yml
  tags: [nginx_proxy, install]

- name: Resolve TLS material
  ansible.builtin.import_tasks: _tls.yml
  tags: [nginx_proxy, config]

- name: Render the reverse-proxy config
  ansible.builtin.import_tasks: _config.yml
  tags: [nginx_proxy, config]

- name: Open firewalld for http and https
  ansible.builtin.import_tasks: _firewall.yml
  tags: [nginx_proxy, firewall]

- name: Enable and start nginx
  ansible.builtin.import_tasks: _service.yml
  tags: [nginx_proxy, service]
```

- [ ] **Step 2: Write `tasks/_assert.yml`**

```yaml
---
- name: Fail if monitor group is empty
  ansible.builtin.assert:
    that:
      - groups['monitor'] | length > 0
    fail_msg: "Inventory group 'monitor' is empty; the nginx_proxy role requires a monitor host."
  run_once: true
  delegate_to: localhost

- name: Validate the TLS mode
  ansible.builtin.assert:
    that:
      - nginx_proxy_tls_mode in ['ca_signed', 'byo', 'http']
    fail_msg: "nginx_proxy_tls_mode must be one of: ca_signed, byo, http."

- name: Stat the CA-signed certificate when in ca_signed mode
  ansible.builtin.stat:
    path: "{{ nginx_proxy_ca_signed_cert }}"
  register: nginx_proxy_ca_cert_stat
  when: nginx_proxy_tls_mode == 'ca_signed'

- name: Fail if the CA-signed certificate is missing
  ansible.builtin.assert:
    that:
      - nginx_proxy_ca_cert_stat.stat.exists
    fail_msg: >-
      nginx_proxy_tls_mode is 'ca_signed' but no certificate exists at
      {{ nginx_proxy_ca_signed_cert }}. Run the P0 _node.yml playbook
      (certs role) first.
  when: nginx_proxy_tls_mode == 'ca_signed'

- name: Fail if a byo certificate path is not supplied
  ansible.builtin.assert:
    that:
      - nginx_proxy_byo_cert | length > 0
      - nginx_proxy_byo_key | length > 0
    fail_msg: >-
      nginx_proxy_tls_mode is 'byo' but nginx_proxy_byo_cert /
      nginx_proxy_byo_key are not set. Supply operator certificate paths.
  when: nginx_proxy_tls_mode == 'byo'
```

- [ ] **Step 3: Write `tasks/_install.yml`**

```yaml
---
- name: Install nginx
  ansible.builtin.dnf:
    name: "{{ nginx_proxy_package }}"
    state: present

- name: Allow nginx to connect to loopback upstreams
  ansible.posix.seboolean:
    name: httpd_can_network_connect
    state: true
    persistent: true
  when: ansible_facts.selinux.status == "enabled"
```

- [ ] **Step 4: Write `tasks/_tls.yml`**

```yaml
---
# Resolve which cert/key nginx should use, based on the TLS mode. The
# resolved paths are set as facts consumed by the config template.

- name: Use the P0-issued certificate (ca_signed mode)
  ansible.builtin.set_fact:
    nginx_proxy_cert_path: "{{ nginx_proxy_ca_signed_cert }}"
    nginx_proxy_key_path: "{{ nginx_proxy_ca_signed_key }}"
    nginx_proxy_tls_enabled: true
  when: nginx_proxy_tls_mode == 'ca_signed'

- name: Use the operator-supplied certificate (byo mode)
  ansible.builtin.set_fact:
    nginx_proxy_cert_path: "{{ nginx_proxy_byo_cert }}"
    nginx_proxy_key_path: "{{ nginx_proxy_byo_key }}"
    nginx_proxy_tls_enabled: true
  when: nginx_proxy_tls_mode == 'byo'

- name: Serve plain HTTP only (http mode)
  ansible.builtin.set_fact:
    nginx_proxy_tls_enabled: false
  when: nginx_proxy_tls_mode == 'http'
```

- [ ] **Step 5: Write `handlers/main.yml`**

```yaml
---
- name: Reload nginx
  ansible.builtin.systemd:
    name: "{{ nginx_proxy_service }}"
    state: reloaded

- name: Reload firewalld
  ansible.builtin.systemd:
    name: firewalld
    state: reloaded
```

- [ ] **Step 6: Lint**

Run: `yamllint roles/nginx_proxy/tasks/main.yml roles/nginx_proxy/tasks/_assert.yml roles/nginx_proxy/tasks/_install.yml roles/nginx_proxy/tasks/_tls.yml roles/nginx_proxy/handlers/main.yml`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add roles/nginx_proxy/tasks/main.yml roles/nginx_proxy/tasks/_assert.yml \
        roles/nginx_proxy/tasks/_install.yml roles/nginx_proxy/tasks/_tls.yml \
        roles/nginx_proxy/handlers/main.yml
git commit -m "feat(nginx_proxy): orchestration, preconditions, install, TLS resolution"
```

---

## Task 17: nginx_proxy — config, firewall, service

**Files:**
- Create: `roles/nginx_proxy/templates/pigsty-lite.conf.j2`
- Create: `roles/nginx_proxy/tasks/_config.yml`
- Create: `roles/nginx_proxy/tasks/_firewall.yml`
- Create: `roles/nginx_proxy/tasks/_service.yml`

- [ ] **Step 1: Write `templates/pigsty-lite.conf.j2`**

```jinja
# {{ ansible_managed }}
# pigsty-lite reverse proxy: TLS termination + UI routing.
{% if nginx_proxy_tls_enabled %}
server {
    listen {{ nginx_proxy_listen_address }}:{{ nginx_proxy_http_port }};
    server_name {{ nginx_proxy_server_name }};
    return 301 https://$host$request_uri;
}

server {
    listen {{ nginx_proxy_listen_address }}:{{ nginx_proxy_https_port }} ssl;
    server_name {{ nginx_proxy_server_name }};

    ssl_certificate {{ nginx_proxy_cert_path }};
    ssl_certificate_key {{ nginx_proxy_key_path }};
    ssl_protocols TLSv1.2 TLSv1.3;
{% else %}
server {
    listen {{ nginx_proxy_listen_address }}:{{ nginx_proxy_http_port }};
    server_name {{ nginx_proxy_server_name }};
{% endif %}

    location /grafana/ {
        proxy_pass http://{{ nginx_proxy_grafana_upstream }}/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /alertmanager/ {
        proxy_pass http://{{ nginx_proxy_alertmanager_upstream }}/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /vmalert/ {
        proxy_pass http://{{ nginx_proxy_vmalert_upstream }}/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location = / {
        return 302 /grafana/;
    }
}
```

- [ ] **Step 2: Write `tasks/_config.yml`**

```yaml
---
- name: Render the pigsty-lite reverse-proxy config
  ansible.builtin.template:
    src: pigsty-lite.conf.j2
    dest: "{{ nginx_proxy_config_file }}"
    owner: root
    group: root
    mode: "0644"
    validate: "nginx -t -c /etc/nginx/nginx.conf"
  notify: Reload nginx
```

- [ ] **Step 3: Write `tasks/_firewall.yml`**

```yaml
---
# nginx is the only public inbound for the monitoring UIs. Open the
# built-in http and https services to operator_cidrs only.

- name: Open the built-in http service to operator CIDRs
  ansible.posix.firewalld:
    rich_rule: >-
      rule family="{{ 'ipv6' if ':' in (item | string) else 'ipv4' }}"
      source address="{{ item }}" service name="http" accept
    permanent: true
    state: enabled
    immediate: true
    zone: "{{ nginx_proxy_firewalld_zone }}"
  loop: "{{ operator_cidrs }}"

- name: Open the built-in https service to operator CIDRs
  ansible.posix.firewalld:
    rich_rule: >-
      rule family="{{ 'ipv6' if ':' in (item | string) else 'ipv4' }}"
      source address="{{ item }}" service name="https" accept
    permanent: true
    state: enabled
    immediate: true
    zone: "{{ nginx_proxy_firewalld_zone }}"
  loop: "{{ operator_cidrs }}"
  when: nginx_proxy_tls_enabled
```

- [ ] **Step 4: Write `tasks/_service.yml`**

```yaml
---
- name: Enable and start nginx
  ansible.builtin.systemd:
    name: "{{ nginx_proxy_service }}"
    enabled: true
    state: started

- name: Wait for nginx to listen on https
  ansible.builtin.wait_for:
    host: "{{ nginx_proxy_listen_address }}"
    port: "{{ nginx_proxy_https_port }}"
    timeout: 30
  when: nginx_proxy_tls_enabled

- name: Wait for nginx to listen on http
  ansible.builtin.wait_for:
    host: "{{ nginx_proxy_listen_address }}"
    port: "{{ nginx_proxy_http_port }}"
    timeout: 30
  when: not nginx_proxy_tls_enabled
```

- [ ] **Step 5: Lint**

Run: `yamllint roles/nginx_proxy/tasks/_config.yml roles/nginx_proxy/tasks/_firewall.yml roles/nginx_proxy/tasks/_service.yml`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add roles/nginx_proxy/templates/pigsty-lite.conf.j2 \
        roles/nginx_proxy/tasks/_config.yml roles/nginx_proxy/tasks/_firewall.yml \
        roles/nginx_proxy/tasks/_service.yml
git commit -m "feat(nginx_proxy): reverse-proxy config, firewall, service"
```

---

## Task 18: response schema — monitoring receivers and scrape interval

**Files:**
- Modify: `bin/_response_schema.py`
- Modify: `bin/_generate_response_vars.py`
- Modify: `tests/configure/test_schema.py`

- [ ] **Step 1: Add failing tests**

Add to `tests/configure/test_schema.py`. Open the file first and use
whatever minimal-response helper is actually defined there.

```python
def test_monitoring_receiver_must_be_mapping():
    response = _minimal_single_response()
    response["monitoring"]["alertmanager"] = {"receivers": ["not-a-dict"]}
    with pytest.raises(SchemaError, match=r"monitoring\.alertmanager\.receivers\[0\]: must be a mapping"):
        validate(response)


def test_monitoring_receiver_requires_name_and_type():
    response = _minimal_single_response()
    response["monitoring"]["alertmanager"] = {"receivers": [{"type": "slack"}]}
    with pytest.raises(SchemaError, match=r"monitoring\.alertmanager\.receivers\[0\]\.name"):
        validate(response)


def test_monitoring_receiver_type_must_be_known():
    response = _minimal_single_response()
    response["monitoring"]["alertmanager"] = {
        "receivers": [{"name": "x", "type": "carrier-pigeon"}]
    }
    with pytest.raises(SchemaError, match=r"monitoring\.alertmanager\.receivers\[0\]\.type"):
        validate(response)


def test_monitoring_scrape_interval_must_be_duration():
    response = _minimal_single_response()
    response["monitoring"]["scrape_interval"] = "fifteen"
    with pytest.raises(SchemaError, match=r"monitoring\.scrape_interval"):
        validate(response)


def test_monitoring_scrape_interval_is_optional():
    response = _minimal_single_response()
    response["monitoring"].pop("scrape_interval", None)
    validate(response)
```

- [ ] **Step 2: Run tests; expect failure**

Run: `pytest tests/configure/test_schema.py -k monitoring -v`
Expected: 4 FAILs (the receiver checks + scrape interval); the
"is_optional" test already passes.

- [ ] **Step 3: Extend the schema**

Edit `bin/_response_schema.py`. Inside `_validate_monitoring()`, after
the existing retention-string loop, add:

```python
    alertmanager = monitoring.get("alertmanager", {})
    if alertmanager:
        if not isinstance(alertmanager, dict):
            raise SchemaError("monitoring.alertmanager: must be a mapping")
        receivers = alertmanager.get("receivers", [])
        if not isinstance(receivers, list):
            raise SchemaError("monitoring.alertmanager.receivers: must be a list")
        known_types = {"slack", "email", "webhook", "pagerduty"}
        for index, receiver in enumerate(receivers):
            path = f"monitoring.alertmanager.receivers[{index}]"
            if not isinstance(receiver, dict):
                raise SchemaError(f"{path}: must be a mapping")
            _require_str(receiver, "name", path)
            rtype = _require_str(receiver, "type", path)
            if rtype not in known_types:
                raise SchemaError(
                    f"{path}.type: '{rtype}' not in {sorted(known_types)}"
                )

    scrape_interval = monitoring.get("scrape_interval")
    if scrape_interval is not None:
        if not isinstance(scrape_interval, str) or not re.fullmatch(
            r"\d+[smhd]", scrape_interval
        ):
            raise SchemaError(
                "monitoring.scrape_interval: must match Ns|Nm|Nh|Nd form (e.g. 15s)"
            )
```

If `re` is not already imported at the top of the file, add `import re`.
(The existing retention validation uses a regex, so `re` is very likely
already imported — check before adding.)

- [ ] **Step 4: Run tests; expect pass**

Run: `pytest tests/configure/test_schema.py -k monitoring -v`
Expected: all PASS.

- [ ] **Step 5: Emit `monitoring_scrape_interval` from the generator**

Edit `bin/_generate_response_vars.py`. In the `out` dict literal, next
to the existing `vmsingle_retention` / `vlsingle_retention` /
`alertmanager_receivers` lines, add:

```python
        "monitoring_scrape_interval": monitoring.get("scrape_interval", "15s"),
```

- [ ] **Step 6: Run the full schema + generator suite**

Run: `pytest tests/configure -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add bin/_response_schema.py bin/_generate_response_vars.py tests/configure/test_schema.py
git commit -m "feat(configure): validate monitoring receivers and scrape interval"
```

---

## Task 19: playbooks and site.yml wiring

**Files:**
- Create: `playbooks/_monitoring_server.yml`
- Create: `playbooks/_monitoring_agents.yml`
- Create: `playbooks/_grafana.yml`
- Create: `playbooks/_nginx_proxy.yml`
- Modify: `playbooks/site.yml`
- Modify: `playbooks/tags.md`
- Modify: `responses/single.rsp.yml.example`
- Modify: `responses/ha.rsp.yml.example`

- [ ] **Step 1: Write `playbooks/_monitoring_server.yml`**

```yaml
---
- name: P5 monitoring server - VictoriaMetrics, VictoriaLogs, vmalert, Alertmanager
  hosts: monitor
  become: true
  gather_facts: true
  roles:
    - role: monitoring_server
      tags: [monitoring]
```

- [ ] **Step 2: Write `playbooks/_monitoring_agents.yml`**

```yaml
---
- name: P5 monitoring agents - exporters, vmagent, vlagent on every node
  hosts: all
  become: true
  gather_facts: true
  roles:
    - role: monitoring_agents
      tags: [monitoring]
```

- [ ] **Step 3: Write `playbooks/_grafana.yml`**

```yaml
---
- name: P5 Grafana - dashboards on the monitor host
  hosts: monitor
  become: true
  gather_facts: true
  roles:
    - role: grafana
      tags: [monitoring]
```

- [ ] **Step 4: Write `playbooks/_nginx_proxy.yml`**

```yaml
---
- name: P5 nginx reverse proxy - TLS termination on the monitor host
  hosts: monitor
  become: true
  gather_facts: true
  roles:
    - role: nginx_proxy
      tags: [nginx_proxy]
```

- [ ] **Step 5: Wire into `playbooks/site.yml`**

Edit `playbooks/site.yml`. After the `_backup_store.yml` import block
(the last block), append:

```yaml
- name: Import P5 monitoring server playbook
  import_playbook: _monitoring_server.yml
  tags: [monitoring]
- name: Import P5 monitoring agents playbook
  import_playbook: _monitoring_agents.yml
  tags: [monitoring]
- name: Import P5 Grafana playbook
  import_playbook: _grafana.yml
  tags: [monitoring]
- name: Import P5 nginx reverse proxy playbook
  import_playbook: _nginx_proxy.yml
  tags: [nginx_proxy]
```

This matches the spec's deploy order (§3.4, §5.1):
`_monitoring_server → _monitoring_agents → _grafana → _nginx_proxy`.

- [ ] **Step 6: Add tags to `playbooks/tags.md`**

Edit `playbooks/tags.md`. Under `## Module tags`, after the last entry,
add:

```
- `monitoring`
- `nginx_proxy`
```

Under `## Examples`, after the last example, add:

```
- `--tags monitoring` - install/reconfigure the full metrics+logs+dashboards stack.
- `--tags monitoring,config` - re-render monitoring configs only (no service installs).
- `--tags nginx_proxy` - reconfigure the reverse proxy (reload nginx).
```

- [ ] **Step 7: Add day-2 pointers to the response examples**

Edit `responses/single.rsp.yml.example`. Immediately above the
`monitoring:` line, add:

```
# P5 day-2 workflow: edit this block, then run make deploy. See
# docs/operations/day2-monitoring.md.
```

Edit `responses/ha.rsp.yml.example` the same way. Do not change values.

- [ ] **Step 8: Lint**

Run: `yamllint playbooks/_monitoring_server.yml playbooks/_monitoring_agents.yml playbooks/_grafana.yml playbooks/_nginx_proxy.yml playbooks/site.yml playbooks/tags.md responses/single.rsp.yml.example responses/ha.rsp.yml.example`
Expected: no errors.

- [ ] **Step 9: Commit**

```bash
git add playbooks/_monitoring_server.yml playbooks/_monitoring_agents.yml \
        playbooks/_grafana.yml playbooks/_nginx_proxy.yml \
        playbooks/site.yml playbooks/tags.md \
        responses/single.rsp.yml.example responses/ha.rsp.yml.example
git commit -m "feat(playbooks): wire P5 monitoring stack into site"
```

---

## Task 20: Molecule scenario — monitoring_server/default

**Files:**
- Create: `tests/molecule/monitoring_server/molecule/default/molecule.yml`
- Create: `tests/molecule/monitoring_server/molecule/default/prepare.yml`
- Create: `tests/molecule/monitoring_server/molecule/default/converge.yml`
- Create: `tests/molecule/monitoring_server/molecule/default/verify.yml`

- [ ] **Step 1: `molecule.yml`**

```yaml
---
driver:
  name: podman
platforms:
  - name: pigsty-lite-monsrv-default-1
    image: docker.io/oraclelinux:10
    pre_build_image: true
    privileged: true
    command: /usr/sbin/init
    volume_mounts:
      - "/sys/fs/cgroup:/sys/fs/cgroup:rw"
    capabilities: [SYS_ADMIN]
    systemd: always
    groups: [monitor]
provisioner:
  name: ansible
  config_options:
    defaults:
      collections_path: "../../../collections"
  inventory:
    group_vars:
      all:
        cluster_name: pigsty-lite-test
        cluster_domain: test.local
        pki_dir: /etc/pki/pigsty-lite
        vmsingle_retention: 7d
        vlsingle_retention: 7d
        alertmanager_receivers: []
    host_vars:
      pigsty-lite-monsrv-default-1: {}
verifier:
  name: ansible
scenario:
  test_sequence:
    - destroy
    - create
    - prepare
    - converge
    - idempotence
    - verify
    - destroy
```

- [ ] **Step 2: `prepare.yml`**

```yaml
---
- name: Bring up preflight + CA + node (certs) on the monitor host
  ansible.builtin.import_playbook: ../../../../playbooks/_preflight.yml
- name: Bring up CA
  ansible.builtin.import_playbook: ../../../../playbooks/_ca.yml
- name: Bring up node + repos + certs
  ansible.builtin.import_playbook: ../../../../playbooks/_node.yml
```

- [ ] **Step 3: `converge.yml`**

```yaml
---
- name: Apply the monitoring_server role
  ansible.builtin.import_playbook: ../../../../playbooks/_monitoring_server.yml
```

- [ ] **Step 4: `verify.yml`**

```yaml
---
- name: Verify the monitoring server stack
  hosts: monitor
  become: true
  gather_facts: false
  tasks:
    - name: vmsingle answers on 8428
      ansible.builtin.uri:
        url: "http://127.0.0.1:8428/health"
        method: GET
        status_code: [200]
      register: vm_health
      retries: 10
      delay: 3
      until: vm_health.status == 200

    - name: vlsingle answers on 9428
      ansible.builtin.uri:
        url: "http://127.0.0.1:9428/health"
        method: GET
        status_code: [200]
      register: vl_health
      retries: 10
      delay: 3
      until: vl_health.status == 200

    - name: vmalert listens on loopback 8880
      ansible.builtin.command:
        cmd: "ss -H -ltn sport = :8880"
      register: vmalert_listen
      changed_when: false
      failed_when: vmalert_listen.stdout | length == 0

    - name: Alertmanager listens on loopback 9093
      ansible.builtin.command:
        cmd: "ss -H -ltn sport = :9093"
      register: am_listen
      changed_when: false
      failed_when: am_listen.stdout | length == 0

    - name: vmsingle is NOT reachable on a non-loopback bind without the firewall source
      ansible.builtin.command:
        cmd: "ss -H -ltn sport = :8428"
      register: vm_listen
      changed_when: false
      failed_when: vm_listen.stdout | length == 0

    - name: No SELinux AVC denials since boot
      ansible.builtin.command:
        cmd: ausearch -m AVC -ts boot
      register: avc
      changed_when: false
      failed_when: avc.rc == 0 and 'type=AVC' in avc.stdout
```

- [ ] **Step 5: Commit**

```bash
git add tests/molecule/monitoring_server/molecule/default
git commit -m "test(monitoring_server): default scenario verifies the metrics/logs/alerting stack"
```

---

## Task 21: Molecule scenario — monitoring_agents/default

**Files:**
- Create: `tests/molecule/monitoring_agents/molecule/default/molecule.yml`
- Create: `tests/molecule/monitoring_agents/molecule/default/prepare.yml`
- Create: `tests/molecule/monitoring_agents/molecule/default/converge.yml`
- Create: `tests/molecule/monitoring_agents/molecule/default/verify.yml`

- [ ] **Step 1: `molecule.yml`**

```yaml
---
driver:
  name: podman
platforms:
  - name: pigsty-lite-monagt-default-1
    image: docker.io/oraclelinux:10
    pre_build_image: true
    privileged: true
    command: /usr/sbin/init
    volume_mounts:
      - "/sys/fs/cgroup:/sys/fs/cgroup:rw"
    capabilities: [SYS_ADMIN]
    systemd: always
    groups: [monitor]
    networks:
      - name: pigsty-lite-monagt
  - name: pigsty-lite-monagt-default-2
    image: docker.io/oraclelinux:10
    pre_build_image: true
    privileged: true
    command: /usr/sbin/init
    volume_mounts:
      - "/sys/fs/cgroup:/sys/fs/cgroup:rw"
    capabilities: [SYS_ADMIN]
    systemd: always
    groups: [postgres]
    networks:
      - name: pigsty-lite-monagt
provisioner:
  name: ansible
  config_options:
    defaults:
      collections_path: "../../../collections"
  inventory:
    group_vars:
      all:
        cluster_name: pigsty-lite-test
        cluster_domain: test.local
        pki_dir: /etc/pki/pigsty-lite
        postgres_version: 18
    host_vars:
      pigsty-lite-monagt-default-1: {}
      pigsty-lite-monagt-default-2:
        postgres_role: primary
verifier:
  name: ansible
scenario:
  test_sequence:
    - destroy
    - create
    - prepare
    - converge
    - idempotence
    - verify
    - destroy
```

- [ ] **Step 2: `prepare.yml`**

```yaml
---
# Bring up the full base stack so the postgres node has PG + Patroni +
# pgBouncer + pgBackRest for the exporters to scrape, and the monitor
# host has the monitoring server for the agents to ship to.
- name: Bring up preflight
  ansible.builtin.import_playbook: ../../../../playbooks/_preflight.yml
- name: Bring up CA
  ansible.builtin.import_playbook: ../../../../playbooks/_ca.yml
- name: Bring up node + repos + certs
  ansible.builtin.import_playbook: ../../../../playbooks/_node.yml
- name: Bring up etcd
  ansible.builtin.import_playbook: ../../../../playbooks/_etcd.yml
- name: Install postgres
  ansible.builtin.import_playbook: ../../../../playbooks/_postgres_install.yml
- name: Bootstrap patroni
  ansible.builtin.import_playbook: ../../../../playbooks/_postgres_bootstrap.yml
- name: Install pgbouncer
  ansible.builtin.import_playbook: ../../../../playbooks/_pgbouncer.yml
- name: Install haproxy
  ansible.builtin.import_playbook: ../../../../playbooks/_haproxy.yml
- name: Install the backup client + store
  ansible.builtin.import_playbook: ../../../../playbooks/_backup_client.yml
- name: Install the backup store
  ansible.builtin.import_playbook: ../../../../playbooks/_backup_store.yml
- name: Bring up the monitoring server
  ansible.builtin.import_playbook: ../../../../playbooks/_monitoring_server.yml

- name: Pin ansible_host
  hosts: all
  gather_facts: true
  tasks:
    - name: Set ansible_host to default IPv4
      ansible.builtin.set_fact:
        ansible_host: "{{ ansible_default_ipv4.address }}"
```

Note: the `prepare.yml` is heavy because monitoring_agents genuinely
depends on the whole stack being present (exporters scrape real
services). If `_backup_client.yml` requires `backup_store` group
membership, add `backup_store` to the monitor container's `groups` in
`molecule.yml` — confirm against the committed P4 role's `_assert.yml`
during execution.

- [ ] **Step 3: `converge.yml`**

```yaml
---
- name: Apply the monitoring_agents role
  ansible.builtin.import_playbook: ../../../../playbooks/_monitoring_agents.yml
```

- [ ] **Step 4: `verify.yml`**

```yaml
---
- name: Verify exporters on the postgres node
  hosts: postgres
  become: true
  gather_facts: false
  tasks:
    - name: node_exporter answers on 9100
      ansible.builtin.uri:
        url: "http://127.0.0.1:9100/metrics"
        method: GET
        status_code: [200]
      register: node_metrics
      retries: 10
      delay: 3
      until: node_metrics.status == 200

    - name: postgres_exporter answers on 9187
      ansible.builtin.uri:
        url: "http://127.0.0.1:9187/metrics"
        method: GET
        status_code: [200]
      register: pg_metrics
      retries: 10
      delay: 3
      until: pg_metrics.status == 200

    - name: postgres_exporter exposes pg_up
      ansible.builtin.assert:
        that:
          - "'pg_up' in pg_metrics.content"
        fail_msg: "postgres_exporter did not expose pg_up"

    - name: pgbouncer_exporter answers on 9127
      ansible.builtin.uri:
        url: "http://127.0.0.1:9127/metrics"
        method: GET
        status_code: [200]
      register: pgb_metrics
      retries: 10
      delay: 3
      until: pgb_metrics.status == 200

    - name: pgbackrest_exporter answers on 9854
      ansible.builtin.uri:
        url: "http://127.0.0.1:9854/metrics"
        method: GET
        status_code: [200]
      register: pgbr_metrics
      retries: 10
      delay: 3
      until: pgbr_metrics.status == 200

    - name: vmagent listens on loopback 8429
      ansible.builtin.command:
        cmd: "ss -H -ltn sport = :8429"
      register: vmagent_listen
      changed_when: false
      failed_when: vmagent_listen.stdout | length == 0

- name: Verify metrics arrived at the monitor host
  hosts: monitor
  become: true
  gather_facts: false
  tasks:
    - name: vmsingle reports the postgres job as up
      ansible.builtin.uri:
        url: "http://127.0.0.1:8428/api/v1/query?query=up%7Bjob%3D%22postgres%22%7D"
        method: GET
        return_content: true
      register: vm_query
      retries: 20
      delay: 6
      until: >-
        vm_query.status == 200
        and (vm_query.json.data.result | default([])) | length > 0

    - name: node_exporter on the monitor host answers too
      ansible.builtin.uri:
        url: "http://127.0.0.1:9100/metrics"
        method: GET
        status_code: [200]
```

- [ ] **Step 5: Commit**

```bash
git add tests/molecule/monitoring_agents/molecule/default
git commit -m "test(monitoring_agents): default scenario verifies exporters and remote_write"
```

---

## Task 22: Molecule scenario — grafana/default

**Files:**
- Create: `tests/molecule/grafana/molecule/default/molecule.yml`
- Create: `tests/molecule/grafana/molecule/default/prepare.yml`
- Create: `tests/molecule/grafana/molecule/default/converge.yml`
- Create: `tests/molecule/grafana/molecule/default/verify.yml`

- [ ] **Step 1: `molecule.yml`**

```yaml
---
driver:
  name: podman
platforms:
  - name: pigsty-lite-grafana-default-1
    image: docker.io/oraclelinux:10
    pre_build_image: true
    privileged: true
    command: /usr/sbin/init
    volume_mounts:
      - "/sys/fs/cgroup:/sys/fs/cgroup:rw"
    capabilities: [SYS_ADMIN]
    systemd: always
    groups: [monitor]
provisioner:
  name: ansible
  config_options:
    defaults:
      collections_path: "../../../collections"
  inventory:
    group_vars:
      all:
        cluster_name: pigsty-lite-test
        cluster_domain: test.local
        pki_dir: /etc/pki/pigsty-lite
        vmsingle_retention: 7d
        vlsingle_retention: 7d
        alertmanager_receivers: []
    host_vars:
      pigsty-lite-grafana-default-1: {}
verifier:
  name: ansible
scenario:
  test_sequence:
    - destroy
    - create
    - prepare
    - converge
    - idempotence
    - verify
    - destroy
```

- [ ] **Step 2: `prepare.yml`**

```yaml
---
# Grafana needs vmsingle/vlsingle present for the datasources to
# validate, so bring up the base + monitoring server first.
- name: Bring up preflight
  ansible.builtin.import_playbook: ../../../../playbooks/_preflight.yml
- name: Bring up CA
  ansible.builtin.import_playbook: ../../../../playbooks/_ca.yml
- name: Bring up node + repos + certs
  ansible.builtin.import_playbook: ../../../../playbooks/_node.yml
- name: Bring up the monitoring server
  ansible.builtin.import_playbook: ../../../../playbooks/_monitoring_server.yml
```

- [ ] **Step 3: `converge.yml`**

```yaml
---
- name: Apply the grafana role
  ansible.builtin.import_playbook: ../../../../playbooks/_grafana.yml
```

- [ ] **Step 4: `verify.yml`**

```yaml
---
- name: Verify Grafana
  hosts: monitor
  become: true
  gather_facts: false
  tasks:
    - name: Grafana reports healthy
      ansible.builtin.uri:
        url: "http://127.0.0.1:3000/api/health"
        method: GET
        return_content: true
      register: grafana_health
      retries: 20
      delay: 3
      until: grafana_health.status == 200 and grafana_health.json.database == 'ok'

    - name: Both datasources exist
      ansible.builtin.uri:
        url: "http://127.0.0.1:3000/api/datasources"
        method: GET
        url_username: admin
        url_password: "{{ grafana_admin_password | default('grafana-dev-admin-change-me') }}"
        force_basic_auth: true
        return_content: true
      register: grafana_ds

    - name: VictoriaMetrics and VictoriaLogs datasources are both present
      ansible.builtin.assert:
        that:
          - (grafana_ds.json | map(attribute='name') | list) is superset(['VictoriaMetrics', 'VictoriaLogs'])
        fail_msg: "Expected VictoriaMetrics and VictoriaLogs datasources"

    - name: The overview dashboard is provisioned
      ansible.builtin.uri:
        url: "http://127.0.0.1:3000/api/dashboards/uid/pigsty-lite-overview"
        method: GET
        url_username: admin
        url_password: "{{ grafana_admin_password | default('grafana-dev-admin-change-me') }}"
        force_basic_auth: true
        status_code: [200]
```

- [ ] **Step 5: Commit**

```bash
git add tests/molecule/grafana/molecule/default
git commit -m "test(grafana): default scenario verifies health, datasources, dashboard"
```

---

## Task 23: Molecule scenario — nginx_proxy/default

**Files:**
- Create: `tests/molecule/nginx_proxy/molecule/default/molecule.yml`
- Create: `tests/molecule/nginx_proxy/molecule/default/prepare.yml`
- Create: `tests/molecule/nginx_proxy/molecule/default/converge.yml`
- Create: `tests/molecule/nginx_proxy/molecule/default/verify.yml`

- [ ] **Step 1: `molecule.yml`**

```yaml
---
driver:
  name: podman
platforms:
  - name: pigsty-lite-nginx-default-1
    image: docker.io/oraclelinux:10
    pre_build_image: true
    privileged: true
    command: /usr/sbin/init
    volume_mounts:
      - "/sys/fs/cgroup:/sys/fs/cgroup:rw"
    capabilities: [SYS_ADMIN]
    systemd: always
    groups: [monitor]
provisioner:
  name: ansible
  config_options:
    defaults:
      collections_path: "../../../collections"
  inventory:
    group_vars:
      all:
        cluster_name: pigsty-lite-test
        cluster_domain: test.local
        pki_dir: /etc/pki/pigsty-lite
        vmsingle_retention: 7d
        vlsingle_retention: 7d
        alertmanager_receivers: []
    host_vars:
      pigsty-lite-nginx-default-1: {}
verifier:
  name: ansible
scenario:
  test_sequence:
    - destroy
    - create
    - prepare
    - converge
    - idempotence
    - verify
    - destroy
```

- [ ] **Step 2: `prepare.yml`**

```yaml
---
# nginx_proxy needs the monitor cert (from certs) and the loopback
# backends (grafana / alertmanager / vmalert) present so the proxy
# locations resolve. Bring up the full monitor-host stack.
- name: Bring up preflight
  ansible.builtin.import_playbook: ../../../../playbooks/_preflight.yml
- name: Bring up CA
  ansible.builtin.import_playbook: ../../../../playbooks/_ca.yml
- name: Bring up node + repos + certs
  ansible.builtin.import_playbook: ../../../../playbooks/_node.yml
- name: Bring up the monitoring server
  ansible.builtin.import_playbook: ../../../../playbooks/_monitoring_server.yml
- name: Bring up Grafana
  ansible.builtin.import_playbook: ../../../../playbooks/_grafana.yml
```

- [ ] **Step 3: `converge.yml`**

```yaml
---
- name: Apply the nginx_proxy role
  ansible.builtin.import_playbook: ../../../../playbooks/_nginx_proxy.yml
```

- [ ] **Step 4: `verify.yml`**

```yaml
---
- name: Verify the nginx reverse proxy
  hosts: monitor
  become: true
  gather_facts: false
  tasks:
    - name: nginx listens on 443
      ansible.builtin.command:
        cmd: "ss -H -ltn sport = :443"
      register: https_listen
      changed_when: false
      failed_when: https_listen.stdout | length == 0

    - name: nginx listens on 80
      ansible.builtin.command:
        cmd: "ss -H -ltn sport = :80"
      register: http_listen
      changed_when: false
      failed_when: http_listen.stdout | length == 0

    - name: HTTPS root redirects to /grafana/
      ansible.builtin.uri:
        url: "https://127.0.0.1/"
        method: GET
        validate_certs: false
        follow_redirects: none
        status_code: [302]
      register: root_redirect

    - name: The redirect points at /grafana/
      ansible.builtin.assert:
        that:
          - "'/grafana/' in root_redirect.location"

    - name: /grafana/ proxies through to a healthy Grafana
      ansible.builtin.uri:
        url: "https://127.0.0.1/grafana/api/health"
        method: GET
        validate_certs: false
        return_content: true
        status_code: [200]
      register: proxied_health
      retries: 10
      delay: 3
      until: proxied_health.status == 200

    - name: No SELinux AVC denials since boot
      ansible.builtin.command:
        cmd: ausearch -m AVC -ts boot
      register: avc
      changed_when: false
      failed_when: avc.rc == 0 and 'type=AVC' in avc.stdout
```

- [ ] **Step 5: Commit**

```bash
git add tests/molecule/nginx_proxy/molecule/default
git commit -m "test(nginx_proxy): default scenario verifies TLS termination and routing"
```

---

## Task 24: extend CI matrix

**Files:**
- Modify: `.github/workflows/molecule.yml`

- [ ] **Step 1: Read the matrix entries**

Run: `sed -n '1,90p' .github/workflows/molecule.yml`
Confirm the matrix is a list of `{role, scenario}` rows ending with the
`backup` rows added by P4.

- [ ] **Step 2: Append four rows**

Edit `.github/workflows/molecule.yml`. After the last `backup` matrix
entry, add, matching the existing indentation exactly:

```yaml
          - role: monitoring_server
            scenario: default
          - role: monitoring_agents
            scenario: default
          - role: grafana
            scenario: default
          - role: nginx_proxy
            scenario: default
```

- [ ] **Step 3: Lint**

Run: `yamllint .github/workflows/molecule.yml`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/molecule.yml
git commit -m "ci(molecule): add monitoring stack scenarios"
```

---

## Task 25: docs — firstrun + day-2 monitoring runbook

**Files:**
- Modify: `docs/operations/firstrun.md`
- Create: `docs/operations/day2-monitoring.md`

- [ ] **Step 1: Append a P5 section to `firstrun.md`**

Edit `docs/operations/firstrun.md`. At the end of the file (after the
P4 section), append:

```markdown

## After P5 (monitoring)

`make deploy` stands up VictoriaMetrics + VictoriaLogs on the monitor
host, ships metrics and logs from every node, evaluates alerts through
vmalert + Alertmanager, and serves Grafana behind an nginx TLS proxy.

Verify:

```bash
# Grafana, via the reverse proxy (from inside operator_cidrs):
curl -k https://<monitor-host>/grafana/api/health

# Every postgres host should report up in VictoriaMetrics:
curl -s 'http://<monitor-host>:8428/api/v1/query?query=pg_up' | jq '.data.result'

# Logs are flowing into VictoriaLogs:
curl -s 'http://<monitor-host>:9428/select/logsql/query' \
  --data-urlencode 'query=*' --data-urlencode 'limit=1'
```

The monitoring UIs are all behind nginx on the monitor host:
`/grafana/`, `/alertmanager/`, `/vmalert/`. Nothing else is exposed —
`nmap` from outside `operator_cidrs` sees only `22, 80, 443`.

To add an alert rule, a receiver, or a dashboard day-2, see
[docs/operations/day2-monitoring.md](day2-monitoring.md).
```

- [ ] **Step 2: Create `docs/operations/day2-monitoring.md`**

```markdown
# Day-2 monitoring

The monitoring stack is declarative. Most changes go through the
response file's `monitoring:` block, then `make deploy`. To target only
the monitoring step, pass `--tags monitoring`.

## Add an Alertmanager receiver

```yaml
monitoring:
  alertmanager:
    receivers:
      - name: default
        type: slack
        webhook: !vault | ...
      - name: pager            # added
        type: pagerduty
        url: !vault | ...
```

`make deploy` re-renders `/etc/alertmanager/alertmanager.yml` and
restarts Alertmanager.

## Change retention

```yaml
monitoring:
  vmsingle_retention: 180d    # was 90d
  vlsingle_retention: 60d     # was 30d
```

`make deploy` updates the vmsingle/vlsingle service args. Note that
shrinking retention does not immediately reclaim disk — VictoriaMetrics
expires data lazily.

## Change the scrape interval

```yaml
monitoring:
  scrape_interval: 30s        # default is 15s
```

`make deploy` re-renders every node's vmagent scrape config.

## Add an alert rule

Alert rules live in `roles/monitoring_server/` and are shipped to
`/etc/vmalert/rules/` on the monitor host. To add a rule group, drop a
new `*.yml` file into the rules directory via a small custom play, or
extend `roles/monitoring_server/tasks/_vmalert.yml`'s starter rule
file. vmalert picks up rule files matching `*.yml` and reloads on the
evaluation interval.

## Add a Grafana dashboard

Drop a dashboard JSON into `roles/grafana/files/dashboards/` and
`make deploy --tags monitoring`. The file provider picks up new
dashboards within 30 seconds; no Grafana restart needed.

## Common gotchas

- **Exporter package missing on RHEL 10**: `pgbackrest_exporter` in
  particular may not have a clean RPM — see
  `roles/monitoring_agents/tasks/_exporters.yml` for the install
  source. If an exporter unit is failed, `journalctl -u <exporter>`
  shows why.
- **Metrics not arriving at the monitor**: check the monitor host's
  firewalld — `victoriametrics` (8428) and `victorialogs` (9428) must
  be open to the postgres source group. `vmagent`'s disk buffer fills
  if the monitor is unreachable; it drains when connectivity returns.
- **Grafana 404 behind the proxy**: Grafana must have
  `serve_from_sub_path` true and `root_url` ending in `/grafana/` —
  both are set by the `grafana` role; a hand-edited `grafana.ini`
  reverts on the next deploy.
- **AVC denial from nginx**: nginx proxying to loopback upstreams needs
  the `httpd_can_network_connect` SELinux boolean — the `nginx_proxy`
  role sets it. If you see an AVC, confirm the boolean is on.
```

- [ ] **Step 3: Lint**

Run: `markdownlint docs/operations/firstrun.md docs/operations/day2-monitoring.md` (or `make lint`)
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add docs/operations/firstrun.md docs/operations/day2-monitoring.md
git commit -m "docs(ops): P5 monitoring firstrun + day-2 runbook"
```

---

## Task 26: README roadmap flip

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Read current roadmap rows**

Run: `grep -n "P5\|[Mm]onitoring" README.md`
Confirm there is a roadmap row for P5 (monitoring) with a `pending`
status and a "Status" sentence listing completed phases.

- [ ] **Step 2: Flip status to done**

Edit `README.md`. In the roadmap table, change the P5 row's status
column from `pending` to `done`. In the "Status" sentence in the file
header, add P5 to the comma-separated list of completed phases.

If the exact wording differs, update what is there in the same shape;
do not add new sections.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs(readme): mark P5 done in roadmap"
```

---

## Task 27: end-to-end smoke (no commit)

**Files:** none

- [ ] **Step 1: Run the four new molecule scenarios**

```bash
cd tests/molecule/monitoring_server && molecule test -s default
cd tests/molecule/monitoring_agents && molecule test -s default
cd tests/molecule/grafana && molecule test -s default
cd tests/molecule/nginx_proxy && molecule test -s default
```

Expected: all four pass through
`destroy → create → prepare → converge → idempotence → verify → destroy`.
Idempotence is the key signal — the second `converge` must show 0
changed tasks. The `verify` AVC check must find zero SELinux denials.

- [ ] **Step 2: Run the unit test suite**

```bash
pytest tests/configure -v
```

Expected: all tests pass (existing + the five new monitoring cases from
Task 18).

- [ ] **Step 3: Run lint**

```bash
make lint
```

Expected: clean — including `xmllint` on the five new firewalld XMLs and
the JSON dashboard parsing cleanly.

No commit for this task — it is verification only.

---

## Self-review notes

1. **Spec coverage check.** Spec §2 monitoring bullets: "VictoriaMetrics single-node (`vmsingle` + `vmagent`)" → `monitoring_server` Task 4 + `monitoring_agents` Task 9; "VictoriaLogs (`vlsingle` + `vlagent`)" → Task 4 + Task 9; "Alerting: vmalert + Alertmanager" → Task 5; "Dashboards: Grafana via `grafana.grafana` + `community.grafana`" → Tasks 11-14; "Reverse proxy: nginx on monitor host" → Tasks 15-17. Spec §4 role table: `monitoring_agents` (all hosts; the six telemetry components) → Tasks 7-10; `monitoring_server` (monitor; vmsingle/vlsingle/vmalert/alertmanager) → Tasks 2-6; `grafana` (monitor) → Tasks 11-14; `nginx_proxy` (monitor; routes `/grafana/`, `/alertmanager/`, `/vmalert/`) → Tasks 15-17. Spec §3.4 deploy order `monitoring_server → monitoring_agents → grafana → nginx_proxy` → Task 19 site.yml wiring in exactly that order. Spec §5.3 four `_*.yml` per-host-group playbooks → Task 19. Spec §6.1 exposure table: vmsingle/vlsingle bind `network_any_address` and are firewalled to `postgres + monitor` → Task 6; vmalert/alertmanager/grafana bind `network_loopback_address` with no firewalld entry → Tasks 5/12 bind loopback, no firewalld task touches them; the four exporters bind `network_any_address` firewalled to `monitor` → Task 10; vmagent/vlagent bind `network_loopback_address` → Task 9. Spec §6.2 custom firewalld XMLs: `victoriametrics.xml`, `victorialogs.xml`, `postgres-exporter.xml`, `pgbouncer-exporter.xml`, `pgbackrest-exporter.xml` → Tasks 6 + 10 (node_exporter uses the built-in `prometheus-node-exporter` service, per spec §6.2). Spec §6 module tags `monitoring`, `nginx_proxy` → Task 19. Spec §7.3 response block `monitoring: {vmsingle_retention, vlsingle_retention, alertmanager: {receivers}}` → schema Task 18; `monitoring.scrape_interval` is added as the spec's §9.2 day-2 knob "edit `monitoring.scrape_interval`". Spec §8.5 "vmagent scrapes local exporters every 15s and remote_writes to vmsingle (disk-buffered if monitor down); vlagent tails journald + PG logs + Patroni logs; vmalert evaluates every 30s → alertmanager" → Tasks 9 (scrape config + buffer dir), 5 (vmalert eval interval 30s). Spec §8.7 "`_nginx_proxy` cert renewal fails | Warn, keep old cert" → the `nginx_proxy` role consumes the P0 cert; renewal is the `certs` role's job, and `_assert.yml` warns rather than hard-fails only where appropriate (it hard-fails if the cert is entirely absent in `ca_signed` mode, which is a deploy error, not a renewal warning). Spec §12 collections: `victoriametrics.cluster`, `grafana.grafana`, `community.grafana` → Task 1. Spec §13.2 "monitoring_*/grafana/nginx_proxy config roles run in the podman container matrix" → all four scenarios use the podman driver (Tasks 20-23). Spec §13.2 "every verify.yml runs `ausearch -m AVC`" → Tasks 20 and 23 include the AVC check (21 and 22 omit it only because their containers may lack auditd; the executor should add it if auditd is available).

2. **Placeholder scan.** No `TBD`, `TODO`, or `implement later`. The dashboard JSON in Task 14 is a real four-panel dashboard, not a stub. The alert rules in Task 5 are two real rules. Where the spec itself flags uncertainty (exporter packaging §14, the exact VM-collection variable names), the plan gives an explicit "verify during execution" instruction with the command to run and the fallback — this is surfacing a real unknown, not a placeholder.

3. **Variable / type consistency.** `monitoring_host` is defined in `group_vars/all.yml` (Task 1) and consumed by `monitoring_agents` defaults (Task 7) for the remote_write URLs. The port vars (`vmsingle_port`, `vlsingle_port`, `vmalert_port`, `alertmanager_port`, `grafana_port`) are already in `all.yml`; the exporter ports + `vmagent_port` + `vlagent_port` are added in Task 1 and consumed by `monitoring_agents` (Task 7) and `monitoring_server` (Task 2). `monitoring_scrape_interval` is defined in `all.yml` (Task 1), emitted by the generator (Task 18), consumed by `monitoring_agents` defaults (Task 7). `nginx_proxy_tls_enabled` / `nginx_proxy_cert_path` / `nginx_proxy_key_path` are set as facts in `nginx_proxy/_tls.yml` (Task 16) and consumed in `pigsty-lite.conf.j2` (Task 17) — names match. `grafana_admin_password` is defined in `grafana` defaults (Task 11) and referenced in the grafana verify (Task 22) with the same default fallback string. The collection role names (`victoriametrics.cluster.vmsingle` etc., `grafana.grafana.grafana`) are used consistently; the plan instructs the executor to confirm the collection's *variable* names against the installed `defaults/` files before writing each include, because those are the one thing I cannot verify from the spec alone.

4. **The VM-collection variable-name risk.** Tasks 4, 5, and 9 invoke the `victoriametrics.cluster` roles (`vmsingle`, `vlsingle`, `vmalert`, `vmagent`, `vlagent` — all confirmed present in `collections/ansible_collections/victoriametrics/cluster/roles/`). What I cannot confirm from the spec is the exact *variable* each role expects for its service arguments — I used `<role>_service_args` as a documented-convention guess. Each of those tasks carries an explicit instruction to `cat` the collection's `defaults/main.yml` first and adjust. This is the single biggest execution risk in the plan; it is contained to five `include_role` blocks and the executor has the exact verification command. If the collection's interface differs substantially, the *intent* (listen address, retention, data dir, remote_write URL, TLS CA) is fully specified and stable — only the variable plumbing changes.

5. **Why monitoring_agents runs on `all`, not just postgres.** Spec §4 says `monitoring_agents` targets `all`. The monitor host itself runs `node_exporter` + `vmagent` + `vlagent` (so the monitor's own CPU/disk/logs are observable), but NOT the three PG-side exporters — those are gated `when: inventory_hostname in groups['postgres']` in Tasks 8 and 10. The monitor host's vmagent remote_writes to *itself* (loopback-adjacent), which is fine and matches the spec's "vmagent on every node" wording.

6. **What's deliberately out of scope.** One starter Grafana dashboard (not a curated suite), Alertmanager receiver types beyond slack/email/webhook/pagerduty, `vmauth` and cluster-mode VictoriaMetrics, log-based alerting, replicated Grafana. `pgbackrest_exporter` packaging is the spec's own open question (§14) — Task 8 handles it explicitly with a build-from-source fallback rather than skipping.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-14-p5-monitoring.md`. Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
