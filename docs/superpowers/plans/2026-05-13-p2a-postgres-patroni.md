# P2a (PostgreSQL + Patroni) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a streaming-replicated PostgreSQL 18 cluster managed by Patroni on top of P1's etcd. Replication is over TLS using the P0 per-host certs; the leader is elected automatically; the cluster survives `make deploy` re-runs as a no-op. P2a stops at "Patroni reports healthy leader+replicas" — pgBouncer/HAProxy/VIP are P2b; provisioning of business databases/users/HBA is P3.

**Architecture:** Two new thin roles. `roles/postgres/` installs `postgresql{{ postgres_version }}-server` and `postgresql{{ postgres_version }}-contrib` from PGDG, lays down the data dir at the vendor default `/var/lib/pgsql/<ver>/data`, registers SELinux fcontext where needed, and stops short of `initdb` (Patroni owns bootstrap). `roles/patroni/` installs Patroni (PGDG package), renders `/etc/patroni/patroni.yml` with TLS for both the REST API (8008) and PG streaming replication, masks the vendor `postgresql-<ver>` unit (Patroni starts/stops PG itself), enables+starts `patroni.service`, opens firewalld for `patroni-rest`, and gates on `patronictl list` reporting one leader + N healthy members. P2a wires both roles into `site.yml` after `_etcd.yml`. Two Molecule scenarios: `single` (one PG host with the 1-node etcd from P1) and `ha` (three PG hosts colocated with the 3-node etcd quorum). The `ha` scenario asserts a leader plus two replicas in `streaming` state.

**Tech Stack:** Ansible role + Jinja2, PostgreSQL 18 via PGDG RPM, Patroni from PGDG, `community.postgresql` for waitfor/cluster sanity probes, `community.crypto` reuse from P0, `community.general.sefcontext` + `seport`, `ansible.posix.firewalld`, Molecule + podman for tests.

---

## File Structure

**New files (in `roles/postgres/`):**

- `roles/postgres/defaults/main.yml` — package names (computed from `postgres_version`), data dir, port, listen address, SELinux port type, dnf module disable marker, support package list.
- `roles/postgres/meta/main.yml` — minimal galaxy_info; no role deps (orchestration in `site.yml`).
- `roles/postgres/tasks/main.yml` — orchestrates: assert preconditions → install RPMs → fcontext + dirs → seport for non-default ports → mask vendor systemd unit (Patroni owns lifecycle).
- `roles/postgres/tasks/_assert.yml` — fails fast if `postgres_version` mismatches `pgdg_postgres_version`, if data-dir parent block device matches etcd's (warn-only), if per-host cert is missing.
- `roles/postgres/tasks/_install.yml` — dnf install of server + contrib + libs.
- `roles/postgres/tasks/_filesystem.yml` — create `/var/lib/pgsql`, `/var/lib/pgsql/<ver>`, `/var/lib/pgsql/<ver>/data` empty with PG ownership; `community.general.sefcontext` for non-vendor data dirs (no-op when default).
- `roles/postgres/tasks/_seport.yml` — `community.general.seport` for `postgres_port` and `patroni_rest_port` only when they differ from vendor labels.
- `roles/postgres/tasks/_mask_vendor.yml` — mask `postgresql-<ver>.service` so PGDG's vendor unit cannot race with Patroni.
- `roles/postgres/README.md` — what the role does, what it deliberately does NOT do (no `initdb`, no `postgresql.conf`).

**New files (in `roles/patroni/`):**

- `roles/patroni/defaults/main.yml` — package, listen addresses, REST port, bootstrap timeout, wal_level/replication slot defaults, sync mode, tuning-profile path lookup, DCS endpoints (derived from `groups['etcd']`).
- `roles/patroni/meta/main.yml` — galaxy_info.
- `roles/patroni/tasks/main.yml` — orchestrate: assert → install → render `patroni.yml` → render systemd drop-in (env file, restart policy) → firewalld → enable+start → wait for leader (primary) / wait for `running` member (replicas).
- `roles/patroni/tasks/_assert.yml` — etcd group present + healthy reachable from this host; per-host cert exists; postgres role installed.
- `roles/patroni/tasks/_install.yml` — `dnf install patroni patroni-etcd python3-psycopg3`.
- `roles/patroni/tasks/_configure.yml` — render `/etc/patroni/patroni.yml`, dirs, ownership; render systemd drop-in if needed.
- `roles/patroni/tasks/_firewall.yml` — open `patroni-rest` firewalld service.
- `roles/patroni/tasks/_service.yml` — `daemon_reload`, `enable + start patroni`, then wait-for-leader (run_once on `postgres_role==primary`) and wait-for-running on every member.
- `roles/patroni/templates/patroni.yml.j2` — single source of truth.
- `roles/patroni/templates/systemd-override.conf.j2` — restart policy + LimitNOFILE bump.
- `roles/patroni/files/tuning/oltp.conf` — curated PG params.
- `roles/patroni/files/tuning/olap.conf` — curated PG params.
- `roles/patroni/files/tuning/tiny.conf` — curated PG params.
- `roles/patroni/handlers/main.yml` — `Restart patroni` (used only when Patroni's own settings change; PG-level changes go through `patronictl edit-config` and are out of scope for P2a).
- `roles/patroni/README.md`.

**New firewalld service:**

- `files/firewalld/services/patroni-rest.xml` — port 8008/tcp.

**New playbook + wiring:**

- `playbooks/_postgres_install.yml` — runs the `postgres` role on the `postgres` group.
- `playbooks/_postgres_bootstrap.yml` — runs the `patroni` role on the `postgres` group; primary first then replicas via `serial: 1` is NOT used (Patroni handles ordering; we run all hosts and the leader-wait gate forces synchronization).
- `playbooks/site.yml` — modify to import both new playbooks after `_etcd.yml`.
- `playbooks/tags.md` — add `postgres` and `patroni` module tags.
- `group_vars/postgres.yml` — replace placeholder with real defaults consumed by both roles (postgres_data_dir, patroni_dcs_endpoints, postgres_replication_user).

**New test files:**

- `tests/molecule/postgres/molecule/default/molecule.yml` — single container, single etcd, single PG.
- `tests/molecule/postgres/molecule/default/prepare.yml`
- `tests/molecule/postgres/molecule/default/converge.yml`
- `tests/molecule/postgres/molecule/default/verify.yml`
- `tests/molecule/patroni/molecule/single/molecule.yml` — 1 PG host, 1 etcd member colocated.
- `tests/molecule/patroni/molecule/single/prepare.yml`
- `tests/molecule/patroni/molecule/single/converge.yml`
- `tests/molecule/patroni/molecule/single/verify.yml`
- `tests/molecule/patroni/molecule/ha/molecule.yml` — 3 PG hosts, 3 etcd members colocated.
- `tests/molecule/patroni/molecule/ha/prepare.yml`
- `tests/molecule/patroni/molecule/ha/converge.yml`
- `tests/molecule/patroni/molecule/ha/verify.yml`

**Modified files:**

- `.github/workflows/molecule.yml` — extend matrix with `postgres-default`, `patroni-single`, `patroni-ha`.
- `roles/repos/defaults/main.yml` and `roles/repos/tasks/main.yml` — no edits expected; the PGDG repo + extras (which carries patroni) are already enabled in P0. Repo priority enforcement (spec §11: PGDG > vendor > EPEL > pigsty) is handled by the amended P0 plan, not here. Verification step in Task 1.

---

## Task 1: Verify PGDG already exposes postgresql18 and patroni

**Files:**
- Read-only check: `roles/repos/tasks/main.yml`, `roles/repos/defaults/main.yml`.

This task confirms P0's `repos` role already arranges what P2a needs, so we don't duplicate it. No code change here, but it gates the rest of the plan.

- [ ] **Step 1: Skim the repos role**

Run: `grep -n 'extras\|pgdg' roles/repos/defaults/main.yml roles/repos/tasks/main.yml`
Expected: `repos_pgdg_extras_repo: pgdg-rhel10-extras` is set and the task `Enable PGDG extras repo` toggles `enabled=1`. The task `Enable selected PGDG PostgreSQL major repo` enables `pgdg{{ postgres_version }}`.

- [ ] **Step 2: Confirm patroni is in PGDG extras**

Run on a Rocky 10 box (or skip if you trust the PGDG repo):
```bash
podman run --rm rockylinux/rockylinux:10 bash -c '
  dnf -y install https://download.postgresql.org/pub/repos/yum/reporpms/EL-10-x86_64/pgdg-redhat-repo-latest.noarch.rpm > /dev/null 2>&1 &&
  dnf --enablerepo=pgdg-rhel10-extras list patroni patroni-etcd python3-psycopg3
'
```
Expected: all three packages listed, source=`pgdg-rhel10-extras` (or `pgdg-common` for python3-psycopg3). If patroni is missing, fall back to installing from EPEL by toggling `repos_epel_enabled: true` in defaults — note this in Task 4's `_install.yml`.

- [ ] **Step 3: No commit.**

Verification only.

---

## Task 2: Add postgres role defaults

**Files:**
- Create: `roles/postgres/defaults/main.yml`

- [ ] **Step 1: Write defaults**

```yaml
---
# roles/postgres/defaults/main.yml
# Variables prefixed `postgres_`. Vendor PGDG paths so SELinux fcontext
# is inherited where possible. Patroni (P2a) drives bootstrap; this role
# never runs initdb or writes postgresql.conf.

# Versioning - postgres_version comes from group_vars/all.yml (default 18).
postgres_pkg_server: "postgresql{{ postgres_version }}-server"
postgres_pkg_contrib: "postgresql{{ postgres_version }}-contrib"
postgres_pkg_libs: "postgresql{{ postgres_version }}"
postgres_support_packages:
  - python3-libselinux
  - policycoreutils-python-utils

# Filesystem (vendor defaults)
postgres_base_dir: "/var/lib/pgsql"
postgres_version_dir: "{{ postgres_base_dir }}/{{ postgres_version }}"
postgres_data_dir: "{{ postgres_version_dir }}/data"
postgres_log_dir: "{{ postgres_version_dir }}/log"
postgres_user: postgres
postgres_group: postgres
postgres_data_dir_mode: "0700"

# OS / vendor unit handling
postgres_vendor_systemd_unit: "postgresql-{{ postgres_version }}.service"
postgres_mask_vendor_unit: true

# Network (Patroni reads these via group_vars)
postgres_listen_address: "{{ network_any_address | default('0.0.0.0') }}"
postgres_port: "{{ postgres_port | default(5432) }}"
```

- [ ] **Step 2: Lint**

Run: `yamllint roles/postgres/defaults/main.yml`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add roles/postgres/defaults/main.yml
git commit -m "feat(postgres): role defaults"
```

---

## Task 3: Add postgres role meta and README

**Files:**
- Create: `roles/postgres/meta/main.yml`
- Create: `roles/postgres/README.md`

- [ ] **Step 1: Write `meta/main.yml`**

```yaml
---
galaxy_info:
  role_name: postgres
  author: pigsty-lite
  description: Install PostgreSQL from PGDG; do not initialize the cluster (Patroni owns bootstrap).
  license: Apache-2.0
  min_ansible_version: "2.16"
  platforms:
    - name: EL
      versions: ["10"]
dependencies: []
```

- [ ] **Step 2: Write `README.md`**

```markdown
# postgres

Install PostgreSQL `{{ postgres_version }}` from PGDG, prepare vendor-default
filesystem layout, register SELinux fcontext for non-vendor data dirs, and
mask the vendor `postgresql-<ver>.service` unit so Patroni (P2a) is the only
process that starts/stops PG.

## What this role does NOT do

- No `initdb`. Patroni owns cluster bootstrap.
- No `postgresql.conf` editing. Tuning profile is rendered by `patroni`
  role into `patroni.yml -> postgresql.parameters`.
- No replication slot management. Patroni handles slots at the DCS layer.

## Variables

See `defaults/main.yml`. The most important downstream contract is that
`postgres_data_dir` matches the path Patroni writes into its own
`postgresql.data_dir`. Both roles consume `group_vars/postgres.yml` for
this; do not override per-host unless you really mean it.

## SELinux

Vendor data dir `/var/lib/pgsql/<ver>/data` carries `postgresql_db_t` by
default. If `postgres_data_dir` is overridden, this role registers an
fcontext rule and runs `restorecon`. We never `setenforce 0`.

## Firewalld

This role opens nothing. The `patroni-rest` service is opened by the
patroni role (P2a). Postgres port 5432 is exposed via HAProxy in P2b.
```

- [ ] **Step 3: Commit**

```bash
git add roles/postgres/meta/main.yml roles/postgres/README.md
git commit -m "feat(postgres): role meta and README"
```

---

## Task 4: Add postgres tasks (skeleton)

**Files:**
- Create: `roles/postgres/tasks/main.yml`
- Create: `roles/postgres/tasks/_assert.yml`
- Create: `roles/postgres/tasks/_install.yml`
- Create: `roles/postgres/tasks/_filesystem.yml`
- Create: `roles/postgres/tasks/_seport.yml`
- Create: `roles/postgres/tasks/_mask_vendor.yml`

- [ ] **Step 1: `roles/postgres/tasks/main.yml`**

```yaml
---
- name: Validate preconditions
  ansible.builtin.import_tasks: _assert.yml
  tags: [postgres, assert]

- name: Install PostgreSQL packages
  ansible.builtin.import_tasks: _install.yml
  tags: [postgres, install]

- name: Prepare PostgreSQL filesystem
  ansible.builtin.import_tasks: _filesystem.yml
  tags: [postgres, install]

- name: Register SELinux ports
  ansible.builtin.import_tasks: _seport.yml
  tags: [postgres, install, selinux]

- name: Mask vendor systemd unit
  ansible.builtin.import_tasks: _mask_vendor.yml
  tags: [postgres, install]
```

- [ ] **Step 2: `roles/postgres/tasks/_assert.yml`**

```yaml
---
- name: Fail if postgres group is empty
  ansible.builtin.assert:
    that:
      - groups['postgres'] | length > 0
    fail_msg: "Inventory group 'postgres' is empty; P2a requires at least one host."
  run_once: true
  delegate_to: localhost

- name: Fail if exactly one host has postgres_role=primary
  ansible.builtin.assert:
    that:
      - (groups['postgres']
         | map('extract', hostvars, 'postgres_role')
         | select('equalto', 'primary')
         | list | length) == 1
    fail_msg: >-
      Exactly one host in the 'postgres' group must have
      postgres_role=primary; check your inventory.
  run_once: true
  delegate_to: localhost

- name: Verify per-host certificate exists (P0 certs role)
  ansible.builtin.stat:
    path: "{{ pki_dir }}/{{ inventory_hostname }}.crt"
  register: postgres_cert_stat

- name: Fail if certificate missing
  ansible.builtin.assert:
    that:
      - postgres_cert_stat.stat.exists
    fail_msg: >-
      Expected per-host certificate at {{ pki_dir }}/{{ inventory_hostname }}.crt;
      run the P0 _node.yml playbook (certs role) first.
```

- [ ] **Step 3: `roles/postgres/tasks/_install.yml`**

```yaml
---
- name: Install PostgreSQL server, contrib, and support packages
  ansible.builtin.dnf:
    name:
      - "{{ postgres_pkg_server }}"
      - "{{ postgres_pkg_contrib }}"
      - "{{ postgres_pkg_libs }}"
    state: present

- name: Install support packages for SELinux management
  ansible.builtin.dnf:
    name: "{{ postgres_support_packages }}"
    state: present
```

- [ ] **Step 4: `roles/postgres/tasks/_filesystem.yml`**

```yaml
---
- name: Ensure base dir exists
  ansible.builtin.file:
    path: "{{ postgres_base_dir }}"
    state: directory
    owner: "{{ postgres_user }}"
    group: "{{ postgres_group }}"
    mode: "0755"

- name: Ensure version dir exists
  ansible.builtin.file:
    path: "{{ postgres_version_dir }}"
    state: directory
    owner: "{{ postgres_user }}"
    group: "{{ postgres_group }}"
    mode: "0755"

- name: Ensure data dir exists (empty, owned by postgres)
  ansible.builtin.file:
    path: "{{ postgres_data_dir }}"
    state: directory
    owner: "{{ postgres_user }}"
    group: "{{ postgres_group }}"
    mode: "{{ postgres_data_dir_mode }}"

- name: Ensure log dir exists
  ansible.builtin.file:
    path: "{{ postgres_log_dir }}"
    state: directory
    owner: "{{ postgres_user }}"
    group: "{{ postgres_group }}"
    mode: "0700"

- name: Register SELinux fcontext for custom data dir
  community.general.sefcontext:
    target: "{{ postgres_data_dir }}(/.*)?"
    setype: postgresql_db_t
    state: present
  register: postgres_sefcontext
  when:
    - ansible_facts.selinux.status == "enabled"
    - postgres_data_dir != "/var/lib/pgsql/" ~ postgres_version ~ "/data"

- name: Relabel data dir if fcontext changed
  ansible.builtin.command:
    cmd: "restorecon -RF {{ postgres_data_dir }}"
  when:
    - ansible_facts.selinux.status == "enabled"
    - postgres_sefcontext is defined
    - postgres_sefcontext.changed
  changed_when: true
```

- [ ] **Step 5: `roles/postgres/tasks/_seport.yml`**

```yaml
---
# Postgres on default 5432 already carries postgresql_port_t. We only
# label custom ports. Patroni REST 8008 also needs http_port_t (or a
# custom label); we use the existing http_port_t which SELinux already
# permits the system to bind.

- name: Allow custom postgres port via SELinux
  community.general.seport:
    ports: "{{ postgres_port }}"
    proto: tcp
    setype: postgresql_port_t
    state: present
  when:
    - ansible_facts.selinux.status == "enabled"
    - (postgres_port | int) != 5432
```

- [ ] **Step 6: `roles/postgres/tasks/_mask_vendor.yml`**

```yaml
---
- name: Check whether vendor postgres unit exists
  ansible.builtin.stat:
    path: "/usr/lib/systemd/system/{{ postgres_vendor_systemd_unit }}"
  register: postgres_vendor_unit_stat

- name: Mask vendor postgres systemd unit (Patroni owns lifecycle)
  ansible.builtin.systemd:
    name: "{{ postgres_vendor_systemd_unit }}"
    masked: true
    enabled: false
    state: stopped
  when:
    - postgres_mask_vendor_unit | bool
    - postgres_vendor_unit_stat.stat.exists
```

- [ ] **Step 7: Commit**

```bash
git add roles/postgres/tasks
git commit -m "feat(postgres): install, filesystem, seport, mask-vendor tasks"
```

---

## Task 5: Write postgres molecule scenario (failing)

**Files:**
- Create: `tests/molecule/postgres/molecule/default/molecule.yml`
- Create: `tests/molecule/postgres/molecule/default/prepare.yml`
- Create: `tests/molecule/postgres/molecule/default/converge.yml`
- Create: `tests/molecule/postgres/molecule/default/verify.yml`

- [ ] **Step 1: `molecule.yml`**

```yaml
---
driver:
  name: podman
platforms:
  - name: pigsty-lite-postgres
    image: docker.io/rockylinux/rockylinux:10-ubi-init
    pre_build_image: true
    privileged: true
    command: /usr/sbin/init
    volume_mounts:
      - "/sys/fs/cgroup:/sys/fs/cgroup:rw"
    capabilities: [SYS_ADMIN]
    systemd: always
    groups: [postgres]
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
    host_vars:
      pigsty-lite-postgres:
        ansible_host: 127.0.0.1
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
- name: Prepare - generate CA on control side
  hosts: localhost
  gather_facts: false
  vars:
    ca_dir: "{{ playbook_dir }}/_tmp_ca"
  roles:
    - role: ../../../../../roles/ca

- name: Prepare - install repos and issue host cert on the target
  hosts: all
  gather_facts: true
  become: true
  vars:
    certs_ca_dir_on_control: "{{ playbook_dir }}/_tmp_ca"
    repos_pgdg_enabled: true
    repos_pgdg_rpm_url: "https://download.postgresql.org/pub/repos/yum/reporpms/EL-10-x86_64/pgdg-redhat-repo-latest.noarch.rpm"
  roles:
    - role: ../../../../../roles/repos
    - role: ../../../../../roles/certs
```

- [ ] **Step 3: `converge.yml`**

```yaml
---
- name: Converge - apply postgres role
  hosts: postgres
  become: true
  gather_facts: true
  vars:
    network_any_address: 0.0.0.0
    network_loopback_address: 127.0.0.1
  roles:
    - role: ../../../../../roles/postgres
```

- [ ] **Step 4: `verify.yml`**

```yaml
---
- name: Verify
  hosts: postgres
  become: true
  tasks:
    - name: postgresql server binary present
      ansible.builtin.stat:
        path: "/usr/pgsql-18/bin/postgres"
      register: pg_bin

    - name: Assert postgres binary installed
      ansible.builtin.assert:
        that:
          - pg_bin.stat.exists
          - pg_bin.stat.executable

    - name: Data dir exists, empty, postgres-owned
      ansible.builtin.command: ls -A /var/lib/pgsql/18/data
      register: data_dir_listing
      changed_when: false

    - name: Assert data dir is empty (Patroni hasn't run)
      ansible.builtin.assert:
        that:
          - data_dir_listing.stdout == ""
        fail_msg: "data dir contained files: {{ data_dir_listing.stdout_lines }}"

    - name: Vendor postgresql-18 unit is masked
      ansible.builtin.command: systemctl is-enabled postgresql-18.service
      register: vendor_state
      changed_when: false
      failed_when: false

    - name: Assert vendor unit masked
      ansible.builtin.assert:
        that:
          - vendor_state.stdout in ["masked", "disabled"]
        fail_msg: "vendor postgresql-18 unit is {{ vendor_state.stdout }}; expected masked"
```

- [ ] **Step 5: Run scenario, expect failure**

Run: `cd tests/molecule/postgres && molecule test -s default`
Expected: FAIL during converge because tasks were committed in Task 4 — actually the test should now pass create+prepare+converge but may surface SELinux fcontext issues in podman. If converge passes and verify passes, that's the desired green state; skip Task 6.

- [ ] **Step 6: Commit the scenario**

```bash
git add tests/molecule/postgres
git commit -m "test(postgres): default molecule scenario"
```

---

## Task 6: Fix any postgres molecule failure

If Task 5 step 5 turned green, skip this task entirely.

**Files (likely):**
- Possibly modify: `roles/postgres/tasks/_filesystem.yml` (selinux gate)
- Possibly modify: `roles/postgres/tasks/_install.yml` (extras repo)

- [ ] **Step 1: Inspect the failing task output**

Common modes:
- `dnf install postgresql18-server` fails: PGDG repo wasn't enabled by `prepare.yml`. Check `repos_pgdg_enabled` and that the `pgdg18` section is enabled in `/etc/yum.repos.d/pgdg-redhat-all.repo`. Fix is in the prepare playbook, not the role.
- `community.general.sefcontext` fails because `selinux` facts aren't gathered: add `setup` task in `_assert.yml` with `gather_subset: ['selinux']`, or change the `when` to `ansible_facts.selinux is defined and ansible_facts.selinux.status == "enabled"`.
- vendor unit not present (PGDG packaging changed): the stat/when guard already covers this — no fix needed.

- [ ] **Step 2: Apply the minimal fix in the role**

Example (selinux fact gate):
```yaml
# roles/postgres/tasks/_filesystem.yml — change `when` clauses
when:
  - ansible_facts.selinux is defined
  - ansible_facts.selinux.status == "enabled"
  - postgres_data_dir != "/var/lib/pgsql/" ~ postgres_version ~ "/data"
```

- [ ] **Step 3: Re-run scenario**

Run: `cd tests/molecule/postgres && molecule test -s default`
Expected: PASS including idempotence.

- [ ] **Step 4: Commit fix**

```bash
git add roles/postgres
git commit -m "fix(postgres): <specific fix>"
```

---

## Task 7: Add patroni firewalld custom service

**Files:**
- Create: `files/firewalld/services/patroni-rest.xml`

- [ ] **Step 1: Write the XML**

```xml
<?xml version="1.0" encoding="utf-8"?>
<service>
  <short>patroni-rest</short>
  <description>Patroni REST API used for cluster control and HAProxy health checks.</description>
  <port protocol="tcp" port="8008"/>
</service>
```

- [ ] **Step 2: Lint**

Run: `xmllint --noout files/firewalld/services/patroni-rest.xml`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add files/firewalld/services/patroni-rest.xml
git commit -m "feat(firewalld): patroni-rest custom service definition"
```

---

## Task 8: Add patroni role defaults

**Files:**
- Create: `roles/patroni/defaults/main.yml`

- [ ] **Step 1: Write defaults**

```yaml
---
# roles/patroni/defaults/main.yml
# Variables prefixed `patroni_`. Patroni owns the PG cluster lifecycle:
# initdb, recovery, replication slots, switchover. This role only places
# the binary, the config, the systemd unit, and the firewalld rule —
# everything else is Patroni's job.

# Packages
patroni_packages:
  - patroni
  - patroni-etcd
  - python3-psycopg3

# Filesystem
patroni_config_dir: /etc/patroni
patroni_config_file: "{{ patroni_config_dir }}/patroni.yml"
patroni_log_dir: /var/log/patroni
patroni_pki_dir: "{{ pki_dir | default('/etc/pki/pigsty-lite') }}"

# TLS (reuse per-host cert from P0 certs role) - Patroni REST and PG SSL
patroni_cert_file: "{{ patroni_pki_dir }}/{{ inventory_hostname }}.crt"
patroni_key_file: "{{ patroni_pki_dir }}/{{ inventory_hostname }}.key"
patroni_trusted_ca_file: "{{ patroni_pki_dir }}/ca.crt"

# Network
patroni_listen_address: "{{ network_any_address | default('0.0.0.0') }}"
patroni_advertise_address: "{{ ansible_host | default(ansible_default_ipv4.address) }}"
patroni_rest_port: "{{ patroni_rest_port | default(8008) }}"

# DCS - derive endpoints from groups['etcd']
patroni_etcd_protocol: https
patroni_etcd_hosts: >-
  [{% for h in groups['etcd'] -%}
    "{{ patroni_etcd_protocol }}://{{ hostvars[h].ansible_host }}:{{ etcd_client_port | default(2379) }}"
    {%- if not loop.last %},{% endif %}
  {%- endfor %}]

# Cluster identity
patroni_scope: "{{ cluster_name | default('pigsty-lite') }}"
patroni_namespace: "/{{ patroni_scope }}/"

# PG bootstrap — operator-tunable knobs.
patroni_bootstrap_timeout_seconds: 300
patroni_replication_user: replicator
patroni_superuser: postgres
patroni_rewind_user: rewind_user

# Memory-derived params (overridable)
patroni_shared_buffer_ratio: "{{ postgres_shared_buffer_ratio | default(0.25) }}"
patroni_tune_profile: "{{ postgres_tune_profile | default('oltp') }}"

# Service control
patroni_service_name: patroni
patroni_systemd_dropin_dir: "/etc/systemd/system/{{ patroni_service_name }}.service.d"
patroni_systemd_dropin_file: "{{ patroni_systemd_dropin_dir }}/10-pigsty-lite.conf"

# Firewall
patroni_firewalld_zone: "{{ firewalld_default_zone | default('public') }}"
patroni_firewalld_service_name: patroni-rest

# Health gates
patroni_leader_wait_retries: "{{ (patroni_bootstrap_timeout_seconds | int) // 5 }}"
patroni_leader_wait_delay: 5
```

- [ ] **Step 2: Lint**

Run: `yamllint roles/patroni/defaults/main.yml`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add roles/patroni/defaults/main.yml
git commit -m "feat(patroni): role defaults"
```

---

## Task 9: Add patroni meta and README

**Files:**
- Create: `roles/patroni/meta/main.yml`
- Create: `roles/patroni/README.md`

- [ ] **Step 1: `meta/main.yml`**

```yaml
---
galaxy_info:
  role_name: patroni
  author: pigsty-lite
  description: Install Patroni and run PostgreSQL under its supervision, backed by the P1 etcd DCS.
  license: Apache-2.0
  min_ansible_version: "2.16"
  platforms:
    - name: EL
      versions: ["10"]
dependencies: []
```

- [ ] **Step 2: `README.md`**

```markdown
# patroni

Install Patroni from PGDG, render `/etc/patroni/patroni.yml`, hand
PostgreSQL lifecycle control to Patroni, and gate on a healthy cluster
state before returning.

## Cluster bootstrap

Patroni's own bootstrap runs on the first node to claim the leader key
in etcd. The role does not pre-seed `postgres_role=primary` host as
"leader" — Patroni decides via DCS race. In practice the host marked
`postgres_role=primary` will win because it is the first to start in
fresh deployments; if it loses the race for some reason, the cluster is
still correct.

## Replication slots

Managed by Patroni. We do not pre-create them.

## TLS

REST API and PG `ssl=on` both use the per-host cert issued by the P0
certs role. Replication uses certificate authentication via the
`replicator` system role; the password from `artifacts/credentials.txt`
is the secondary credential (used in the rare case where cert auth is
not available, e.g. during `pg_basebackup` over a temporary TCP
connection).

## What this role does NOT do

- No business databases, users, or HBA rules. P3 (`provision`) handles
  that via `community.postgresql` modules.
- No pgBouncer, HAProxy, or VIP. P2b adds those.
- No backups. P4 wires pgBackRest.

## Variables

See `defaults/main.yml`. The most important contract:
- `patroni_etcd_hosts` is computed from `groups['etcd']`. Override only
  in extreme edge cases (e.g. external etcd not in the inventory).
- `patroni_scope` defaults to `cluster_name`; this becomes the etcd key
  prefix and Patroni cluster name.
```

- [ ] **Step 3: Commit**

```bash
git add roles/patroni/meta roles/patroni/README.md
git commit -m "feat(patroni): role meta and README"
```

---

## Task 10: Add patroni tuning profiles

**Files:**
- Create: `roles/patroni/files/tuning/oltp.conf`
- Create: `roles/patroni/files/tuning/olap.conf`
- Create: `roles/patroni/files/tuning/tiny.conf`

These are read at template time and inlined into `patroni.yml`'s
`postgresql.parameters` block. Memory-derived params (`shared_buffers`,
`effective_cache_size`, `work_mem`, `maintenance_work_mem`) are computed
in the template; everything below is a static baseline.

- [ ] **Step 1: `tuning/oltp.conf`**

```ini
# Pigsty-lite OLTP baseline (PostgreSQL 18)
# Static parameters; memory-derived values are computed at deploy time.

# Connections / process model
max_connections = 200
superuser_reserved_connections = 10

# WAL / replication
wal_level = replica
max_wal_senders = 16
max_replication_slots = 16
wal_keep_size = 2GB
hot_standby = on
hot_standby_feedback = on
synchronous_commit = on

# Checkpointing
checkpoint_timeout = 15min
checkpoint_completion_target = 0.9
min_wal_size = 1GB
max_wal_size = 8GB

# Background writer / autovacuum
bgwriter_delay = 50ms
bgwriter_lru_maxpages = 1000
autovacuum_max_workers = 5
autovacuum_naptime = 30s

# Query planning
random_page_cost = 1.1
effective_io_concurrency = 200
default_statistics_target = 200

# Logging
log_destination = 'stderr'
logging_collector = on
log_directory = 'log'
log_filename = 'postgresql-%a.log'
log_rotation_age = 1d
log_rotation_size = 0
log_truncate_on_rotation = on
log_min_duration_statement = 1000
log_checkpoints = on
log_connections = on
log_disconnections = on
log_lock_waits = on
log_temp_files = 0
log_autovacuum_min_duration = 0
log_line_prefix = '%m [%p] %q%u@%d/%a '

# Stats / extensions
shared_preload_libraries = 'pg_stat_statements'
track_activity_query_size = 4096
track_io_timing = on
track_functions = pl
```

- [ ] **Step 2: `tuning/olap.conf`**

```ini
# Pigsty-lite OLAP baseline (PostgreSQL 18)
# Tilted toward fewer, longer queries with bigger memory grants.

max_connections = 100
superuser_reserved_connections = 10

wal_level = replica
max_wal_senders = 16
max_replication_slots = 16
wal_keep_size = 2GB
hot_standby = on
synchronous_commit = on

checkpoint_timeout = 30min
checkpoint_completion_target = 0.9
min_wal_size = 2GB
max_wal_size = 32GB

bgwriter_delay = 100ms
autovacuum_max_workers = 3
autovacuum_naptime = 60s

random_page_cost = 1.1
effective_io_concurrency = 200
default_statistics_target = 500

max_parallel_workers = 16
max_parallel_workers_per_gather = 8
max_parallel_maintenance_workers = 4

log_destination = 'stderr'
logging_collector = on
log_directory = 'log'
log_filename = 'postgresql-%a.log'
log_min_duration_statement = 5000
log_checkpoints = on
log_lock_waits = on
log_temp_files = 0
log_autovacuum_min_duration = 0
log_line_prefix = '%m [%p] %q%u@%d/%a '

shared_preload_libraries = 'pg_stat_statements'
track_io_timing = on
```

- [ ] **Step 3: `tuning/tiny.conf`**

```ini
# Pigsty-lite TINY baseline (PostgreSQL 18)
# Smallest viable footprint; suitable for a 2-CPU / 4GB-RAM dev VM.

max_connections = 50
superuser_reserved_connections = 5

wal_level = replica
max_wal_senders = 4
max_replication_slots = 4
wal_keep_size = 256MB
hot_standby = on

checkpoint_timeout = 10min
checkpoint_completion_target = 0.9
min_wal_size = 256MB
max_wal_size = 2GB

bgwriter_delay = 200ms
autovacuum_max_workers = 2
autovacuum_naptime = 60s

random_page_cost = 1.1
effective_io_concurrency = 100
default_statistics_target = 100

log_destination = 'stderr'
logging_collector = on
log_directory = 'log'
log_filename = 'postgresql-%a.log'
log_min_duration_statement = 1000
log_line_prefix = '%m [%p] %q%u@%d/%a '

shared_preload_libraries = 'pg_stat_statements'
```

- [ ] **Step 4: Commit**

```bash
git add roles/patroni/files/tuning
git commit -m "feat(patroni): tuning profiles oltp/olap/tiny"
```

---

## Task 11: Add patroni.yml.j2 template

**Files:**
- Create: `roles/patroni/templates/patroni.yml.j2`

- [ ] **Step 1: Write the template**

```jinja
# {{ ansible_managed }}
# Rendered by roles/patroni. Do not edit by hand. To change tunables,
# edit responses/site.rsp.yml and re-run `make deploy`.

scope: {{ patroni_scope }}
namespace: {{ patroni_namespace }}
name: {{ inventory_hostname }}

restapi:
  listen: {{ patroni_listen_address }}:{{ patroni_rest_port }}
  connect_address: {{ patroni_advertise_address }}:{{ patroni_rest_port }}
  certfile: {{ patroni_cert_file }}
  keyfile: {{ patroni_key_file }}
  cafile: {{ patroni_trusted_ca_file }}
  verify_client: optional

ctl:
  certfile: {{ patroni_cert_file }}
  keyfile: {{ patroni_key_file }}
  cafile: {{ patroni_trusted_ca_file }}

etcd3:
  hosts: {{ patroni_etcd_hosts }}
  protocol: {{ patroni_etcd_protocol }}
  cacert: {{ patroni_trusted_ca_file }}
  cert: {{ patroni_cert_file }}
  key: {{ patroni_key_file }}

bootstrap:
  dcs:
    ttl: 30
    loop_wait: 10
    retry_timeout: 10
    maximum_lag_on_failover: 1048576
    synchronous_mode: false
    postgresql:
      use_pg_rewind: true
      use_slots: true
      parameters:
{% set memtotal_mb = ansible_memtotal_mb | default(4096) | int %}
{% set shared_buffers_mb = ((memtotal_mb * (patroni_shared_buffer_ratio | float)) | int) %}
{% set effective_cache_mb = ((memtotal_mb * 0.6) | int) %}
{% set work_mem_mb = ([(memtotal_mb // 64), 4] | max) %}
{% set maint_mem_mb = ([(memtotal_mb // 16), 64] | max) %}
        shared_buffers: {{ shared_buffers_mb }}MB
        effective_cache_size: {{ effective_cache_mb }}MB
        work_mem: {{ work_mem_mb }}MB
        maintenance_work_mem: {{ maint_mem_mb }}MB
        listen_addresses: '{{ postgres_listen_address }}'
        port: {{ postgres_port }}
        ssl: 'on'
        ssl_cert_file: '{{ patroni_cert_file }}'
        ssl_key_file: '{{ patroni_key_file }}'
        ssl_ca_file: '{{ patroni_trusted_ca_file }}'
        ssl_min_protocol_version: 'TLSv1.2'

  initdb:
    - encoding: UTF8
    - data-checksums
    - locale: C.UTF-8

  pg_hba:
    - local all all peer
    - host all postgres 127.0.0.1/32 trust
    - host replication {{ patroni_replication_user }} 127.0.0.1/32 trust
    - hostssl replication {{ patroni_replication_user }} 0.0.0.0/0 cert
    - hostssl all all 0.0.0.0/0 scram-sha-256

  users:
    {{ patroni_superuser }}:
      password: '{{ patroni_superuser_password }}'
      options:
        - SUPERUSER
    {{ patroni_replication_user }}:
      password: '{{ patroni_replication_password }}'
      options:
        - REPLICATION

postgresql:
  listen: {{ postgres_listen_address }}:{{ postgres_port }}
  connect_address: {{ patroni_advertise_address }}:{{ postgres_port }}
  data_dir: {{ postgres_data_dir }}
  bin_dir: /usr/pgsql-{{ postgres_version }}/bin
  pgpass: /var/lib/pgsql/.pgpass_patroni
  authentication:
    superuser:
      username: {{ patroni_superuser }}
      password: '{{ patroni_superuser_password }}'
    replication:
      username: {{ patroni_replication_user }}
      password: '{{ patroni_replication_password }}'
      sslmode: verify-ca
      sslrootcert: {{ patroni_trusted_ca_file }}
      sslcert: {{ patroni_cert_file }}
      sslkey: {{ patroni_key_file }}
    rewind:
      username: {{ patroni_rewind_user }}
      password: '{{ patroni_rewind_password }}'

  parameters:
{% set tune_path = role_path ~ '/files/tuning/' ~ patroni_tune_profile ~ '.conf' %}
{% for line in (lookup('file', tune_path)).splitlines() %}
{%   if line.strip() and not line.lstrip().startswith('#') %}
    {{ line }}
{%   endif %}
{% endfor %}
{% for k, v in (postgres_extra_parameters | default({})).items() %}
    {{ k }} = {{ v }}
{% endfor %}

tags:
  nofailover: false
  noloadbalance: false
  clonefrom: false
  nosync: false
```

Notes:
- `patroni_superuser_password`, `patroni_replication_password`, and
  `patroni_rewind_password` are not in `defaults/main.yml`. They MUST be
  supplied by the operator-facing layer. Task 12 wires
  `group_vars/postgres.yml` to default them to fixtures suitable for
  dev/molecule; production responses must override them via the
  response file.
- The `pg_hba` block above is the bootstrap-time HBA. Once the cluster
  exists, P3 owns runtime HBA via `community.postgresql.postgresql_pg_hba`.

- [ ] **Step 2: Lint**

Run: `yamllint -d "{rules: {line-length: disable}}" roles/patroni/templates/patroni.yml.j2 || true`
Expected: any output is informational only — Jinja templates are not strict YAML.

- [ ] **Step 3: Commit**

```bash
git add roles/patroni/templates/patroni.yml.j2
git commit -m "feat(patroni): patroni.yml.j2 template"
```

---

## Task 12: Wire group_vars/postgres.yml

**Files:**
- Modify: `group_vars/postgres.yml`

- [ ] **Step 1: Replace placeholder**

```yaml
---
# group_vars/postgres.yml - per-cluster overrides for the postgres group.
# Role defaults live in roles/postgres/defaults/main.yml and
# roles/patroni/defaults/main.yml. Variables here encode shared decisions
# that span both roles.

# Patroni passwords. In dev / molecule these are fine. In production the
# operator overrides them via the response file (see configure +
# group_vars/response.yml). Never check production passwords in git.
patroni_superuser_password: "{{ vault_patroni_superuser_password | default('postgres-dev-password-change-me') }}"
patroni_replication_password: "{{ vault_patroni_replication_password | default('replicator-dev-password-change-me') }}"
patroni_rewind_password: "{{ vault_patroni_rewind_password | default('rewind-dev-password-change-me') }}"

# Memory tuning ratio, surfaced to patroni_shared_buffer_ratio.
postgres_shared_buffer_ratio: 0.25

# Tuning profile file selected from roles/patroni/files/tuning/.
postgres_tune_profile: oltp

# Operator overrides applied AFTER tuning profile (always win).
postgres_extra_parameters: {}
```

- [ ] **Step 2: Lint**

Run: `yamllint group_vars/postgres.yml`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add group_vars/postgres.yml
git commit -m "feat(patroni): group_vars defaults for passwords and tuning"
```

---

## Task 13: Add patroni systemd drop-in

**Files:**
- Create: `roles/patroni/templates/systemd-override.conf.j2`

- [ ] **Step 1: Write template**

```ini
# {{ ansible_managed }}
[Service]
LimitNOFILE=65536
Restart=on-failure
RestartSec=5s
TimeoutStartSec={{ patroni_bootstrap_timeout_seconds }}
```

- [ ] **Step 2: Commit**

```bash
git add roles/patroni/templates/systemd-override.conf.j2
git commit -m "feat(patroni): systemd drop-in for limits and restart policy"
```

---

## Task 14: Add patroni handler

**Files:**
- Create: `roles/patroni/handlers/main.yml`

- [ ] **Step 1: Write handler**

```yaml
---
- name: Restart patroni
  ansible.builtin.systemd:
    name: "{{ patroni_service_name }}"
    state: restarted
    daemon_reload: true
```

- [ ] **Step 2: Commit**

```bash
git add roles/patroni/handlers/main.yml
git commit -m "feat(patroni): restart handler"
```

---

## Task 15: Add patroni tasks (skeleton)

**Files:**
- Create: `roles/patroni/tasks/main.yml`
- Create: `roles/patroni/tasks/_assert.yml`
- Create: `roles/patroni/tasks/_install.yml`
- Create: `roles/patroni/tasks/_configure.yml`
- Create: `roles/patroni/tasks/_firewall.yml`
- Create: `roles/patroni/tasks/_service.yml`

- [ ] **Step 1: `roles/patroni/tasks/main.yml`**

```yaml
---
- name: Validate preconditions
  ansible.builtin.import_tasks: _assert.yml
  tags: [patroni, assert]

- name: Install Patroni packages
  ansible.builtin.import_tasks: _install.yml
  tags: [patroni, install]

- name: Configure Patroni
  ansible.builtin.import_tasks: _configure.yml
  tags: [patroni, config]

- name: Open firewalld for Patroni REST
  ansible.builtin.import_tasks: _firewall.yml
  tags: [patroni, firewall]

- name: Start Patroni and wait for healthy cluster
  ansible.builtin.import_tasks: _service.yml
  tags: [patroni, service]
```

- [ ] **Step 2: `roles/patroni/tasks/_assert.yml`**

```yaml
---
- name: Fail if etcd group is empty
  ansible.builtin.assert:
    that:
      - groups['etcd'] | length > 0
    fail_msg: "Patroni requires the etcd DCS to exist; etcd group is empty."
  run_once: true
  delegate_to: localhost

- name: Verify per-host certificate exists
  ansible.builtin.stat:
    path: "{{ patroni_cert_file }}"
  register: patroni_cert_stat

- name: Fail if certificate missing
  ansible.builtin.assert:
    that:
      - patroni_cert_stat.stat.exists
    fail_msg: >-
      Expected per-host certificate at {{ patroni_cert_file }};
      run the P0 _node.yml playbook (certs role) first.

- name: Verify postgres binary present (postgres role ran)
  ansible.builtin.stat:
    path: "/usr/pgsql-{{ postgres_version }}/bin/postgres"
  register: patroni_pg_bin_stat

- name: Fail if postgres binary missing
  ansible.builtin.assert:
    that:
      - patroni_pg_bin_stat.stat.exists
    fail_msg: >-
      Expected postgres binary at /usr/pgsql-{{ postgres_version }}/bin/postgres;
      ensure the postgres role ran first (_postgres_install.yml).
```

- [ ] **Step 3: `roles/patroni/tasks/_install.yml`**

```yaml
---
- name: Install Patroni and DCS client
  ansible.builtin.dnf:
    name: "{{ patroni_packages }}"
    state: present

- name: Ensure patroni log dir exists
  ansible.builtin.file:
    path: "{{ patroni_log_dir }}"
    state: directory
    owner: "{{ postgres_user }}"
    group: "{{ postgres_group }}"
    mode: "0750"

- name: Ensure patroni config dir exists
  ansible.builtin.file:
    path: "{{ patroni_config_dir }}"
    state: directory
    owner: root
    group: "{{ postgres_group }}"
    mode: "0750"
```

- [ ] **Step 4: `roles/patroni/tasks/_configure.yml`**

```yaml
---
- name: Render patroni.yml
  ansible.builtin.template:
    src: patroni.yml.j2
    dest: "{{ patroni_config_file }}"
    owner: root
    group: "{{ postgres_group }}"
    mode: "0640"
  notify: Restart patroni

- name: Allow patroni to read its host private key
  ansible.builtin.file:
    path: "{{ patroni_key_file }}"
    owner: "{{ postgres_user }}"
    group: "{{ postgres_group }}"
    mode: "0640"

- name: Ensure patroni systemd drop-in dir exists
  ansible.builtin.file:
    path: "{{ patroni_systemd_dropin_dir }}"
    state: directory
    owner: root
    group: root
    mode: "0755"

- name: Render patroni systemd drop-in
  ansible.builtin.template:
    src: systemd-override.conf.j2
    dest: "{{ patroni_systemd_dropin_file }}"
    owner: root
    group: root
    mode: "0644"
  notify: Restart patroni
```

- [ ] **Step 5: `roles/patroni/tasks/_firewall.yml`**

```yaml
---
- name: Install custom patroni-rest firewalld service
  ansible.builtin.copy:
    src: "{{ playbook_dir | dirname }}/files/firewalld/services/patroni-rest.xml"
    dest: /etc/firewalld/services/patroni-rest.xml
    owner: root
    group: root
    mode: "0644"
  register: patroni_fw_svc

- name: Reload firewalld if service definition changed
  ansible.builtin.command: firewall-cmd --reload
  when: patroni_fw_svc.changed
  changed_when: true

- name: Open patroni-rest in default zone
  ansible.posix.firewalld:
    service: "{{ patroni_firewalld_service_name }}"
    permanent: true
    state: enabled
    immediate: true
    zone: "{{ patroni_firewalld_zone }}"
```

- [ ] **Step 6: `roles/patroni/tasks/_service.yml`**

```yaml
---
- name: Enable and start patroni
  ansible.builtin.systemd:
    name: "{{ patroni_service_name }}"
    enabled: true
    state: started
    daemon_reload: true

- name: Wait for Patroni REST API to respond
  ansible.builtin.uri:
    url: "https://{{ patroni_advertise_address }}:{{ patroni_rest_port }}/health"
    method: GET
    return_content: false
    validate_certs: false
    status_code: [200, 503]
  register: patroni_rest_probe
  retries: "{{ patroni_leader_wait_retries }}"
  delay: "{{ patroni_leader_wait_delay }}"
  until: patroni_rest_probe.status in [200, 503]

- name: Wait for cluster to have a leader (run once on primary host)
  ansible.builtin.uri:
    url: "https://{{ patroni_advertise_address }}:{{ patroni_rest_port }}/leader"
    method: GET
    return_content: false
    validate_certs: false
    status_code: 200
  register: patroni_leader_probe
  retries: "{{ patroni_leader_wait_retries }}"
  delay: "{{ patroni_leader_wait_delay }}"
  until: patroni_leader_probe.status == 200
  when: postgres_role == 'primary'

- name: Wait for this member to report state=running
  ansible.builtin.uri:
    url: "https://{{ patroni_advertise_address }}:{{ patroni_rest_port }}/patroni"
    method: GET
    return_content: true
    validate_certs: false
    status_code: 200
  register: patroni_member_probe
  retries: "{{ patroni_leader_wait_retries }}"
  delay: "{{ patroni_leader_wait_delay }}"
  until: (patroni_member_probe.json.state | default('')) == 'running'
```

- [ ] **Step 7: Commit**

```bash
git add roles/patroni/tasks
git commit -m "feat(patroni): install, configure, firewall, service tasks"
```

---

## Task 16: Wire postgres + patroni into site.yml

**Files:**
- Create: `playbooks/_postgres_install.yml`
- Create: `playbooks/_postgres_bootstrap.yml`
- Modify: `playbooks/site.yml`
- Modify: `playbooks/tags.md`

- [ ] **Step 1: `playbooks/_postgres_install.yml`**

```yaml
---
- name: P2a postgres - install PG packages and prepare filesystem
  hosts: postgres
  become: true
  gather_facts: true
  roles:
    - role: postgres
      tags: [postgres]
```

- [ ] **Step 2: `playbooks/_postgres_bootstrap.yml`**

```yaml
---
- name: P2a patroni - bootstrap PG cluster under Patroni supervision
  hosts: postgres
  become: true
  gather_facts: true
  roles:
    - role: patroni
      tags: [patroni]
```

- [ ] **Step 3: Modify `playbooks/site.yml`**

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
```

- [ ] **Step 4: Modify `playbooks/tags.md`**

Replace contents with:

```markdown
# Tag reference

## Module tags

- `preflight`
- `ca`
- `node`
- `repos`
- `certs`
- `etcd`
- `postgres`
- `patroni`

## Action tags (used inside roles in later sub-plans)

- `install`
- `config`
- `restart`
- `provision`
- `firewall`
- `service`
- `assert`
- `selinux`

## Examples

- `--tags preflight` - only run the preflight role.
- `--tags ca` - only (re)generate the CA on localhost.
- `--tags etcd` - install/configure the etcd cluster.
- `--tags postgres` - install PG, prepare fs, mask vendor unit.
- `--tags patroni` - configure and start Patroni; safe re-run.
- `--tags patroni,config` - render patroni.yml without restart (handler still flushes if file changed).
```

- [ ] **Step 5: Commit**

```bash
git add playbooks/_postgres_install.yml playbooks/_postgres_bootstrap.yml playbooks/site.yml playbooks/tags.md
git commit -m "feat(playbooks): wire P2a postgres + patroni into site"
```

---

## Task 17: Patroni single-node molecule scenario (failing)

**Files:**
- Create: `tests/molecule/patroni/molecule/single/molecule.yml`
- Create: `tests/molecule/patroni/molecule/single/prepare.yml`
- Create: `tests/molecule/patroni/molecule/single/converge.yml`
- Create: `tests/molecule/patroni/molecule/single/verify.yml`

- [ ] **Step 1: `molecule.yml`**

```yaml
---
driver:
  name: podman
platforms:
  - name: pigsty-lite-patroni-single
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
      pigsty-lite-patroni-single:
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

- name: Prepare - repos, certs, etcd, postgres install
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
```

- [ ] **Step 3: `converge.yml`**

```yaml
---
- name: Converge - apply patroni role
  hosts: postgres
  become: true
  gather_facts: true
  vars:
    network_any_address: 0.0.0.0
    network_loopback_address: 127.0.0.1
    firewalld_default_zone: public
  roles:
    - role: ../../../../../roles/patroni
```

- [ ] **Step 4: `verify.yml`**

```yaml
---
- name: Verify
  hosts: postgres
  become: true
  tasks:
    - name: patroni service active
      ansible.builtin.systemd:
        name: patroni
      register: patroni_unit

    - name: Assert patroni active
      ansible.builtin.assert:
        that:
          - patroni_unit.status.ActiveState == "active"

    - name: Patroni reports leader
      ansible.builtin.uri:
        url: "https://127.0.0.1:8008/leader"
        validate_certs: false
        status_code: 200

    - name: Member state running
      ansible.builtin.uri:
        url: "https://127.0.0.1:8008/patroni"
        validate_certs: false
        return_content: true
      register: member_state

    - name: Assert state running, role primary
      ansible.builtin.assert:
        that:
          - member_state.json.state == "running"
          - member_state.json.role == "primary"

    - name: Cluster sees one member, one leader
      ansible.builtin.uri:
        url: "https://127.0.0.1:8008/cluster"
        validate_certs: false
        return_content: true
      register: cluster_state

    - name: Assert one member, one leader
      ansible.builtin.assert:
        that:
          - cluster_state.json.members | length == 1
          - (cluster_state.json.members | selectattr('role', 'equalto', 'leader') | list | length) == 1

    - name: psql can connect via TCP with cert auth disabled (localhost trust rule)
      ansible.builtin.command: >
        /usr/pgsql-18/bin/psql -h 127.0.0.1 -U postgres -d postgres -c "select 1"
      become: true
      become_user: postgres
      register: psql_probe
      changed_when: false

    - name: Assert psql returned 1
      ansible.builtin.assert:
        that:
          - "'1' in psql_probe.stdout"
```

- [ ] **Step 5: Run scenario, expect failure or pass**

Run: `cd tests/molecule/patroni && molecule test -s single`
Expected: PASS if no bugs in tasks 11/15. The most likely failure is the
patroni.yml template — Patroni is strict about YAML.

- [ ] **Step 6: Commit scenario**

```bash
git add tests/molecule/patroni/molecule/single
git commit -m "test(patroni): single-node molecule scenario"
```

---

## Task 18: Fix any single-node failure

If Task 17 step 5 went green, skip this task.

**Files:**
- Likely: `roles/patroni/templates/patroni.yml.j2`

- [ ] **Step 1: Inspect failure**

If `journalctl -u patroni` shows YAML parse errors, render the template
manually with:
```bash
cd tests/molecule/patroni/molecule/single
podman exec pigsty-lite-patroni-single cat /etc/patroni/patroni.yml | python3 -c 'import sys, yaml; yaml.safe_load(sys.stdin)'
```

Common issues:
- Tuning include indentation: a stray space in `tuning/oltp.conf` line breaks the `parameters:` block. Fix is to harden the Jinja loop in Task 11's template by stripping the line and re-indenting consistently.
- `etcd3.hosts` needs to be a YAML list, not a JSON-looking string. The current expression `[...]` produces a string when the loop runs, which Patroni accepts as long as quoting is consistent. If Patroni rejects, replace with explicit YAML list rendering:
  ```yaml
  etcd3:
    hosts:
  {% for h in groups['etcd'] %}
      - "{{ patroni_etcd_protocol }}://{{ hostvars[h].ansible_host }}:{{ etcd_client_port | default(2379) }}"
  {% endfor %}
  ```

- [ ] **Step 2: Apply fix in template**

Edit `roles/patroni/templates/patroni.yml.j2` per the diagnosis.

- [ ] **Step 3: Re-run**

Run: `cd tests/molecule/patroni && molecule test -s single`
Expected: PASS including idempotence.

- [ ] **Step 4: Commit**

```bash
git add roles/patroni
git commit -m "fix(patroni): <specific fix>"
```

---

## Task 19: Patroni HA (3-node) molecule scenario

**Files:**
- Create: `tests/molecule/patroni/molecule/ha/molecule.yml`
- Create: `tests/molecule/patroni/molecule/ha/prepare.yml`
- Create: `tests/molecule/patroni/molecule/ha/converge.yml`
- Create: `tests/molecule/patroni/molecule/ha/verify.yml`

- [ ] **Step 1: `molecule.yml`**

```yaml
---
driver:
  name: podman
platforms:
  - name: pigsty-lite-patroni-ha-1
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
      - name: pigsty-lite-patroni
  - name: pigsty-lite-patroni-ha-2
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
      - name: pigsty-lite-patroni
  - name: pigsty-lite-patroni-ha-3
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
      - name: pigsty-lite-patroni
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
      pigsty-lite-patroni-ha-1:
        postgres_role: primary
        etcd_seq: 1
      pigsty-lite-patroni-ha-2:
        postgres_role: replica
        etcd_seq: 2
      pigsty-lite-patroni-ha-3:
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

- name: Prepare - repos, certs, etcd, postgres install on every target
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

- name: Converge - apply patroni role on all members
  hosts: postgres
  become: true
  gather_facts: true
  vars:
    network_any_address: 0.0.0.0
    network_loopback_address: 127.0.0.1
    firewalld_default_zone: public
  roles:
    - role: ../../../../../roles/patroni
```

- [ ] **Step 4: `verify.yml`**

```yaml
---
- name: Pin ansible_host to each container's network IP
  hosts: all
  gather_facts: true
  tasks:
    - name: Set ansible_host = default IPv4
      ansible.builtin.set_fact:
        ansible_host: "{{ ansible_default_ipv4.address }}"

- name: Verify
  hosts: postgres
  become: true
  tasks:
    - name: patroni service active on every host
      ansible.builtin.systemd:
        name: patroni
      register: patroni_unit

    - name: Assert patroni active
      ansible.builtin.assert:
        that:
          - patroni_unit.status.ActiveState == "active"

    - name: This member is running
      ansible.builtin.uri:
        url: "https://{{ ansible_host }}:8008/patroni"
        validate_certs: false
        return_content: true
      register: member_state
      retries: 12
      delay: 5
      until: (member_state.json.state | default('')) == 'running'

    - name: Assert member state running
      ansible.builtin.assert:
        that:
          - member_state.json.state == "running"

- name: Verify cluster from one node
  hosts: postgres
  become: true
  run_once: true
  tasks:
    - name: Cluster has 3 members and exactly 1 leader
      ansible.builtin.uri:
        url: "https://{{ ansible_host }}:8008/cluster"
        validate_certs: false
        return_content: true
      register: cluster_state
      retries: 12
      delay: 5
      until:
        - (cluster_state.json.members | length) == 3
        - (cluster_state.json.members | selectattr('role', 'equalto', 'leader') | list | length) == 1

    - name: Two replicas in streaming state
      ansible.builtin.assert:
        that:
          - (cluster_state.json.members
             | selectattr('role', 'equalto', 'replica')
             | selectattr('state', 'equalto', 'streaming')
             | list | length) == 2
        fail_msg: >-
          Expected 2 replicas in streaming state.
          Got: {{ cluster_state.json.members }}

    - name: Write a probe row on the primary
      ansible.builtin.command: >
        /usr/pgsql-18/bin/psql -h 127.0.0.1 -U postgres -d postgres
        -c "create table if not exists probe_p2a (id int);
            insert into probe_p2a values (42);"
      become: true
      become_user: postgres
      changed_when: false

- name: Verify replication on every replica
  hosts: postgres
  become: true
  tasks:
    - name: Read probe row on this host
      ansible.builtin.command: >
        /usr/pgsql-18/bin/psql -h 127.0.0.1 -U postgres -d postgres -tAc "select id from probe_p2a where id=42"
      become_user: postgres
      register: probe_read
      retries: 12
      delay: 5
      changed_when: false
      until: probe_read.stdout.strip() == "42"

    - name: Assert probe row visible on this host
      ansible.builtin.assert:
        that:
          - probe_read.stdout.strip() == "42"
```

- [ ] **Step 5: Run scenario**

Run: `cd tests/molecule/patroni && molecule test -s ha`
Expected: PASS, including idempotence and the replication probe on every replica.

- [ ] **Step 6: Commit scenario**

```bash
git add tests/molecule/patroni/molecule/ha
git commit -m "test(patroni): ha 3-node molecule scenario"
```

---

## Task 20: Wire molecule scenarios into CI

**Files:**
- Modify: `.github/workflows/molecule.yml`

- [ ] **Step 1: Extend the matrix**

Open `.github/workflows/molecule.yml` and add three rows to the `include:` matrix block.

After the existing `etcd ha` row, append:

```yaml
          - role: postgres
            scenario: default
          - role: patroni
            scenario: single
          - role: patroni
            scenario: ha
```

The existing job step already uses `${{ matrix.role }}` and
`${{ matrix.scenario }}`, so no other change is needed.

- [ ] **Step 2: Lint workflow**

Run: `yamllint .github/workflows/molecule.yml`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/molecule.yml
git commit -m "ci(molecule): add postgres-default, patroni-single, patroni-ha"
```

---

## Task 21: Update docs/operations/firstrun.md

**Files:**
- Modify: `docs/operations/firstrun.md`

- [ ] **Step 1: Add a P2a section after the etcd section**

Find the heading `### etcd (P1)` (or its equivalent in the current file —
the P1 plan added text that may live as a section or as appended prose).
After that block, add:

```markdown
### postgres + patroni (P2a)

After `_etcd.yml` succeeds, two playbooks run against the `postgres` group:

- `_postgres_install.yml` (postgres role) installs `postgresql18-server`
  and `postgresql18-contrib` from PGDG, prepares
  `/var/lib/pgsql/18/data` empty with `postgres:postgres` ownership, and
  masks `postgresql-18.service` so Patroni is the only process that
  starts and stops PG.
- `_postgres_bootstrap.yml` (patroni role) installs Patroni, renders
  `/etc/patroni/patroni.yml` (REST + PG SSL both backed by the
  pigsty-lite CA), opens firewalld `patroni-rest` (8008/tcp), starts
  `patroni.service`, and gates on `https://<host>:8008/patroni`
  reporting `state=running` for every member plus `https://<primary>:8008/leader`
  returning 200.

Profile mapping:

- `single`: 1 PG host with `postgres_role=primary`. No replicas.
- `ha`: 1 primary + N replicas (default 2). Patroni elects the leader
  via etcd; the host marked `postgres_role=primary` typically wins the
  initial race.

Useful commands:

```bash
# Show cluster state from any member
sudo -u postgres patronictl -c /etc/patroni/patroni.yml list

# Check cluster via REST
curl -sk https://$(hostname -i):8008/cluster | jq

# psql via the local trust rule (postgres dbsu over loopback)
sudo -u postgres /usr/pgsql-18/bin/psql -h 127.0.0.1 -U postgres -c '\l'
```

Patroni passwords are NOT auto-generated in P2a. Until configure CLI
support lands (separate task), set them in the response file:

```yaml
postgres:
  ...
  patroni_passwords:    # key not currently parsed; see TODO in P2a plan
    superuser: "..."    # vault-encrypted in production
    replication: "..."
    rewind: "..."
```

For now, override directly in `group_vars/postgres.yml` or
`group_vars/response.yml`.
```

- [ ] **Step 2: Commit**

```bash
git add docs/operations/firstrun.md
git commit -m "docs(ops): firstrun guide P2a postgres + patroni section"
```

---

## Task 22: Update docs/superpowers/plans pointer + README roadmap

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update Status line**

In `README.md`, change the `**Status:**` paragraph from:

```
**Status:** P0 (Foundation) and P1 (etcd) are complete. Subsequent sub-plans
(P2 PostgreSQL HA, P3 provisioning, P4 backups, P5 monitoring, P6
lifecycle/portability) are pending. ...
```

To:

```
**Status:** P0 (Foundation), P1 (etcd), and P2a (PostgreSQL + Patroni)
are complete. Subsequent sub-plans (P2b connection layer, P3 provisioning,
P4 backups, P5 monitoring, P6 lifecycle/portability) are pending. The
architecture and scope are defined in
[`docs/superpowers/specs/2026-05-12-pigsty-lite-design.md`](docs/superpowers/specs/2026-05-12-pigsty-lite-design.md).
```

- [ ] **Step 2: Update Roadmap table**

Replace the P2 row with three rows:

```markdown
| P2a | PostgreSQL + Patroni (HA cluster bootstrap) | done |
| P2b | Connection layer: pgBouncer + HAProxy + vip-manager | pending |
| P2c | Integration tests + RTO measurement | pending |
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs(readme): mark P2a done, split P2 into P2a/P2b/P2c"
```

---

## Task 23: Local smoke test on RHEL 10 VMs (optional)

This step is **optional** and **not run in CI**.

**Prerequisite:** A 4-VM libvirt environment matching the `ha` profile (1
monitor + 3 PG hosts) reachable via SSH with sudo. Storage is
operator-provisioned per the spec.

- [ ] **Step 1: Generate inventory and response**

```bash
./configure -c ha
# Edit responses/site.rsp.yml to fill IPs.
./configure -s -f responses/site.rsp.yml
```

- [ ] **Step 2: Plan**

```bash
make plan
```

Expected: `--check --diff` shows what would change. No errors.

- [ ] **Step 3: Deploy**

```bash
make deploy
```

Expected: P0 + P1 + P2a all green. Final task is the patroni member-state
gate on every postgres host.

- [ ] **Step 4: Verify cluster**

```bash
ssh pgnode01 sudo -u postgres patronictl -c /etc/patroni/patroni.yml list
```

Expected: 3 members, 1 leader, 2 replicas with state `streaming`.

- [ ] **Step 5: Verify SELinux still enforcing on every host**

```bash
ansible postgres -i inventory/site.yml -a 'getenforce'
```

Expected: every host returns `Enforcing`.

- [ ] **Step 6: Verify firewalld**

```bash
ansible postgres -i inventory/site.yml -b -a 'firewall-cmd --list-services'
```

Expected: at minimum `ssh`, `etcd-server`, `etcd-client`, `patroni-rest`.

- [ ] **Step 7: Re-run for idempotence**

```bash
make deploy
```

Expected: zero changed tasks.

No commit. Verification only.

---

## Self-review notes

1. **Spec coverage check.** Spec §3.4 "deploy order" calls for
   `postgres_install → postgres_bootstrap (patroni; needs etcd)`. P2a
   ships exactly those two playbooks (Task 16). Spec §4 "Roles" rows
   `postgres` and `patroni` are addressed by Tasks 2-6 and 8-15 with
   responsibilities matching the spec table. Spec §6.1 "Postgres node"
   firewall expectations: `patroni-rest` is opened (Task 7 + Task 15
   `_firewall.yml`); haproxy/pgbouncer ports are deferred to P2b per
   the user's scope decision; node_exporter, pgexporter, etc. are P5.
   Spec §7.5 "Postgres parameter management" three layers: tuning
   profile (Task 10), memory-derived params (Task 11 template), extra
   parameters override (Task 11 template + group_vars Task 12). Spec
   §7.6 "pg_hba.conf" — bootstrap HBA in template (Task 11); runtime
   HBA management is P3, called out in Task 9 README. Spec §8.6
   "Idempotency" is enforced by every Molecule scenario's `idempotence`
   step. Spec §8.7 "Error handling" rows for "Patroni doesn't elect
   leader in `patroni_bootstrap_timeout`" mapped to
   `patroni_bootstrap_timeout_seconds` + leader-wait retries in Task 8.

2. **Placeholder scan.** Every code block is concrete. The one TODO-ish
   note in Task 21 ("until configure CLI support lands") points to
   future work but does not place a TODO in code. The patroni README
   (Task 9) mentions the credentials.txt-based password story but does
   not pretend it exists yet — P2a defers that to a configure-CLI
   change.

3. **Variable / type consistency.** `pki_dir`, `cluster_name`,
   `postgres_version`, `postgres_port`, `postgres_listen_address`,
   `postgres_data_dir`, `postgres_user`, `postgres_group`,
   `network_any_address`, `firewalld_default_zone`,
   `etcd_client_port` are all defined in `group_vars/all.yml` (P0) or
   `roles/<role>/defaults` and consumed identically across tasks.
   `patroni_*` variables are introduced in Task 8 and reused without
   rename in Tasks 11, 12, 13, 14, 15. `postgres_role` is read in
   Task 4 (`_assert.yml`) and Task 15 (`_service.yml`) and
   provided by inventory (already wired in P0 by `_generate_inventory.py`).

4. **Why not split `_postgres_install` and `_postgres_bootstrap` further.**
   Spec §5.2 "One-job-per-playbook rule" justifies them as separate
   playbooks; this plan respects that. They share the `postgres`
   target group, so they read as a vertical slice but execute as two
   independent playbooks per the spec.

5. **What's out of scope for P2a (do not implement):**
   - pgBouncer, HAProxy, vip-manager (P2b).
   - Business databases, users, runtime HBA (P3).
   - Backups (P4).
   - Monitoring (exporters, dashboards) (P5).
   - Auto-generation of Patroni passwords by `configure` CLI; for now
     they are dev fixtures in `group_vars/postgres.yml` and operators
     override via vault-encrypted vars (Task 21 doc note).
   - `community.postgresql.postgresql_pg_hba` runtime HBA management
     (P3).
   - SELinux booleans for `nginx_proxy` or other monitor-host components
     (P5).

6. **Why no `serial: 1` on `_postgres_bootstrap.yml`.** Patroni handles
   ordering via the DCS leader election. Running all members in
   parallel is correct: the one that wins the etcd race becomes leader
   and bootstraps; the rest see a leader present and clone via
   `pg_basebackup`. `serial: 1` would add no safety and would slow the
   first deploy substantially.

7. **Why `verify_client: optional` in restapi.** Patroni REST needs to
   accept HAProxy health checks (P2b) without client cert auth. We
   keep mTLS for `ctl` (which is operator/superuser tooling) but allow
   anonymous TLS for read-only health probes. Spec does not contradict
   this — §6.1 lists `patroni-rest:8008` as a cross-host service
   reachable from the monitor and the postgres group, not from
   arbitrary clients (firewalld restricts source CIDRs).

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-13-p2a-postgres-patroni.md`. Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
