# P2b (Connection Layer: pgBouncer + HAProxy + vip-manager) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put a real client-facing front end on the P2a Patroni cluster: a per-host pgBouncer (port 6432) for connection pooling, a per-host HAProxy that uses Patroni's REST `/leader` and `/replica` endpoints to route 5432→primary, 5433→primary-only RW, 5434→replicas RO, and an optional vip-manager that binds an L2 VIP to whichever node is currently leader. After P2b an application can connect to any postgres host on 5432/5433/5434 and the connection lands on the right backend even after a failover.

**Architecture:** Three new thin roles all targeted at the `postgres` group. `roles/pgbouncer/` installs `pgbouncer` from PGDG, renders `pgbouncer.ini`+`userlist.txt`, opens nothing in firewalld by default (the role enables the service but the `pgbouncer` firewalld service stays disabled — apps go via HAProxy). `roles/haproxy/` installs `haproxy` from vendor, renders a single config with three `frontend`/`backend` pairs that all health-check via Patroni REST, opens firewalld `postgresql` (5432) and the custom `haproxy-postgres` service (5433+5434), and binds the stats socket to loopback. `roles/vip_manager/` installs `vip-manager` from PGDG-extras, renders its YAML config to poll Patroni REST and manage an L2 VIP on a named interface. vip-manager is **gated off by default** (`vip_manager_enabled: false`) so a cluster without a spare IP address sees a no-op. P2b wires three new internal playbooks (`_pgbouncer.yml`, `_haproxy.yml`, `_vip_manager.yml`) into `site.yml` after `_postgres_bootstrap.yml`. Two Molecule scenarios: a combined `default` scenario that exercises pgbouncer+haproxy on a single-node Patroni cluster, and an `ha` scenario for haproxy specifically that proves 5433 hits the leader, 5434 hits a replica, and that 5433 follows the leader after a `patronictl switchover`.

**Tech Stack:** Ansible role + Jinja2, pgBouncer from PGDG, HAProxy from vendor (RHEL/Rocky/Alma 10 ships 2.4+), vip-manager from PGDG-extras, `community.general.sefcontext` + `seport`, `ansible.posix.firewalld`, `community.postgresql.postgresql_query` for switchover assertions in tests, `python3-psycopg3` (already present from P2a) for psql connectivity probes, Molecule + podman.

---

## File Structure

**New files (in `roles/pgbouncer/`):**

- `roles/pgbouncer/defaults/main.yml` — package, port, listen address, auth_type, pool_mode, max_client_conn, default_pool_size, admin/stats users, log dir, config + userlist paths, systemd unit name.
- `roles/pgbouncer/meta/main.yml` — galaxy_info, no deps.
- `roles/pgbouncer/tasks/main.yml` — orchestrate: assert → install → fcontext (log dir, custom port) → render `pgbouncer.ini` and `userlist.txt` → systemd enable+start → wait for port → optional firewalld.
- `roles/pgbouncer/tasks/_assert.yml` — postgres role complete; patroni dbsu password available.
- `roles/pgbouncer/tasks/_install.yml` — `dnf install pgbouncer`, support packages.
- `roles/pgbouncer/tasks/_filesystem.yml` — log dir, run dir, SELinux for non-default paths.
- `roles/pgbouncer/tasks/_configure.yml` — render config + userlist, handler triggers reload (not restart — pgbouncer supports `RELOAD` over the admin console for most settings).
- `roles/pgbouncer/tasks/_service.yml` — enable, start, `wait_for` 6432.
- `roles/pgbouncer/tasks/_firewall.yml` — open `pgbouncer` only when `pgbouncer_firewalld_enabled: true`.
- `roles/pgbouncer/templates/pgbouncer.ini.j2` — single source of truth.
- `roles/pgbouncer/templates/userlist.txt.j2` — `"user" "password"` lines, mode 0600.
- `roles/pgbouncer/handlers/main.yml` — `Reload pgbouncer` (`systemctl reload`), `Restart pgbouncer` (only when needed).
- `roles/pgbouncer/README.md`.

**New files (in `roles/haproxy/`):**

- `roles/haproxy/defaults/main.yml` — package, port assignments (5432/5433/5434/7000), stats credentials, RTO profile, Patroni REST scheme/port, health-check intervals, max conns.
- `roles/haproxy/meta/main.yml`.
- `roles/haproxy/tasks/main.yml` — orchestrate: assert → install → fcontext for non-default paths → render `haproxy.cfg` → systemd → firewalld → wait_for ports.
- `roles/haproxy/tasks/_assert.yml` — postgres group non-empty; patroni REST cert present.
- `roles/haproxy/tasks/_install.yml` — `dnf install haproxy`.
- `roles/haproxy/tasks/_configure.yml` — render `/etc/haproxy/haproxy.cfg`, install custom systemd drop-in only if non-default paths used.
- `roles/haproxy/tasks/_firewall.yml` — open built-in `postgresql` (5432) plus custom `haproxy-postgres` (5433+5434).
- `roles/haproxy/tasks/_service.yml` — enable, start, `wait_for` 5432/5433/5434.
- `roles/haproxy/tasks/_selinux.yml` — `seboolean haproxy_connect_any=on` (HAProxy connects to arbitrary Patroni REST ports across nodes).
- `roles/haproxy/templates/haproxy.cfg.j2` — global, defaults, three frontend/backend pairs, stats listener bound to `network_loopback_address:7000`.
- `roles/haproxy/handlers/main.yml` — `Reload haproxy` (systemctl reload — haproxy supports hot config reload).
- `roles/haproxy/README.md`.

**New files (in `roles/vip_manager/`):**

- `roles/vip_manager/defaults/main.yml` — package, interface, VIP CIDR, dcs config (etcd hosts + TLS), trigger key, retry/loop intervals, `vip_manager_enabled` default false.
- `roles/vip_manager/meta/main.yml`.
- `roles/vip_manager/tasks/main.yml` — orchestrate: short-circuit when disabled → assert → install → render → systemd.
- `roles/vip_manager/tasks/_assert.yml` — interface present; VIP not already bound elsewhere outside this role's management (warn-only); etcd reachable.
- `roles/vip_manager/tasks/_install.yml` — `dnf install vip-manager` (PGDG-extras).
- `roles/vip_manager/tasks/_configure.yml` — render `/etc/vip-manager.yml`.
- `roles/vip_manager/tasks/_service.yml` — enable + start, wait for `vip-manager.service` active.
- `roles/vip_manager/templates/vip-manager.yml.j2` — DCS endpoints (from `groups['etcd']`), trigger-key (`/<scope>/leader`), interface, VIP CIDR.
- `roles/vip_manager/handlers/main.yml` — `Restart vip-manager`.
- `roles/vip_manager/README.md`.

**New firewalld services:**

- `files/firewalld/services/haproxy-postgres.xml` — ports 5433/tcp and 5434/tcp.
- `files/firewalld/services/pgbouncer.xml` — port 6432/tcp.

**New playbook + wiring:**

- `playbooks/_pgbouncer.yml` — runs `pgbouncer` role on `postgres` group.
- `playbooks/_haproxy.yml` — runs `haproxy` role on `postgres` group.
- `playbooks/_vip_manager.yml` — runs `vip_manager` role on `postgres` group (the role no-ops when disabled).
- `playbooks/site.yml` — modify to import the three playbooks after `_postgres_bootstrap.yml`.
- `playbooks/tags.md` — add `pgbouncer`, `haproxy`, `vip_manager` module tags.
- `group_vars/postgres.yml` — add `pgbouncer_admin_users`, `haproxy_stats_user`, `haproxy_stats_password`, `vip_manager_enabled` (false).
- `group_vars/response.yml` shape additions — handled by `bin/_generate_response_vars.py` so the response file can drive vip-manager's enablement and stats credentials.

**Modified files:**

- `bin/_generate_response_vars.py` — emit `vip_manager_enabled`, `vip_manager_interface`, `vip_manager_vip_cidr`, `haproxy_rto_profile` from the response file.
- `bin/_response_schema.py` — accept new optional `vip_manager:` and `haproxy:` sections (both optional with safe defaults).
- `responses/single.rsp.yml.example` and `responses/ha.rsp.yml.example` — show the new optional blocks commented out.
- `.github/workflows/molecule.yml` — extend matrix with `pgbouncer-default`, `haproxy-default`, `haproxy-ha`, `vip_manager-default` (the latter is a "disabled by default => no-op" idempotence test).
- `docs/operations/firstrun.md` — add a P2b section.
- `README.md` — flip P2b to done in the roadmap.

**New test files:**

- `tests/molecule/pgbouncer/molecule/default/{molecule,prepare,converge,verify}.yml` — single-node, full P0+P1+P2a stack underneath.
- `tests/molecule/haproxy/molecule/default/{molecule,prepare,converge,verify}.yml` — single-node, asserts 5432/5433 routes to local Patroni primary.
- `tests/molecule/haproxy/molecule/ha/{molecule,prepare,converge,verify}.yml` — 3-node, asserts 5433 follows leader after `patronictl switchover`, 5434 hits a replica.
- `tests/molecule/vip_manager/molecule/default/{molecule,prepare,converge,verify}.yml` — assert the role is a no-op when `vip_manager_enabled=false` (the default).

vip-manager's real (`enabled=true`) behavior is intentionally NOT covered by Molecule. Binding an L2 VIP requires `NET_ADMIN` plus a network interface that podman doesn't reliably model. The runbook for vip-manager + a libvirt VM smoke test is in Task 23 (optional, local only).

---

## Task 1: pgBouncer role defaults

**Files:**
- Create: `roles/pgbouncer/defaults/main.yml`

- [ ] **Step 1: Write defaults**

```yaml
---
# roles/pgbouncer/defaults/main.yml
# Variables prefixed `pgbouncer_`. pgBouncer 1.21+ from PGDG; vendor paths
# (/etc/pgbouncer, /var/log/pgbouncer) so SELinux fcontext is inherited.

# Package
pgbouncer_package: pgbouncer
pgbouncer_service_name: pgbouncer

# Filesystem (vendor defaults)
pgbouncer_config_dir: /etc/pgbouncer
pgbouncer_config_file: "{{ pgbouncer_config_dir }}/pgbouncer.ini"
pgbouncer_userlist_file: "{{ pgbouncer_config_dir }}/userlist.txt"
pgbouncer_log_dir: /var/log/pgbouncer
pgbouncer_run_dir: /var/run/pgbouncer
pgbouncer_pid_file: "{{ pgbouncer_run_dir }}/pgbouncer.pid"
pgbouncer_user: pgbouncer
pgbouncer_group: pgbouncer

# Network
pgbouncer_listen_address: "{{ network_any_address | default('0.0.0.0') }}"
pgbouncer_listen_port: 6432
pgbouncer_unix_socket_dir: "{{ pgbouncer_run_dir }}"

# Auth
pgbouncer_auth_type: scram-sha-256
pgbouncer_auth_file: "{{ pgbouncer_userlist_file }}"
pgbouncer_auth_user: "{{ patroni_superuser | default('postgres') }}"
# Admin and stats users that can connect to the special `pgbouncer` database
pgbouncer_admin_users: ["postgres"]
pgbouncer_stats_users: ["postgres"]

# Pooling
pgbouncer_pool_mode: transaction
pgbouncer_max_client_conn: 1000
pgbouncer_default_pool_size: 25
pgbouncer_reserve_pool_size: 5
pgbouncer_server_idle_timeout: 600
pgbouncer_server_lifetime: 3600

# Upstream (local Patroni-managed PG)
pgbouncer_upstream_host: "{{ network_loopback_address | default('127.0.0.1') }}"
pgbouncer_upstream_port: "{{ postgres_port | default(5432) }}"

# Logging
pgbouncer_log_file: "{{ pgbouncer_log_dir }}/pgbouncer.log"
pgbouncer_log_connections: 1
pgbouncer_log_disconnections: 1
pgbouncer_log_pooler_errors: 1

# Firewalld (off by default - clients go via HAProxy)
pgbouncer_firewalld_enabled: false
pgbouncer_firewalld_zone: "{{ firewalld_default_zone | default('public') }}"
pgbouncer_firewalld_service_name: pgbouncer

# Userlist content. Operators add business users by overriding
# pgbouncer_userlist_extra; the system entries below are always present.
pgbouncer_userlist_system_entries:
  - { user: "{{ patroni_superuser | default('postgres') }}", password: "{{ patroni_superuser_password }}" }
pgbouncer_userlist_extra: []
```

- [ ] **Step 2: Lint**

Run: `yamllint roles/pgbouncer/defaults/main.yml`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add roles/pgbouncer/defaults/main.yml
git commit -m "feat(pgbouncer): role defaults"
```

---

## Task 2: pgBouncer meta and README

**Files:**
- Create: `roles/pgbouncer/meta/main.yml`
- Create: `roles/pgbouncer/README.md`

- [ ] **Step 1: `meta/main.yml`**

```yaml
---
galaxy_info:
  role_name: pgbouncer
  author: pigsty-lite
  description: Install and configure pgBouncer as a per-host PostgreSQL connection pool.
  license: Apache-2.0
  min_ansible_version: "2.16"
  platforms:
    - name: EL
      versions: ["10"]
dependencies: []
```

- [ ] **Step 2: `README.md`**

````markdown
# pgbouncer

Per-host pgBouncer sidecar listening on port 6432. Pools client
connections to the local Patroni-managed PostgreSQL instance on
`127.0.0.1:5432`. pgBouncer never talks to a remote PG — it always
points at the local PG, and HAProxy upstream decides which node clients
hit. This keeps the "no pooler split-brain on failover" property:
pgBouncer doesn't have to know about leader changes; it just dies when
PG dies and is recreated when PG comes back.

## Auth

`scram-sha-256`. The userlist is rendered from
`pgbouncer_userlist_system_entries` + `pgbouncer_userlist_extra`.
System entries include the postgres superuser by default. Add business
users via the response file → `pgbouncer_userlist_extra`.

## Firewall

The `pgbouncer` firewalld service ships with the project but is
**disabled by default**. Clients connect via HAProxy on 5432; pgBouncer
is reached only locally over `127.0.0.1:6432`. If you really want
external pgBouncer access, set `pgbouncer_firewalld_enabled: true`.

## Reload vs restart

Most settings reload via `pgbouncer -R`; this role uses
`systemctl reload` which sends `SIGHUP`. A handful of settings require
restart (port, listen_addr) — those are gated to fire the
`Restart pgbouncer` handler explicitly in `_configure.yml`.
````

- [ ] **Step 3: Commit**

```bash
git add roles/pgbouncer/meta roles/pgbouncer/README.md
git commit -m "feat(pgbouncer): role meta and README"
```

---

## Task 3: pgBouncer tasks

**Files:**
- Create: `roles/pgbouncer/tasks/main.yml`
- Create: `roles/pgbouncer/tasks/_assert.yml`
- Create: `roles/pgbouncer/tasks/_install.yml`
- Create: `roles/pgbouncer/tasks/_filesystem.yml`
- Create: `roles/pgbouncer/tasks/_configure.yml`
- Create: `roles/pgbouncer/tasks/_service.yml`
- Create: `roles/pgbouncer/tasks/_firewall.yml`

- [ ] **Step 1: `main.yml`**

```yaml
---
- name: Validate preconditions
  ansible.builtin.import_tasks: _assert.yml
  tags: [pgbouncer, assert]

- name: Install pgBouncer
  ansible.builtin.import_tasks: _install.yml
  tags: [pgbouncer, install]

- name: Prepare filesystem
  ansible.builtin.import_tasks: _filesystem.yml
  tags: [pgbouncer, install]

- name: Render pgBouncer configuration
  ansible.builtin.import_tasks: _configure.yml
  tags: [pgbouncer, config]

- name: Start pgBouncer
  ansible.builtin.import_tasks: _service.yml
  tags: [pgbouncer, service]

- name: Apply firewalld rule (off by default)
  ansible.builtin.import_tasks: _firewall.yml
  tags: [pgbouncer, firewall]
```

- [ ] **Step 2: `_assert.yml`**

```yaml
---
- name: Fail if patroni superuser password is not defined
  ansible.builtin.assert:
    that:
      - patroni_superuser_password is defined
      - patroni_superuser_password | length > 0
    fail_msg: >-
      pgBouncer userlist requires `patroni_superuser_password`. Set it via
      response file or group_vars/postgres.yml. See P2a.

- name: Fail if postgres binary missing
  ansible.builtin.stat:
    path: "/usr/pgsql-{{ postgres_version }}/bin/postgres"
  register: pgb_pg_bin_stat

- name: Assert postgres present
  ansible.builtin.assert:
    that:
      - pgb_pg_bin_stat.stat.exists
    fail_msg: >-
      pgBouncer requires the postgres role to have run first
      (P2a _postgres_install.yml).
```

- [ ] **Step 3: `_install.yml`**

```yaml
---
- name: Install pgBouncer
  ansible.builtin.dnf:
    name: "{{ pgbouncer_package }}"
    state: present
```

- [ ] **Step 4: `_filesystem.yml`**

```yaml
---
- name: Ensure pgbouncer log directory exists
  ansible.builtin.file:
    path: "{{ pgbouncer_log_dir }}"
    state: directory
    owner: "{{ pgbouncer_user }}"
    group: "{{ pgbouncer_group }}"
    mode: "0750"

- name: Ensure pgbouncer run directory exists
  ansible.builtin.file:
    path: "{{ pgbouncer_run_dir }}"
    state: directory
    owner: "{{ pgbouncer_user }}"
    group: "{{ pgbouncer_group }}"
    mode: "0755"
```

- [ ] **Step 5: `_configure.yml`**

```yaml
---
- name: Render pgbouncer.ini
  ansible.builtin.template:
    src: pgbouncer.ini.j2
    dest: "{{ pgbouncer_config_file }}"
    owner: "{{ pgbouncer_user }}"
    group: "{{ pgbouncer_group }}"
    mode: "0640"
  notify: Reload pgbouncer

- name: Render userlist.txt
  ansible.builtin.template:
    src: userlist.txt.j2
    dest: "{{ pgbouncer_userlist_file }}"
    owner: "{{ pgbouncer_user }}"
    group: "{{ pgbouncer_group }}"
    mode: "0600"
  notify: Reload pgbouncer
```

- [ ] **Step 6: `_service.yml`**

```yaml
---
- name: Enable and start pgbouncer
  ansible.builtin.systemd:
    name: "{{ pgbouncer_service_name }}"
    enabled: true
    state: started

- name: Wait for pgbouncer to listen on port {{ pgbouncer_listen_port }}
  ansible.builtin.wait_for:
    host: "{{ network_loopback_address | default('127.0.0.1') }}"
    port: "{{ pgbouncer_listen_port }}"
    timeout: 30
```

- [ ] **Step 7: `_firewall.yml`**

```yaml
---
- name: Install pgbouncer firewalld service definition
  ansible.builtin.copy:
    src: "{{ playbook_dir | dirname }}/files/firewalld/services/pgbouncer.xml"
    dest: /etc/firewalld/services/pgbouncer.xml
    owner: root
    group: root
    mode: "0644"
  register: pgb_fw_svc

- name: Reload firewalld if service definition changed
  ansible.builtin.command: firewall-cmd --reload
  when: pgb_fw_svc.changed
  changed_when: true

- name: Open pgbouncer in default zone (only when explicitly enabled)
  ansible.posix.firewalld:
    service: "{{ pgbouncer_firewalld_service_name }}"
    permanent: true
    state: "{{ 'enabled' if (pgbouncer_firewalld_enabled | bool) else 'disabled' }}"
    immediate: true
    zone: "{{ pgbouncer_firewalld_zone }}"
```

- [ ] **Step 8: Commit**

```bash
git add roles/pgbouncer/tasks
git commit -m "feat(pgbouncer): install, configure, service, firewall tasks"
```

---

## Task 4: pgBouncer templates and handlers

**Files:**
- Create: `roles/pgbouncer/templates/pgbouncer.ini.j2`
- Create: `roles/pgbouncer/templates/userlist.txt.j2`
- Create: `roles/pgbouncer/handlers/main.yml`

- [ ] **Step 1: `pgbouncer.ini.j2`**

```ini
; {{ ansible_managed }}
; Rendered by roles/pgbouncer. Do not edit by hand.

[databases]
* = host={{ pgbouncer_upstream_host }} port={{ pgbouncer_upstream_port }}

[pgbouncer]
listen_addr = {{ pgbouncer_listen_address }}
listen_port = {{ pgbouncer_listen_port }}
unix_socket_dir = {{ pgbouncer_unix_socket_dir }}
pidfile = {{ pgbouncer_pid_file }}

auth_type = {{ pgbouncer_auth_type }}
auth_file = {{ pgbouncer_auth_file }}
auth_user = {{ pgbouncer_auth_user }}

admin_users = {{ pgbouncer_admin_users | join(',') }}
stats_users = {{ pgbouncer_stats_users | join(',') }}

pool_mode = {{ pgbouncer_pool_mode }}
max_client_conn = {{ pgbouncer_max_client_conn }}
default_pool_size = {{ pgbouncer_default_pool_size }}
reserve_pool_size = {{ pgbouncer_reserve_pool_size }}
server_idle_timeout = {{ pgbouncer_server_idle_timeout }}
server_lifetime = {{ pgbouncer_server_lifetime }}

logfile = {{ pgbouncer_log_file }}
log_connections = {{ pgbouncer_log_connections }}
log_disconnections = {{ pgbouncer_log_disconnections }}
log_pooler_errors = {{ pgbouncer_log_pooler_errors }}

ignore_startup_parameters = extra_float_digits,search_path,application_name
```

- [ ] **Step 2: `userlist.txt.j2`**

```jinja
{# {{ ansible_managed }} #}
{% for entry in pgbouncer_userlist_system_entries + pgbouncer_userlist_extra %}
"{{ entry.user }}" "{{ entry.password }}"
{% endfor %}
```

- [ ] **Step 3: `handlers/main.yml`**

```yaml
---
- name: Reload pgbouncer
  ansible.builtin.systemd:
    name: "{{ pgbouncer_service_name }}"
    state: reloaded

- name: Restart pgbouncer
  ansible.builtin.systemd:
    name: "{{ pgbouncer_service_name }}"
    state: restarted
```

- [ ] **Step 4: Commit**

```bash
git add roles/pgbouncer/templates roles/pgbouncer/handlers
git commit -m "feat(pgbouncer): templates and handlers"
```

---

## Task 5: pgBouncer firewalld XML

**Files:**
- Create: `files/firewalld/services/pgbouncer.xml`

- [ ] **Step 1: Write XML**

```xml
<?xml version="1.0" encoding="utf-8"?>
<service>
  <short>pgbouncer</short>
  <description>pgBouncer connection pooler. Disabled by default; apps go via HAProxy.</description>
  <port protocol="tcp" port="6432"/>
</service>
```

- [ ] **Step 2: Lint XML**

Run: `xmllint --noout files/firewalld/services/pgbouncer.xml`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add files/firewalld/services/pgbouncer.xml
git commit -m "feat(firewalld): pgbouncer custom service (off by default)"
```

---

## Task 6: pgBouncer molecule scenario

**Files:**
- Create: `tests/molecule/pgbouncer/molecule/default/molecule.yml`
- Create: `tests/molecule/pgbouncer/molecule/default/prepare.yml`
- Create: `tests/molecule/pgbouncer/molecule/default/converge.yml`
- Create: `tests/molecule/pgbouncer/molecule/default/verify.yml`

- [ ] **Step 1: `molecule.yml`**

```yaml
---
driver:
  name: podman
platforms:
  - name: pigsty-lite-pgbouncer
    image: docker.io/rockylinux/rockylinux:10-ubi-init
    pre_build_image: true
    privileged: true
    command: /usr/sbin/init
    volume_mounts:
      - "/sys/fs/cgroup:/sys/fs/cgroup:rw"
    capabilities: [SYS_ADMIN]
    systemd: always
    groups: [etcd, postgres]
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
        postgres_port: 5432
        certs_subject_alternative_names:
          - "DNS:{{ inventory_hostname }}"
          - "DNS:{{ inventory_hostname }}.test.local"
          - "IP:127.0.0.1"
      etcd:
        etcd_initial_cluster_state: new
      postgres:
        patroni_superuser_password: superuser-test-pw
        patroni_replication_password: replicator-test-pw
        patroni_rewind_password: rewind-test-pw
        postgres_tune_profile: tiny
    host_vars:
      pigsty-lite-pgbouncer:
        ansible_host: 127.0.0.1
        postgres_role: primary
        etcd_seq: 1
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
- name: Prepare - generate CA on control side
  hosts: localhost
  gather_facts: false
  vars:
    ca_dir: "{{ playbook_dir }}/_tmp_ca"
  roles:
    - role: ../../../../../roles/ca

- name: Prepare - repos, certs, etcd, postgres, patroni
  hosts: all
  gather_facts: true
  become: true
  vars:
    certs_ca_dir_on_control: "{{ playbook_dir }}/_tmp_ca"
    repos_pgdg_enabled: true
    repos_pgdg_rpm_url: "https://download.postgresql.org/pub/repos/yum/reporpms/EL-10-x86_64/pgdg-redhat-repo-latest.noarch.rpm"
    network_any_address: 0.0.0.0
    network_loopback_address: 127.0.0.1
    firewalld_default_zone: public
  roles:
    - role: ../../../../../roles/repos
    - role: ../../../../../roles/certs
    - role: ../../../../../roles/etcd
    - role: ../../../../../roles/postgres
    - role: ../../../../../roles/patroni
```

- [ ] **Step 3: `converge.yml`**

```yaml
---
- name: Converge - apply pgbouncer role
  hosts: postgres
  become: true
  gather_facts: true
  vars:
    network_any_address: 0.0.0.0
    network_loopback_address: 127.0.0.1
    firewalld_default_zone: public
  roles:
    - role: ../../../../../roles/pgbouncer
```

- [ ] **Step 4: `verify.yml`**

```yaml
---
- name: Verify
  hosts: postgres
  become: true
  tasks:
    - name: pgbouncer service active
      ansible.builtin.systemd:
        name: pgbouncer
      register: pgb_unit

    - name: Assert pgbouncer active
      ansible.builtin.assert:
        that:
          - pgb_unit.status.ActiveState == "active"

    - name: psql via pgbouncer (postgres user, scram auth)
      ansible.builtin.command: >
        /usr/pgsql-18/bin/psql
        "host=127.0.0.1 port=6432 dbname=postgres user=postgres password=superuser-test-pw sslmode=disable"
        -tAc "select 'pgbouncer-ok'"
      register: pgb_probe
      changed_when: false

    - name: Assert psql via pgbouncer returns the probe row
      ansible.builtin.assert:
        that:
          - "'pgbouncer-ok' in pgb_probe.stdout"

    - name: pgbouncer firewalld rule is NOT in default zone (disabled by default)
      ansible.builtin.command: firewall-cmd --zone=public --query-service=pgbouncer
      register: pgb_fw_query
      changed_when: false
      failed_when: false

    - name: Assert pgbouncer firewalld rule is disabled by default
      ansible.builtin.assert:
        that:
          - pgb_fw_query.rc != 0 or 'no' in pgb_fw_query.stdout
```

- [ ] **Step 5: Run scenario**

Run: `cd tests/molecule/pgbouncer && molecule test -s default`
Expected: PASS including idempotence.

- [ ] **Step 6: Commit**

```bash
git add tests/molecule/pgbouncer
git commit -m "test(pgbouncer): default molecule scenario"
```

---

## Task 7: HAProxy role defaults

**Files:**
- Create: `roles/haproxy/defaults/main.yml`

- [ ] **Step 1: Write defaults**

```yaml
---
# roles/haproxy/defaults/main.yml
# Variables prefixed `haproxy_`. HAProxy is the only inbound for PG
# clients in the standard topology. Three frontends, three backends, all
# health-checked via Patroni REST.

haproxy_package: haproxy
haproxy_service_name: haproxy

# Filesystem (vendor defaults)
haproxy_config_dir: /etc/haproxy
haproxy_config_file: "{{ haproxy_config_dir }}/haproxy.cfg"

# Ports
haproxy_default_port: "{{ haproxy_default_port | default(5432) }}"
haproxy_primary_port: "{{ haproxy_primary_port | default(5433) }}"
haproxy_replica_port: "{{ haproxy_replica_port | default(5434) }}"
haproxy_stats_port: "{{ haproxy_stats_port | default(7000) }}"

# Bind addresses
haproxy_listen_address: "{{ network_any_address | default('0.0.0.0') }}"
haproxy_stats_listen_address: "{{ network_loopback_address | default('127.0.0.1') }}"

# Stats
haproxy_stats_user: pigsty
haproxy_stats_password: "{{ vault_haproxy_stats_password | default('haproxy-dev-stats-change-me') }}"
haproxy_stats_refresh_seconds: 10

# Health-check tuning. The three RTO profiles control how aggressively
# HAProxy declares Patroni unhealthy. Defaults map to the spec's `norm`
# profile (~45s end-to-end RTO).
haproxy_rto_profile: norm  # tight | norm | loose
haproxy_check_intervals:
  tight: { inter: 1000, fall: 2, rise: 2 }
  norm:  { inter: 3000, fall: 3, rise: 2 }
  loose: { inter: 5000, fall: 5, rise: 2 }
haproxy_check_interval_ms: "{{ haproxy_check_intervals[haproxy_rto_profile].inter }}"
haproxy_check_fall: "{{ haproxy_check_intervals[haproxy_rto_profile].fall }}"
haproxy_check_rise: "{{ haproxy_check_intervals[haproxy_rto_profile].rise }}"

# Patroni REST upstream config
haproxy_patroni_rest_port: "{{ patroni_rest_port | default(8008) }}"
haproxy_patroni_rest_scheme: https
haproxy_pgbouncer_port: "{{ pgbouncer_listen_port | default(6432) }}"

# Backend connection target. Two valid choices:
#   `postgres`  - HAProxy goes directly to PG on 5432 (skips pgbouncer)
#   `pgbouncer` - HAProxy goes through the local pgbouncer on 6432
# `pgbouncer` is the default because that's the architecture the design
# describes (HAProxy in front, pgBouncer pooling per host).
haproxy_backend_target: pgbouncer  # pgbouncer | postgres
haproxy_backend_port: >-
  {{ haproxy_pgbouncer_port if haproxy_backend_target == 'pgbouncer'
     else (postgres_port | default(5432)) }}

# Limits
haproxy_maxconn: 4096

# Firewalld
haproxy_firewalld_zone: "{{ firewalld_default_zone | default('public') }}"
```

- [ ] **Step 2: Lint**

Run: `yamllint roles/haproxy/defaults/main.yml`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add roles/haproxy/defaults/main.yml
git commit -m "feat(haproxy): role defaults"
```

---

## Task 8: HAProxy meta and README

**Files:**
- Create: `roles/haproxy/meta/main.yml`
- Create: `roles/haproxy/README.md`

- [ ] **Step 1: `meta/main.yml`**

```yaml
---
galaxy_info:
  role_name: haproxy
  author: pigsty-lite
  description: Per-host HAProxy routing PG clients to Patroni leader / replicas via REST health checks.
  license: Apache-2.0
  min_ansible_version: "2.16"
  platforms:
    - name: EL
      versions: ["10"]
dependencies: []
```

- [ ] **Step 2: `README.md`**

````markdown
# haproxy

Local HAProxy on every postgres node. Three TCP frontends:

| Frontend | Port | Backend                          | Purpose                           |
|----------|------|----------------------------------|-----------------------------------|
| default  | 5432 | All members; HEALTH=`/leader`    | Generic; clients use this         |
| primary  | 5433 | All members; HEALTH=`/leader`    | Explicit RW (same as default)     |
| replica  | 5434 | All members; HEALTH=`/replica`   | Explicit RO; load-balanced        |

Health checks talk to Patroni REST (`/leader` returns 200 on the leader,
503 elsewhere; `/replica` returns 200 on a running replica). HAProxy
uses TLS for the health checks (Patroni REST requires it).

## Why both 5432 and 5433 go to the leader

Spec §3.1: "5432 → default = primary, 5433 → rw = primary only, 5434 → ro".
Apps that don't care about RW/RO use 5432. Apps that want to be explicit
use 5433 (RW) or 5434 (RO). Same backend health rule for default and
primary so an upgrade to "split RW/RO" doesn't require a config change
in the app.

## Backend target

`haproxy_backend_target: pgbouncer` (default) routes through the local
pgBouncer on 6432. Set to `postgres` to bypass pooling entirely (clients
hit PG on 5432 directly). The dynamic default in
`haproxy_backend_port` picks the right port.

## Stats

HTTP stats listen on `127.0.0.1:7000` (loopback only). Nginx_proxy (P5)
exposes them at `/haproxy-stats/` if you want a UI. Default credentials
in `defaults/main.yml`; override in the response file.

## SELinux

HAProxy on RHEL/Rocky/Alma must connect to arbitrary TCP ports across
hosts (the per-node Patroni REST on 8008). Enable
`haproxy_connect_any` SELinux boolean; the role does this for you.

## Reload not restart

Config changes trigger `systemctl reload`. HAProxy supports zero-drop
reload via socat / runtime API; the systemd unit handles the dance.
````

- [ ] **Step 3: Commit**

```bash
git add roles/haproxy/meta roles/haproxy/README.md
git commit -m "feat(haproxy): role meta and README"
```

---

## Task 9: HAProxy tasks

**Files:**
- Create: `roles/haproxy/tasks/main.yml`
- Create: `roles/haproxy/tasks/_assert.yml`
- Create: `roles/haproxy/tasks/_install.yml`
- Create: `roles/haproxy/tasks/_configure.yml`
- Create: `roles/haproxy/tasks/_firewall.yml`
- Create: `roles/haproxy/tasks/_service.yml`
- Create: `roles/haproxy/tasks/_selinux.yml`

- [ ] **Step 1: `main.yml`**

```yaml
---
- name: Validate preconditions
  ansible.builtin.import_tasks: _assert.yml
  tags: [haproxy, assert]

- name: Install HAProxy
  ansible.builtin.import_tasks: _install.yml
  tags: [haproxy, install]

- name: Configure SELinux booleans
  ansible.builtin.import_tasks: _selinux.yml
  tags: [haproxy, install, selinux]

- name: Render HAProxy config
  ansible.builtin.import_tasks: _configure.yml
  tags: [haproxy, config]

- name: Open HAProxy ports in firewalld
  ansible.builtin.import_tasks: _firewall.yml
  tags: [haproxy, firewall]

- name: Start HAProxy
  ansible.builtin.import_tasks: _service.yml
  tags: [haproxy, service]
```

- [ ] **Step 2: `_assert.yml`**

```yaml
---
- name: Fail if postgres group is empty
  ansible.builtin.assert:
    that:
      - groups['postgres'] | length > 0
    fail_msg: "HAProxy requires at least one host in the 'postgres' group."
  run_once: true
  delegate_to: localhost

- name: Stat patroni-rest firewalld service (P2a should have installed it)
  ansible.builtin.stat:
    path: /etc/firewalld/services/patroni-rest.xml
  register: hap_patroni_svc_stat

- name: Warn if patroni-rest firewalld service missing
  ansible.builtin.debug:
    msg: >-
      patroni-rest firewalld service not present at
      /etc/firewalld/services/patroni-rest.xml. HAProxy will still work
      (it talks to Patroni REST locally over loopback), but the cross-host
      health checks won't traverse firewalld until P2a's patroni role runs.
  when: not hap_patroni_svc_stat.stat.exists
```

- [ ] **Step 3: `_install.yml`**

```yaml
---
- name: Install HAProxy
  ansible.builtin.dnf:
    name: "{{ haproxy_package }}"
    state: present
```

- [ ] **Step 4: `_selinux.yml`**

```yaml
---
- name: Allow HAProxy to connect to arbitrary TCP ports (Patroni REST 8008 on peers)
  ansible.posix.seboolean:
    name: haproxy_connect_any
    state: true
    persistent: true
  when: ansible_facts.selinux.status == "enabled"
```

- [ ] **Step 5: `_configure.yml`**

```yaml
---
- name: Render haproxy.cfg
  ansible.builtin.template:
    src: haproxy.cfg.j2
    dest: "{{ haproxy_config_file }}"
    owner: root
    group: root
    mode: "0640"
    validate: "haproxy -c -f %s"
  notify: Reload haproxy
```

- [ ] **Step 6: `_firewall.yml`**

```yaml
---
- name: Install haproxy-postgres firewalld service definition
  ansible.builtin.copy:
    src: "{{ playbook_dir | dirname }}/files/firewalld/services/haproxy-postgres.xml"
    dest: /etc/firewalld/services/haproxy-postgres.xml
    owner: root
    group: root
    mode: "0644"
  register: hap_fw_svc

- name: Reload firewalld if service definition changed
  ansible.builtin.command: firewall-cmd --reload
  when: hap_fw_svc.changed
  changed_when: true

- name: Open built-in postgresql service (5432)
  ansible.posix.firewalld:
    service: postgresql
    permanent: true
    state: enabled
    immediate: true
    zone: "{{ haproxy_firewalld_zone }}"

- name: Open custom haproxy-postgres service (5433, 5434)
  ansible.posix.firewalld:
    service: haproxy-postgres
    permanent: true
    state: enabled
    immediate: true
    zone: "{{ haproxy_firewalld_zone }}"
```

- [ ] **Step 7: `_service.yml`**

```yaml
---
- name: Enable and start haproxy
  ansible.builtin.systemd:
    name: "{{ haproxy_service_name }}"
    enabled: true
    state: started

- name: Wait for HAProxy frontends to listen
  ansible.builtin.wait_for:
    host: "{{ network_loopback_address | default('127.0.0.1') }}"
    port: "{{ item }}"
    timeout: 30
  loop:
    - "{{ haproxy_default_port }}"
    - "{{ haproxy_primary_port }}"
    - "{{ haproxy_replica_port }}"
    - "{{ haproxy_stats_port }}"
```

- [ ] **Step 8: Commit**

```bash
git add roles/haproxy/tasks
git commit -m "feat(haproxy): install, configure, firewall, service tasks"
```

---

## Task 10: HAProxy template and handler

**Files:**
- Create: `roles/haproxy/templates/haproxy.cfg.j2`
- Create: `roles/haproxy/handlers/main.yml`

- [ ] **Step 1: `haproxy.cfg.j2`**

```jinja
# {{ ansible_managed }}
# Rendered by roles/haproxy. Do not edit by hand.

global
    log         /dev/log local2
    chroot      /var/lib/haproxy
    pidfile     /var/run/haproxy.pid
    maxconn     {{ haproxy_maxconn }}
    user        haproxy
    group       haproxy
    daemon

defaults
    mode                    tcp
    log                     global
    option                  tcplog
    option                  dontlognull
    timeout connect         5s
    timeout client          1h
    timeout server          1h
    timeout check           3s

# ---- stats listener (loopback only, behind nginx_proxy in P5) ----
listen stats
    mode http
    bind {{ haproxy_stats_listen_address }}:{{ haproxy_stats_port }}
    stats enable
    stats uri /
    stats refresh {{ haproxy_stats_refresh_seconds }}s
    stats auth {{ haproxy_stats_user }}:{{ haproxy_stats_password }}

# ---- default (5432) → leader ----
listen pg-default
    bind {{ haproxy_listen_address }}:{{ haproxy_default_port }}
    option httpchk OPTIONS /leader
    http-check expect status 200
    default-server inter {{ haproxy_check_interval_ms }}ms fall {{ haproxy_check_fall }} rise {{ haproxy_check_rise }} on-marked-down shutdown-sessions
{% for h in groups['postgres'] %}
    server {{ h }} {{ hostvars[h].ansible_host }}:{{ haproxy_backend_port }} check port {{ haproxy_patroni_rest_port }} check-ssl verify none
{% endfor %}

# ---- primary (5433) → leader (explicit RW) ----
listen pg-primary
    bind {{ haproxy_listen_address }}:{{ haproxy_primary_port }}
    option httpchk OPTIONS /leader
    http-check expect status 200
    default-server inter {{ haproxy_check_interval_ms }}ms fall {{ haproxy_check_fall }} rise {{ haproxy_check_rise }} on-marked-down shutdown-sessions
{% for h in groups['postgres'] %}
    server {{ h }} {{ hostvars[h].ansible_host }}:{{ haproxy_backend_port }} check port {{ haproxy_patroni_rest_port }} check-ssl verify none
{% endfor %}

# ---- replica (5434) → any healthy replica (RO) ----
listen pg-replica
    bind {{ haproxy_listen_address }}:{{ haproxy_replica_port }}
    balance roundrobin
    option httpchk OPTIONS /replica
    http-check expect status 200
    default-server inter {{ haproxy_check_interval_ms }}ms fall {{ haproxy_check_fall }} rise {{ haproxy_check_rise }} on-marked-down shutdown-sessions
{% for h in groups['postgres'] %}
    server {{ h }} {{ hostvars[h].ansible_host }}:{{ haproxy_backend_port }} check port {{ haproxy_patroni_rest_port }} check-ssl verify none
{% endfor %}
```

Notes:
- `check port {{ haproxy_patroni_rest_port }}` means the health check runs against Patroni REST on 8008, not against the data port.
- `check-ssl verify none` because the Patroni REST cert is signed by the pigsty-lite internal CA and HAProxy doesn't have a clean way to trust a custom CA without a `crt-list` config. The check is over TLS but doesn't validate the chain; that's acceptable for a localhost health probe that decides routing, not for client traffic. Operator-supplied CA bundles can be added in a v2 enhancement.
- `on-marked-down shutdown-sessions` aggressively closes connections when a server transitions to DOWN. This makes failover faster from the client's perspective.

- [ ] **Step 2: `handlers/main.yml`**

```yaml
---
- name: Reload haproxy
  ansible.builtin.systemd:
    name: "{{ haproxy_service_name }}"
    state: reloaded

- name: Restart haproxy
  ansible.builtin.systemd:
    name: "{{ haproxy_service_name }}"
    state: restarted
```

- [ ] **Step 3: Commit**

```bash
git add roles/haproxy/templates roles/haproxy/handlers
git commit -m "feat(haproxy): config template and handlers"
```

---

## Task 11: HAProxy firewalld XML

**Files:**
- Create: `files/firewalld/services/haproxy-postgres.xml`

- [ ] **Step 1: Write XML**

```xml
<?xml version="1.0" encoding="utf-8"?>
<service>
  <short>haproxy-postgres</short>
  <description>HAProxy frontends in front of PostgreSQL: 5433 (RW), 5434 (RO).</description>
  <port protocol="tcp" port="5433"/>
  <port protocol="tcp" port="5434"/>
</service>
```

- [ ] **Step 2: Lint**

Run: `xmllint --noout files/firewalld/services/haproxy-postgres.xml`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add files/firewalld/services/haproxy-postgres.xml
git commit -m "feat(firewalld): haproxy-postgres custom service for 5433+5434"
```

---

## Task 12: HAProxy default molecule scenario

**Files:**
- Create: `tests/molecule/haproxy/molecule/default/molecule.yml`
- Create: `tests/molecule/haproxy/molecule/default/prepare.yml`
- Create: `tests/molecule/haproxy/molecule/default/converge.yml`
- Create: `tests/molecule/haproxy/molecule/default/verify.yml`

This scenario uses the same single-node + patroni + pgbouncer base as Task 6, and adds HAProxy on top.

- [ ] **Step 1: `molecule.yml`**

```yaml
---
driver:
  name: podman
platforms:
  - name: pigsty-lite-haproxy
    image: docker.io/rockylinux/rockylinux:10-ubi-init
    pre_build_image: true
    privileged: true
    command: /usr/sbin/init
    volume_mounts:
      - "/sys/fs/cgroup:/sys/fs/cgroup:rw"
    capabilities: [SYS_ADMIN]
    systemd: always
    groups: [etcd, postgres]
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
        postgres_port: 5432
        certs_subject_alternative_names:
          - "DNS:{{ inventory_hostname }}"
          - "DNS:{{ inventory_hostname }}.test.local"
          - "IP:127.0.0.1"
      etcd:
        etcd_initial_cluster_state: new
      postgres:
        patroni_superuser_password: superuser-test-pw
        patroni_replication_password: replicator-test-pw
        patroni_rewind_password: rewind-test-pw
        postgres_tune_profile: tiny
    host_vars:
      pigsty-lite-haproxy:
        ansible_host: 127.0.0.1
        postgres_role: primary
        etcd_seq: 1
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
- name: Prepare - generate CA on control side
  hosts: localhost
  gather_facts: false
  vars:
    ca_dir: "{{ playbook_dir }}/_tmp_ca"
  roles:
    - role: ../../../../../roles/ca

- name: Prepare - full P0+P1+P2a + pgbouncer stack
  hosts: all
  gather_facts: true
  become: true
  vars:
    certs_ca_dir_on_control: "{{ playbook_dir }}/_tmp_ca"
    repos_pgdg_enabled: true
    repos_pgdg_rpm_url: "https://download.postgresql.org/pub/repos/yum/reporpms/EL-10-x86_64/pgdg-redhat-repo-latest.noarch.rpm"
    network_any_address: 0.0.0.0
    network_loopback_address: 127.0.0.1
    firewalld_default_zone: public
  roles:
    - role: ../../../../../roles/repos
    - role: ../../../../../roles/certs
    - role: ../../../../../roles/etcd
    - role: ../../../../../roles/postgres
    - role: ../../../../../roles/patroni
    - role: ../../../../../roles/pgbouncer
```

- [ ] **Step 3: `converge.yml`**

```yaml
---
- name: Converge - apply haproxy role
  hosts: postgres
  become: true
  gather_facts: true
  vars:
    network_any_address: 0.0.0.0
    network_loopback_address: 127.0.0.1
    firewalld_default_zone: public
  roles:
    - role: ../../../../../roles/haproxy
```

- [ ] **Step 4: `verify.yml`**

```yaml
---
- name: Verify
  hosts: postgres
  become: true
  tasks:
    - name: haproxy service active
      ansible.builtin.systemd:
        name: haproxy
      register: hap_unit

    - name: Assert haproxy active
      ansible.builtin.assert:
        that:
          - hap_unit.status.ActiveState == "active"

    - name: haproxy config validates
      ansible.builtin.command: haproxy -c -f /etc/haproxy/haproxy.cfg
      changed_when: false

    - name: 5432 routes to primary (single-node cluster: same host)
      ansible.builtin.command: >
        /usr/pgsql-18/bin/psql
        "host=127.0.0.1 port=5432 dbname=postgres user=postgres password=superuser-test-pw sslmode=disable"
        -tAc "select pg_is_in_recovery()"
      register: probe_5432
      changed_when: false
      retries: 6
      delay: 5
      until: probe_5432.rc == 0

    - name: Assert 5432 hit primary (pg_is_in_recovery=false)
      ansible.builtin.assert:
        that:
          - probe_5432.stdout.strip() in ['f', 'false']

    - name: 5433 routes to primary
      ansible.builtin.command: >
        /usr/pgsql-18/bin/psql
        "host=127.0.0.1 port=5433 dbname=postgres user=postgres password=superuser-test-pw sslmode=disable"
        -tAc "select pg_is_in_recovery()"
      register: probe_5433
      changed_when: false
      retries: 6
      delay: 5
      until: probe_5433.rc == 0

    - name: Assert 5433 hit primary
      ansible.builtin.assert:
        that:
          - probe_5433.stdout.strip() in ['f', 'false']

    - name: 5434 has no healthy backends on a single-node cluster
      ansible.builtin.command: >
        /usr/pgsql-18/bin/psql
        "host=127.0.0.1 port=5434 dbname=postgres user=postgres password=superuser-test-pw sslmode=disable"
        -tAc "select 1"
      register: probe_5434
      changed_when: false
      failed_when: false

    - name: Assert 5434 refuses (no replicas exist in single profile)
      ansible.builtin.assert:
        that:
          - probe_5434.rc != 0

    - name: Stats listener bound to loopback only
      ansible.builtin.command: ss -tln
      register: ss_out
      changed_when: false

    - name: Assert stats on 127.0.0.1:7000, not on 0.0.0.0:7000
      ansible.builtin.assert:
        that:
          - "'127.0.0.1:7000' in ss_out.stdout"
          - "'0.0.0.0:7000' not in ss_out.stdout"
```

- [ ] **Step 5: Run scenario**

Run: `cd tests/molecule/haproxy && molecule test -s default`
Expected: PASS including idempotence.

- [ ] **Step 6: Commit**

```bash
git add tests/molecule/haproxy/molecule/default
git commit -m "test(haproxy): single-node molecule scenario (5432/5433 route to leader)"
```

---

## Task 13: HAProxy HA molecule scenario

**Files:**
- Create: `tests/molecule/haproxy/molecule/ha/molecule.yml`
- Create: `tests/molecule/haproxy/molecule/ha/prepare.yml`
- Create: `tests/molecule/haproxy/molecule/ha/converge.yml`
- Create: `tests/molecule/haproxy/molecule/ha/verify.yml`

This scenario builds a 3-node Patroni cluster (mirroring the P2a `ha` scenario), adds pgbouncer + haproxy, asserts that 5433 lands on the leader and 5434 lands on a replica, then triggers a `patronictl switchover` and asserts 5433 now lands on the new leader.

- [ ] **Step 1: `molecule.yml`**

```yaml
---
driver:
  name: podman
platforms:
  - name: pigsty-lite-haproxy-ha-1
    image: docker.io/rockylinux/rockylinux:10-ubi-init
    pre_build_image: true
    privileged: true
    command: /usr/sbin/init
    volume_mounts:
      - "/sys/fs/cgroup:/sys/fs/cgroup:rw"
    capabilities: [SYS_ADMIN]
    systemd: always
    groups: [etcd, postgres]
    networks:
      - name: pigsty-lite-haproxy
  - name: pigsty-lite-haproxy-ha-2
    image: docker.io/rockylinux/rockylinux:10-ubi-init
    pre_build_image: true
    privileged: true
    command: /usr/sbin/init
    volume_mounts:
      - "/sys/fs/cgroup:/sys/fs/cgroup:rw"
    capabilities: [SYS_ADMIN]
    systemd: always
    groups: [etcd, postgres]
    networks:
      - name: pigsty-lite-haproxy
  - name: pigsty-lite-haproxy-ha-3
    image: docker.io/rockylinux/rockylinux:10-ubi-init
    pre_build_image: true
    privileged: true
    command: /usr/sbin/init
    volume_mounts:
      - "/sys/fs/cgroup:/sys/fs/cgroup:rw"
    capabilities: [SYS_ADMIN]
    systemd: always
    groups: [etcd, postgres]
    networks:
      - name: pigsty-lite-haproxy
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
        postgres_port: 5432
        certs_subject_alternative_names:
          - "DNS:{{ inventory_hostname }}"
          - "DNS:{{ inventory_hostname }}.test.local"
      etcd:
        etcd_initial_cluster_state: new
      postgres:
        patroni_superuser_password: superuser-test-pw
        patroni_replication_password: replicator-test-pw
        patroni_rewind_password: rewind-test-pw
        postgres_tune_profile: tiny
    host_vars:
      pigsty-lite-haproxy-ha-1:
        postgres_role: primary
        etcd_seq: 1
      pigsty-lite-haproxy-ha-2:
        postgres_role: replica
        etcd_seq: 2
      pigsty-lite-haproxy-ha-3:
        postgres_role: replica
        etcd_seq: 3
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
- name: Pin ansible_host to each container's network IP
  hosts: all
  gather_facts: true
  tasks:
    - name: Set ansible_host = default IPv4
      ansible.builtin.set_fact:
        ansible_host: "{{ ansible_default_ipv4.address }}"

- name: Prepare - generate CA on control side
  hosts: localhost
  gather_facts: false
  vars:
    ca_dir: "{{ playbook_dir }}/_tmp_ca"
  roles:
    - role: ../../../../../roles/ca

- name: Prepare - full P0+P1+P2a + pgbouncer
  hosts: all
  become: true
  gather_facts: true
  vars:
    certs_ca_dir_on_control: "{{ playbook_dir }}/_tmp_ca"
    repos_pgdg_enabled: true
    repos_pgdg_rpm_url: "https://download.postgresql.org/pub/repos/yum/reporpms/EL-10-x86_64/pgdg-redhat-repo-latest.noarch.rpm"
    network_any_address: 0.0.0.0
    network_loopback_address: 127.0.0.1
    firewalld_default_zone: public
    certs_subject_alternative_names:
      - "DNS:{{ inventory_hostname }}"
      - "DNS:{{ inventory_hostname }}.test.local"
      - "IP:{{ ansible_default_ipv4.address }}"
  roles:
    - role: ../../../../../roles/repos
    - role: ../../../../../roles/certs
    - role: ../../../../../roles/etcd
    - role: ../../../../../roles/postgres
    - role: ../../../../../roles/patroni
    - role: ../../../../../roles/pgbouncer
```

- [ ] **Step 3: `converge.yml`**

```yaml
---
- name: Pin ansible_host to each container's network IP
  hosts: all
  gather_facts: true
  tasks:
    - name: Set ansible_host = default IPv4
      ansible.builtin.set_fact:
        ansible_host: "{{ ansible_default_ipv4.address }}"

- name: Converge - apply haproxy role on every host
  hosts: postgres
  become: true
  gather_facts: true
  vars:
    network_any_address: 0.0.0.0
    network_loopback_address: 127.0.0.1
    firewalld_default_zone: public
  roles:
    - role: ../../../../../roles/haproxy
```

- [ ] **Step 4: `verify.yml`**

```yaml
---
- name: Pin ansible_host
  hosts: all
  gather_facts: true
  tasks:
    - name: Set ansible_host
      ansible.builtin.set_fact:
        ansible_host: "{{ ansible_default_ipv4.address }}"

- name: Verify haproxy on every host
  hosts: postgres
  become: true
  tasks:
    - name: haproxy active
      ansible.builtin.systemd:
        name: haproxy
      register: hap_unit

    - name: Assert active
      ansible.builtin.assert:
        that:
          - hap_unit.status.ActiveState == "active"

- name: Identify the current Patroni leader
  hosts: postgres
  become: true
  run_once: true
  tasks:
    - name: GET /cluster
      ansible.builtin.uri:
        url: "https://{{ ansible_host }}:8008/cluster"
        validate_certs: false
        return_content: true
      register: pre_cluster

    - name: Extract leader hostname
      ansible.builtin.set_fact:
        leader_host: >-
          {{ (pre_cluster.json.members
              | selectattr('role', 'equalto', 'leader')
              | list | first).name }}

    - name: Print leader
      ansible.builtin.debug:
        msg: "Current leader: {{ leader_host }}"

- name: Probe HAProxy routing (5433 = leader, 5434 = replica)
  hosts: postgres
  become: true
  tasks:
    - name: psql 5433 → must hit a primary
      ansible.builtin.command: >
        /usr/pgsql-18/bin/psql
        "host=127.0.0.1 port=5433 dbname=postgres user=postgres password=superuser-test-pw sslmode=disable"
        -tAc "select inet_server_addr() || ' is_replica=' || pg_is_in_recovery()"
      register: probe_5433
      changed_when: false
      retries: 10
      delay: 3
      until: probe_5433.rc == 0

    - name: Assert 5433 hit a primary
      ansible.builtin.assert:
        that:
          - "'is_replica=f' in probe_5433.stdout or 'is_replica=false' in probe_5433.stdout"
        fail_msg: "5433 routed to a replica. Got: {{ probe_5433.stdout }}"

    - name: psql 5434 → must hit a replica
      ansible.builtin.command: >
        /usr/pgsql-18/bin/psql
        "host=127.0.0.1 port=5434 dbname=postgres user=postgres password=superuser-test-pw sslmode=disable"
        -tAc "select inet_server_addr() || ' is_replica=' || pg_is_in_recovery()"
      register: probe_5434
      changed_when: false
      retries: 10
      delay: 3
      until: probe_5434.rc == 0

    - name: Assert 5434 hit a replica
      ansible.builtin.assert:
        that:
          - "'is_replica=t' in probe_5434.stdout or 'is_replica=true' in probe_5434.stdout"
        fail_msg: "5434 routed to a primary. Got: {{ probe_5434.stdout }}"

- name: Switchover and re-probe
  hosts: postgres
  become: true
  run_once: true
  tasks:
    - name: Pick a switchover candidate (any non-leader replica)
      ansible.builtin.set_fact:
        candidate_host: >-
          {{ (pre_cluster.json.members
              | selectattr('role', 'equalto', 'replica')
              | list | first).name }}

    - name: patronictl switchover
      ansible.builtin.command: >
        patronictl -c /etc/patroni/patroni.yml switchover
        --leader {{ leader_host }} --candidate {{ candidate_host }}
        --force
      become_user: postgres
      changed_when: true
      register: switchover

    - name: Wait for new leader to be {{ candidate_host }}
      ansible.builtin.uri:
        url: "https://{{ ansible_host }}:8008/cluster"
        validate_certs: false
        return_content: true
      register: post_cluster
      retries: 30
      delay: 2
      until: >-
        (post_cluster.json.members
         | selectattr('role', 'equalto', 'leader')
         | map(attribute='name') | list | first) == candidate_host

- name: 5433 follows the new leader
  hosts: postgres
  become: true
  tasks:
    - name: psql 5433 after switchover
      ansible.builtin.command: >
        /usr/pgsql-18/bin/psql
        "host=127.0.0.1 port=5433 dbname=postgres user=postgres password=superuser-test-pw sslmode=disable"
        -tAc "select pg_is_in_recovery()"
      register: post_probe_5433
      changed_when: false
      retries: 30
      delay: 2
      until: post_probe_5433.rc == 0 and post_probe_5433.stdout.strip() in ['f', 'false']

    - name: Assert 5433 still hits a primary after switchover
      ansible.builtin.assert:
        that:
          - post_probe_5433.stdout.strip() in ['f', 'false']
```

- [ ] **Step 5: Run scenario**

Run: `cd tests/molecule/haproxy && molecule test -s ha`
Expected: PASS. Switchover causes a momentary unavailability window on
5433 — the `retries: 30 delay: 2` loop is sized for that.

- [ ] **Step 6: Commit**

```bash
git add tests/molecule/haproxy/molecule/ha
git commit -m "test(haproxy): 3-node molecule scenario covering switchover routing"
```

---

## Task 14: vip-manager role defaults

**Files:**
- Create: `roles/vip_manager/defaults/main.yml`

- [ ] **Step 1: Write defaults**

```yaml
---
# roles/vip_manager/defaults/main.yml
# Variables prefixed `vip_manager_`. The vip-manager package ships in
# PGDG-extras (already enabled by the P0 repos role).

# Master gate - role is a no-op when false (the default).
vip_manager_enabled: false

# Package
vip_manager_package: vip-manager
vip_manager_service_name: vip-manager

# Config
vip_manager_config_file: /etc/vip-manager.yml

# DCS
vip_manager_dcs_type: etcd3
vip_manager_dcs_protocol: https
vip_manager_dcs_endpoints: >-
  [{% for h in groups['etcd'] -%}
    "{{ vip_manager_dcs_protocol }}://{{ hostvars[h].ansible_host }}:{{ etcd_client_port | default(2379) }}"
    {%- if not loop.last %},{% endif %}
  {%- endfor %}]
vip_manager_dcs_cacert: "{{ pki_dir | default('/etc/pki/pigsty-lite') }}/ca.crt"
vip_manager_dcs_cert: "{{ pki_dir | default('/etc/pki/pigsty-lite') }}/{{ inventory_hostname }}.crt"
vip_manager_dcs_key:  "{{ pki_dir | default('/etc/pki/pigsty-lite') }}/{{ inventory_hostname }}.key"

# Cluster identity (matches patroni_scope from P2a)
vip_manager_scope: "{{ cluster_name | default('pigsty-lite') }}"
vip_manager_trigger_key: "/service/{{ vip_manager_scope }}/leader"
vip_manager_trigger_value: "{{ inventory_hostname }}"

# Networking
# operator MUST set vip_manager_vip_cidr and vip_manager_interface if enabling.
# We do not pick defaults that would silently bind a random address.
vip_manager_vip_cidr: ""
vip_manager_interface: ""
vip_manager_hosting_type: basic  # basic | hetzner

# Loop tuning
vip_manager_loop_wait_seconds: 1
vip_manager_retry_after_seconds: 2

# Firewalld - vip-manager itself does not listen on a port; nothing to open.
```

- [ ] **Step 2: Lint**

Run: `yamllint roles/vip_manager/defaults/main.yml`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add roles/vip_manager/defaults/main.yml
git commit -m "feat(vip-manager): role defaults (disabled by default)"
```

---

## Task 15: vip-manager meta and README

**Files:**
- Create: `roles/vip_manager/meta/main.yml`
- Create: `roles/vip_manager/README.md`

- [ ] **Step 1: `meta/main.yml`**

```yaml
---
galaxy_info:
  role_name: vip_manager
  author: pigsty-lite
  description: Optional L2 VIP bound to the current Patroni leader.
  license: Apache-2.0
  min_ansible_version: "2.16"
  platforms:
    - name: EL
      versions: ["10"]
dependencies: []
```

- [ ] **Step 2: `README.md`**

````markdown
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
````

- [ ] **Step 3: Commit**

```bash
git add roles/vip_manager/meta roles/vip_manager/README.md
git commit -m "feat(vip-manager): role meta and README"
```

---

## Task 16: vip-manager tasks

**Files:**
- Create: `roles/vip_manager/tasks/main.yml`
- Create: `roles/vip_manager/tasks/_assert.yml`
- Create: `roles/vip_manager/tasks/_install.yml`
- Create: `roles/vip_manager/tasks/_configure.yml`
- Create: `roles/vip_manager/tasks/_service.yml`

- [ ] **Step 1: `main.yml`**

```yaml
---
- name: Short-circuit when vip-manager is disabled
  ansible.builtin.meta: end_play
  when: not (vip_manager_enabled | bool)

- name: Validate preconditions
  ansible.builtin.import_tasks: _assert.yml
  tags: [vip_manager, assert]

- name: Install vip-manager
  ansible.builtin.import_tasks: _install.yml
  tags: [vip_manager, install]

- name: Render vip-manager config
  ansible.builtin.import_tasks: _configure.yml
  tags: [vip_manager, config]

- name: Start vip-manager
  ansible.builtin.import_tasks: _service.yml
  tags: [vip_manager, service]
```

- [ ] **Step 2: `_assert.yml`**

```yaml
---
- name: Fail unless VIP CIDR and interface are set
  ansible.builtin.assert:
    that:
      - vip_manager_vip_cidr | length > 0
      - vip_manager_interface | length > 0
    fail_msg: >-
      vip_manager_enabled is true but vip_manager_vip_cidr or
      vip_manager_interface is empty. Set both in the response file.

- name: Verify interface exists on this host
  ansible.builtin.assert:
    that:
      - vip_manager_interface in (ansible_facts.interfaces | default([]))
    fail_msg: >-
      Interface {{ vip_manager_interface }} not found on
      {{ inventory_hostname }}. Existing interfaces:
      {{ ansible_facts.interfaces | default([]) | join(', ') }}
```

- [ ] **Step 3: `_install.yml`**

```yaml
---
- name: Install vip-manager from PGDG-extras
  ansible.builtin.dnf:
    name: "{{ vip_manager_package }}"
    state: present
```

- [ ] **Step 4: `_configure.yml`**

```yaml
---
- name: Render vip-manager.yml
  ansible.builtin.template:
    src: vip-manager.yml.j2
    dest: "{{ vip_manager_config_file }}"
    owner: root
    group: root
    mode: "0640"
  notify: Restart vip-manager
```

- [ ] **Step 5: `_service.yml`**

```yaml
---
- name: Enable and start vip-manager
  ansible.builtin.systemd:
    name: "{{ vip_manager_service_name }}"
    enabled: true
    state: started
    daemon_reload: true
```

- [ ] **Step 6: Commit**

```bash
git add roles/vip_manager/tasks
git commit -m "feat(vip-manager): tasks (no-op when disabled)"
```

---

## Task 17: vip-manager template and handler

**Files:**
- Create: `roles/vip_manager/templates/vip-manager.yml.j2`
- Create: `roles/vip_manager/handlers/main.yml`

- [ ] **Step 1: `vip-manager.yml.j2`**

```jinja
# {{ ansible_managed }}
# Rendered by roles/vip_manager. Do not edit by hand.

interval: {{ (vip_manager_loop_wait_seconds | int) * 1000 }}
retry-after: {{ (vip_manager_retry_after_seconds | int) * 1000 }}
retry-num: 2

hosting-type: {{ vip_manager_hosting_type }}
ip: {{ vip_manager_vip_cidr.split('/')[0] }}
netmask: {{ vip_manager_vip_cidr.split('/')[1] }}
interface: {{ vip_manager_interface }}

trigger-key: "{{ vip_manager_trigger_key }}"
trigger-value: "{{ vip_manager_trigger_value }}"

dcs-type: {{ vip_manager_dcs_type }}
dcs-endpoints: {{ vip_manager_dcs_endpoints }}
etcd:
  cacert: {{ vip_manager_dcs_cacert }}
  cert: {{ vip_manager_dcs_cert }}
  key:  {{ vip_manager_dcs_key }}
```

- [ ] **Step 2: `handlers/main.yml`**

```yaml
---
- name: Restart vip-manager
  ansible.builtin.systemd:
    name: "{{ vip_manager_service_name }}"
    state: restarted
    daemon_reload: true
```

- [ ] **Step 3: Commit**

```bash
git add roles/vip_manager/templates roles/vip_manager/handlers
git commit -m "feat(vip-manager): template and restart handler"
```

---

## Task 18: vip-manager molecule scenario (disabled path)

**Files:**
- Create: `tests/molecule/vip_manager/molecule/default/molecule.yml`
- Create: `tests/molecule/vip_manager/molecule/default/prepare.yml`
- Create: `tests/molecule/vip_manager/molecule/default/converge.yml`
- Create: `tests/molecule/vip_manager/molecule/default/verify.yml`

- [ ] **Step 1: `molecule.yml`**

```yaml
---
driver:
  name: podman
platforms:
  - name: pigsty-lite-vipmgr
    image: docker.io/rockylinux/rockylinux:10-ubi-init
    pre_build_image: true
    privileged: true
    command: /usr/sbin/init
    volume_mounts:
      - "/sys/fs/cgroup:/sys/fs/cgroup:rw"
    capabilities: [SYS_ADMIN]
    systemd: always
    groups: [etcd, postgres]
provisioner:
  name: ansible
  config_options:
    defaults:
      collections_path: "../../../collections"
  inventory:
    group_vars:
      all:
        cluster_name: pigsty-lite-test
        pki_dir: /etc/pki/pigsty-lite
        postgres_version: 18
      etcd:
        etcd_initial_cluster_state: new
    host_vars:
      pigsty-lite-vipmgr:
        ansible_host: 127.0.0.1
        postgres_role: primary
        etcd_seq: 1
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
- name: Prepare - install PGDG so vip-manager is even discoverable
  hosts: all
  become: true
  gather_facts: true
  vars:
    repos_pgdg_enabled: true
    repos_pgdg_rpm_url: "https://download.postgresql.org/pub/repos/yum/reporpms/EL-10-x86_64/pgdg-redhat-repo-latest.noarch.rpm"
  roles:
    - role: ../../../../../roles/repos
```

- [ ] **Step 3: `converge.yml`**

```yaml
---
- name: Converge - apply vip_manager role with enabled=false
  hosts: postgres
  become: true
  gather_facts: true
  vars:
    vip_manager_enabled: false
  roles:
    - role: ../../../../../roles/vip_manager
```

- [ ] **Step 4: `verify.yml`**

```yaml
---
- name: Verify the role was a no-op
  hosts: postgres
  become: true
  tasks:
    - name: vip-manager package NOT installed
      ansible.builtin.command: rpm -q vip-manager
      register: vipmgr_pkg
      changed_when: false
      failed_when: false

    - name: Assert vip-manager rpm absent
      ansible.builtin.assert:
        that:
          - vipmgr_pkg.rc != 0
        fail_msg: >-
          vip-manager rpm is installed despite vip_manager_enabled=false.
          The disabled short-circuit in tasks/main.yml is broken.

    - name: vip-manager.yml NOT rendered
      ansible.builtin.stat:
        path: /etc/vip-manager.yml
      register: vipmgr_cfg

    - name: Assert config absent
      ansible.builtin.assert:
        that:
          - not vipmgr_cfg.stat.exists

    - name: vip-manager.service NOT running
      ansible.builtin.command: systemctl is-active vip-manager
      register: vipmgr_active
      changed_when: false
      failed_when: false

    - name: Assert service inactive
      ansible.builtin.assert:
        that:
          - vipmgr_active.rc != 0
```

- [ ] **Step 5: Run scenario**

Run: `cd tests/molecule/vip_manager && molecule test -s default`
Expected: PASS. The role does nothing; verify confirms it.

- [ ] **Step 6: Commit**

```bash
git add tests/molecule/vip_manager
git commit -m "test(vip-manager): default scenario verifies no-op when disabled"
```

---

## Task 19: Extend response schema + generator + examples

**Files:**
- Modify: `bin/_response_schema.py`
- Modify: `bin/_generate_response_vars.py`
- Modify: `responses/single.rsp.yml.example`
- Modify: `responses/ha.rsp.yml.example`

- [ ] **Step 1: Add `connection_layer:` and `vip_manager:` to schema**

In `bin/_response_schema.py`, add a new validator after `_validate_monitoring`:

```python
ALLOWED_HAPROXY_RTO = {"tight", "norm", "loose"}
ALLOWED_HAPROXY_BACKEND = {"pgbouncer", "postgres"}


def _validate_connection_layer(value: Any, ip_version: str) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        raise SchemaError("connection_layer: must be a mapping")
    haproxy = value.get("haproxy", {})
    if not isinstance(haproxy, dict):
        raise SchemaError("connection_layer.haproxy: must be a mapping")
    rto = haproxy.get("rto_profile", "norm")
    if rto not in ALLOWED_HAPROXY_RTO:
        raise SchemaError(
            f"connection_layer.haproxy.rto_profile: '{rto}' not in {sorted(ALLOWED_HAPROXY_RTO)}"
        )
    backend = haproxy.get("backend_target", "pgbouncer")
    if backend not in ALLOWED_HAPROXY_BACKEND:
        raise SchemaError(
            f"connection_layer.haproxy.backend_target: '{backend}' not in "
            f"{sorted(ALLOWED_HAPROXY_BACKEND)}"
        )
    vip = value.get("vip_manager", {})
    if not isinstance(vip, dict):
        raise SchemaError("connection_layer.vip_manager: must be a mapping")
    enabled = vip.get("enabled", False)
    if not isinstance(enabled, bool):
        raise SchemaError("connection_layer.vip_manager.enabled: must be bool")
    if enabled:
        cidr = vip.get("vip_cidr")
        iface = vip.get("interface")
        if not isinstance(cidr, str) or not cidr:
            raise SchemaError(
                "connection_layer.vip_manager.enabled=true requires vip_cidr (string)"
            )
        _check_cidr(cidr, "connection_layer.vip_manager.vip_cidr", ip_version)
        if not isinstance(iface, str) or not iface:
            raise SchemaError(
                "connection_layer.vip_manager.enabled=true requires interface (string)"
            )
```

Then call it from the top-level `validate()`:

```python
    _validate_connection_layer(data.get("connection_layer"), ip_version)
```

Place that call after `_validate_monitoring(_require(data, "monitoring", ""))`.

- [ ] **Step 2: Emit new vars in `_generate_response_vars.py`**

In `bin/_generate_response_vars.py` `generate()`, add at the end of the `out` dict assembly:

```python
    conn = response.get("connection_layer", {}) or {}
    hap = conn.get("haproxy", {}) or {}
    vip = conn.get("vip_manager", {}) or {}

    out["haproxy_rto_profile"] = hap.get("rto_profile", "norm")
    out["haproxy_backend_target"] = hap.get("backend_target", "pgbouncer")
    out["vip_manager_enabled"] = bool(vip.get("enabled", False))
    if out["vip_manager_enabled"]:
        out["vip_manager_vip_cidr"] = vip["vip_cidr"]
        out["vip_manager_interface"] = vip["interface"]
```

- [ ] **Step 3: Add an optional block to each response example**

Append to `responses/single.rsp.yml.example` (and the same block to `ha.rsp.yml.example`):

```yaml
# Optional. Defaults: rto_profile=norm, backend_target=pgbouncer, vip-manager disabled.
# connection_layer:
#   haproxy:
#     rto_profile: norm        # tight | norm | loose
#     backend_target: pgbouncer  # pgbouncer | postgres
#   vip_manager:
#     enabled: false
#     vip_cidr: "10.20.30.20/24"
#     interface: "eth0"
```

- [ ] **Step 4: Run schema tests**

Run: `pytest tests/configure -v`
Expected: existing tests still pass. If they don't, the schema regression is real — fix it.

- [ ] **Step 5: Commit**

```bash
git add bin/_response_schema.py bin/_generate_response_vars.py responses/single.rsp.yml.example responses/ha.rsp.yml.example
git commit -m "feat(configure): connection_layer + vip_manager response schema"
```

---

## Task 20: Wire P2b into site.yml

**Files:**
- Create: `playbooks/_pgbouncer.yml`
- Create: `playbooks/_haproxy.yml`
- Create: `playbooks/_vip_manager.yml`
- Modify: `playbooks/site.yml`
- Modify: `playbooks/tags.md`
- Modify: `group_vars/postgres.yml`

- [ ] **Step 1: `playbooks/_pgbouncer.yml`**

```yaml
---
- name: P2b pgbouncer - install and configure per-host pooler
  hosts: postgres
  become: true
  gather_facts: true
  roles:
    - role: pgbouncer
      tags: [pgbouncer]
```

- [ ] **Step 2: `playbooks/_haproxy.yml`**

```yaml
---
- name: P2b haproxy - install and configure per-host frontends
  hosts: postgres
  become: true
  gather_facts: true
  roles:
    - role: haproxy
      tags: [haproxy]
```

- [ ] **Step 3: `playbooks/_vip_manager.yml`**

```yaml
---
- name: P2b vip-manager - optional L2 VIP for the cluster leader
  hosts: postgres
  become: true
  gather_facts: true
  roles:
    - role: vip_manager
      tags: [vip_manager]
```

- [ ] **Step 4: Update `playbooks/site.yml`**

Replace contents with:

```yaml
---
# pigsty-lite site.yml
- name: Import P0 preflight playbook
  import_playbook: _preflight.yml
  tags: [preflight]
- name: Import P0 CA playbook
  import_playbook: _ca.yml
  tags: [ca]
- name: Import P0 node playbook
  import_playbook: _node.yml
  tags: [repos, node, certs]
- name: Import P1 etcd playbook
  import_playbook: _etcd.yml
  tags: [etcd]
- name: Import P2a postgres install playbook
  import_playbook: _postgres_install.yml
  tags: [postgres]
- name: Import P2a patroni bootstrap playbook
  import_playbook: _postgres_bootstrap.yml
  tags: [patroni]
- name: Import P2b pgbouncer playbook
  import_playbook: _pgbouncer.yml
  tags: [pgbouncer]
- name: Import P2b haproxy playbook
  import_playbook: _haproxy.yml
  tags: [haproxy]
- name: Import P2b vip-manager playbook
  import_playbook: _vip_manager.yml
  tags: [vip_manager]
```

- [ ] **Step 5: Update `playbooks/tags.md`**

Add `pgbouncer`, `haproxy`, `vip_manager` to the module tag list, and add a usage example:

```markdown
- `--tags pgbouncer` - reconfigure pgBouncer (reload, not restart).
- `--tags haproxy` - reconfigure HAProxy (reload). Use `haproxy,restart` to bounce the service.
- `--tags vip_manager` - re-render vip-manager config and restart (only when enabled).
```

- [ ] **Step 6: Update `group_vars/postgres.yml`**

Append:

```yaml
# Connection layer (P2b)
haproxy_rto_profile: norm
haproxy_backend_target: pgbouncer
haproxy_stats_user: pigsty
haproxy_stats_password: "{{ vault_haproxy_stats_password | default('haproxy-dev-stats-change-me') }}"
pgbouncer_admin_users: ["postgres"]
pgbouncer_stats_users: ["postgres"]
vip_manager_enabled: false
```

- [ ] **Step 7: Commit**

```bash
git add playbooks/_pgbouncer.yml playbooks/_haproxy.yml playbooks/_vip_manager.yml \
        playbooks/site.yml playbooks/tags.md group_vars/postgres.yml
git commit -m "feat(playbooks): wire P2b connection layer into site"
```

---

## Task 21: CI matrix

**Files:**
- Modify: `.github/workflows/molecule.yml`

- [ ] **Step 1: Append to the matrix `include:` block**

```yaml
          - role: pgbouncer
            scenario: default
          - role: haproxy
            scenario: default
          - role: haproxy
            scenario: ha
          - role: vip_manager
            scenario: default
```

- [ ] **Step 2: Lint**

Run: `yamllint .github/workflows/molecule.yml`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/molecule.yml
git commit -m "ci(molecule): add pgbouncer, haproxy, and vip_manager scenarios"
```

---

## Task 22: Docs and roadmap

**Files:**
- Modify: `docs/operations/firstrun.md`
- Modify: `README.md`

- [ ] **Step 1: Add a P2b section in firstrun.md**

Insert after the P2a section:

````markdown
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
  service (5433+5434), and toggles the `haproxy_connect_any` SELinux
  boolean so HAProxy can reach Patroni REST on peer hosts.
- `_vip_manager.yml` (vip-manager role) is a no-op unless the operator
  sets `connection_layer.vip_manager.enabled: true` in the response
  file. When enabled, it installs `vip-manager` from PGDG-extras,
  renders `/etc/vip-manager.yml` pointing at the etcd cluster, and
  binds the configured VIP to the configured interface on whichever
  host is currently the Patroni leader.

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
````

- [ ] **Step 2: Update `README.md`**

Change the Status line to:

```
**Status:** P0 (Foundation), P1 (etcd), P2a (PostgreSQL + Patroni), and
P2b (connection layer) are complete. Subsequent sub-plans (P2c
integration tests, P3 provisioning, P4 backups, P5 monitoring, P6
lifecycle/portability) are pending. The architecture and scope are
defined in
[`docs/superpowers/specs/2026-05-12-pigsty-lite-design.md`](docs/superpowers/specs/2026-05-12-pigsty-lite-design.md).
```

In the Roadmap table change the P2b row to `done`:

```markdown
| P2b | Connection layer: pgBouncer + HAProxy + vip-manager | done |
```

- [ ] **Step 3: Commit**

```bash
git add docs/operations/firstrun.md README.md
git commit -m "docs: P2b connection layer firstrun and roadmap"
```

---

## Task 23: Local smoke test (optional)

This step is **optional** and **not run in CI**.

**Prerequisite:** A 4-VM libvirt environment (1 monitor + 3 PG hosts) reachable via SSH with sudo. A spare IP on the same L2 segment as the PG hosts if you want to exercise vip-manager.

- [ ] **Step 1: Deploy without vip-manager first**

```bash
./configure -c ha
$EDITOR responses/site.rsp.yml   # fill IPs
./configure -s -f responses/site.rsp.yml
make plan
make deploy
```

Expected: P0+P1+P2a+P2b all green. Final task is the haproxy `wait_for`
on 5432/5433/5434 on every host.

- [ ] **Step 2: Connectivity matrix**

```bash
# RW via the default port
psql "host=pgnode01 port=5432 dbname=postgres user=postgres" -c 'select pg_is_in_recovery()'
# Expected: f

# RW via the explicit RW port (any host)
psql "host=pgnode02 port=5433 dbname=postgres user=postgres" -c 'select pg_is_in_recovery()'
# Expected: f (HAProxy on pgnode02 forwards to the leader)

# RO via the explicit RO port
psql "host=pgnode02 port=5434 dbname=postgres user=postgres" -c 'select pg_is_in_recovery()'
# Expected: t
```

- [ ] **Step 3: Switchover**

```bash
ssh pgnode01 sudo -u postgres patronictl -c /etc/patroni/patroni.yml switchover \
  --leader pgnode01 --candidate pgnode02 --force
```

In a separate terminal, loop the RW probe:

```bash
while true; do
  date +%H:%M:%S
  psql "host=pgnode01 port=5433 dbname=postgres user=postgres" \
    -tAc 'select inet_server_addr()' || echo "DOWN"
  sleep 1
done
```

Expected: a brief sequence of "DOWN" lines while HAProxy detects the
new leader, then output stabilizes on the new primary's IP. Total
unavailability should be under ~10 seconds under the `norm` profile.

- [ ] **Step 4: SELinux still enforcing**

```bash
ansible postgres -i inventory/site.yml -a 'getenforce'
```

Expected: every host returns `Enforcing`.

- [ ] **Step 5: Firewalld**

```bash
ansible postgres -i inventory/site.yml -b -a 'firewall-cmd --list-services'
```

Expected: at minimum `ssh`, `etcd-server`, `etcd-client`, `patroni-rest`, `postgresql`, `haproxy-postgres`. Critically, `pgbouncer` should NOT be in the list (clients go via HAProxy).

- [ ] **Step 6: vip-manager smoke (only with a spare IP available)**

Edit the response file to enable vip-manager, re-deploy, and verify:

```bash
ssh pgnode01 ip addr show eth0 | grep -F "$(yq '.connection_layer.vip_manager.vip_cidr' responses/site.rsp.yml | tr -d '"')"
```

Expected: the VIP is bound on the current leader and ONLY the leader. After a switchover the address migrates within ~5s.

- [ ] **Step 7: Re-run for idempotence**

```bash
make deploy
```

Expected: zero changed tasks.

No commit. Verification only.

---

## Self-review notes

1. **Spec coverage check.** Spec §3.1 topology: HAProxy on every PG node with 5432→default, 5433→primary, 5434→replicas (Task 7 defaults, Task 10 template). Spec §3.4 deploy order: `pgbouncer, haproxy, vip_manager (optional)` between `provision` and `backup_*` — Task 20 wires them between `_postgres_bootstrap.yml` and the (future) provision playbook. Spec §4 roles table: `pgbouncer` (Tasks 1–6), `haproxy` (Tasks 7–13), `vip_manager` (Tasks 14–18). Spec §6.1 firewall: `postgresql` (5432, built-in) + `haproxy-postgres` (5433/5434, custom XML in Task 11), `pgbouncer` (6432, custom XML in Task 5, disabled by default per the spec "off by default — clients go via haproxy" note). HAProxy stats 7000 bound loopback per spec — Task 7 default + Task 10 template. Spec §7.2 variable naming — every var prefixed by role (`pgbouncer_*`, `haproxy_*`, `vip_manager_*`). Spec §8.2 RTO target: `haproxy_rto_profile` default `norm` with three preset profiles (Task 7). Spec §8.3 client connection path: 5432 = default, 5433 = RW primary, 5434 = RO replicas — matches Task 10 template exactly.

2. **Placeholder scan.** No "TBD", no "TODO", no "implement later". Every step has either a commit or a concrete command. The only deliberate `failed_when: false`s are in tests that probe for an expected failure ("5434 should not be reachable in single profile" / "pgbouncer rpm should NOT be installed when disabled"); those are asserted as failures, not silently swallowed.

3. **Variable / type consistency.** `postgres_port`, `pgbouncer_listen_port`, `patroni_rest_port`, `etcd_client_port` are all defined in `group_vars/all.yml` (P0) and reused without rename here. `patroni_superuser_password` is defined in `group_vars/postgres.yml` (P2a) and consumed in Task 1 (pgbouncer userlist) and Task 6 (verify probes). `haproxy_check_interval_ms` / `_fall` / `_rise` are derived in Task 7 defaults from `haproxy_rto_profile` and consumed in Task 10 template. `vip_manager_enabled` is the canonical gate, defined in Task 14 defaults and checked in Task 16 main, Task 19 schema, Task 20 group_vars, Task 22 README.

4. **Decisions to flag for review.**
   - **HAProxy → pgBouncer chain by default** (`haproxy_backend_target: pgbouncer`). Alternative is HAProxy → PG direct (skip pgBouncer). The spec describes both layers as present; chaining HAProxy through pgBouncer keeps the pooler in the path so connection counts to PG stay bounded. If the operator wants to bypass for ultra-low-latency apps, they set `haproxy_backend_target: postgres` in the response file.
   - **HAProxy health check uses `check-ssl verify none`**. Patroni REST has a cert signed by our internal CA; teaching HAProxy to trust it via `ca-file` is mechanically straightforward but adds a per-host file dependency and a config edit that the role currently doesn't ship. Spec §6.3 SELinux says "enforce TLS for client traffic"; the haproxy→patroni REST check is intra-host control plane traffic, not client traffic. Treating it as TLS-without-verify is acceptable; an operator-friendly improvement is to add a `haproxy_check_ca_file` variable in a follow-up.
   - **vip-manager molecule tests only the disabled path.** Enabling it requires `NET_ADMIN` and a network interface podman doesn't model reliably. Task 23 documents the libvirt smoke test for the enabled path. If integration tests under libvirt land in P2c, that path should be exercised there.

5. **What's out of scope for P2b (do not implement):**
   - Business databases, users, runtime HBA (P3).
   - HAProxy stats exposed externally (P5 nginx_proxy).
   - pgBouncer prometheus exporter (P5).
   - HAProxy `crt-list` / verifying-CA health checks (improvement, not v1).
   - Multi-VIP support in vip-manager (out of v1 per the spec).
   - Cross-cluster routing (spec excludes).
   - RTO measurement / chaos testing (P2c integration scenarios).

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-13-p2b-connection-layer.md`. Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
