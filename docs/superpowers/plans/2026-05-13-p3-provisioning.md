# P3 (Provisioning: HBA + roles + databases + extensions) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the cluster usable to applications. After P3, an operator edits `responses/site.rsp.yml` and `make deploy` creates/updates PostgreSQL roles, databases, installed extensions, and pg_hba rules from declarative input on the Patroni leader, with steady-state idempotence.

**Architecture:** A single new thin role `roles/provision/` runs `run_once: true` against the `postgres` group, delegating to whichever host currently holds the Patroni leader (looked up via `https://<host>:8008/cluster`). It uses the official `community.postgresql` collection (`postgresql_user`, `postgresql_db`, `postgresql_ext`, `postgresql_pg_hba`) over the local Unix socket as the OS database superuser (`postgres_osdba`, default `postgres` — the OS account that owns the data directory and the `postgres` process, analogous to Oracle's `oracle` software owner) — no network round-trips, no password-in-flight. pg_hba is fully managed: the role renders the canonical rule set (system rules + monitor + operator rules from `postgres_hba_rules`) into `postgresql.auto.conf`'s `hba_file` location and signals Patroni to reload (`PATCH /config` with no body triggers `pg_reload_conf()`; we use `postgresql_query SELECT pg_reload_conf()` for clarity). Patroni's bootstrap-time `pg_hba` block in `patroni.yml.j2` is downgraded to the minimum required for first init (local + replication peers); steady-state HBA lives in the `provision` role.

**Tech Stack:** Ansible role + `community.postgresql` collection (already pinned in `requirements.yml`), `python3-psycopg3` (already installed by P2a's patroni role), `ansible.builtin.uri` for the Patroni REST leader lookup, Molecule + podman for the role test, single-node and ha scenarios.

---

## File Structure

**New files (in `roles/provision/`):**

- `roles/provision/defaults/main.yml` — empty defaults plus `provision_*` knobs (idempotence-related: `provision_pg_hba_managed: true`, `provision_db_owner_default: postgres`, `provision_extensions_in_db: postgres`). The OS database superuser is **not** a `provision_` knob — it's a cluster-wide fact (`postgres_osdba`), so it lives in `group_vars/all.yml` (Task 1 Step 1) and the role just references it.
- `roles/provision/meta/main.yml` — galaxy_info, no role deps (collection deps live in `requirements.yml`).
- `roles/provision/tasks/main.yml` — orchestrate: leader lookup → run on leader → assert preconditions → render HBA → manage roles → manage dbs → manage extensions → reload PG.
- `roles/provision/tasks/_leader.yml` — look up the current Patroni leader (sets `provision_leader_host`).
- `roles/provision/tasks/_assert.yml` — assert leader found, OS database superuser (`postgres_osdba`) socket reachable, `community.postgresql` collection present.
- `roles/provision/tasks/_hba.yml` — render `pg_hba.conf` lines via `community.postgresql.postgresql_pg_hba` (one task per rule), then signal reload via handler.
- `roles/provision/tasks/_users.yml` — `community.postgresql.postgresql_user` per `postgres_users` entry.
- `roles/provision/tasks/_databases.yml` — `community.postgresql.postgresql_db` per `postgres_databases` entry.
- `roles/provision/tasks/_extensions.yml` — `community.postgresql.postgresql_ext` per `postgres_extensions` entry, in the database named by `provision_extensions_in_db` (default `postgres`).
- `roles/provision/handlers/main.yml` — `Reload PostgreSQL` (calls `postgresql_query SELECT pg_reload_conf()`).
- `roles/provision/README.md` — variables, ordering guarantees, day-2 examples.

**New playbook + wiring:**

- `playbooks/_provision.yml` — runs the `provision` role with `become: true`, `become_user: postgres`, on `postgres` group with `run_once: true` semantics enforced inside the role via leader delegation.
- `playbooks/site.yml` — modify to import `_provision.yml` after `_vip_manager.yml`.
- `playbooks/tags.md` — add `provision` module tag.
- `group_vars/all.yml` — add `postgres_osdba: postgres` (the OS account owning the data dir and `postgres` process; named for the Oracle `OSDBA` group convention). Cluster-wide, referenced by the `provision` role.
- `group_vars/postgres.yml` — already carries `patroni_superuser` / `patroni_superuser_password`; no change needed unless we add `provision_*` knobs (we don't — defaults live in the role).

**Modified files:**

- `roles/patroni/templates/patroni.yml.j2:63-68` — shrink the bootstrap `pg_hba` to the minimum for first init: the role HBA section becomes `[local all all peer, host all postgres 127.0.0.1/32 trust, host replication <replicator> 127.0.0.1/32 trust, hostssl replication <replicator> 0.0.0.0/0 cert]`. The wildcard `hostssl all all 0.0.0.0/0 scram-sha-256` line is **removed** — it must be added by `provision` when the operator declares HBA rules. This is a behavior change; see Task 12 for the migration test.
- `bin/_response_schema.py` — extend `_validate_postgres` to also validate `postgres.users` (list of `{name, password, roles?}`) and `postgres.databases` (list of `{name, owner?}`) and `postgres.extensions` (list of strings). Today the generator copies these through but the schema does not check them.
- `responses/single.rsp.yml.example` and `responses/ha.rsp.yml.example` — already declare `extensions`, `databases`, `users`, `hba_rules`. No content change; we add a comment block above them pointing at P3's day-2 workflow.
- `.github/workflows/molecule.yml` — extend matrix with `provision/default` (single-node) and `provision/ha` (3-node).
- `docs/operations/firstrun.md` — add a P3 section.
- `docs/operations/day2-provisioning.md` — **new**, short runbook for "add a database / add a user / add an extension."
- `README.md` — flip P3 to done in the roadmap.

**New test files:**

- `tests/molecule/provision/molecule/default/{molecule,prepare,converge,verify}.yml` — single-node, full P0+P1+P2a+P2b stack underneath.
- `tests/molecule/provision/molecule/ha/{molecule,prepare,converge,verify}.yml` — 3-node, asserts the role runs only on the leader and produces identical state on replicas via streaming replication.
- `tests/configure/test_schema.py` — extend with cases for the new postgres user/database/extension schema rules.

**Out of scope (deferred):**

- Vault-encrypted user passwords end-to-end test — `community.postgresql.postgresql_user` accepts plain Jinja-rendered strings; vault decryption is Ansible's job. We document the operator workflow but don't add a vault-decrypt fixture in CI.
- Privileges/grants beyond default-owner-on-database. The spec sample uses `roles: [dbrole_readwrite]`; we materialize membership but not per-table grants.
- Schema migrations (Liquibase/sqitch/etc.) — out of scope per spec §13.7.
- Default privileges (`ALTER DEFAULT PRIVILEGES`) — operators can add via `extra_parameters` or future P-phase.

---

## Task 1: cluster-wide `postgres_osdba` + provision role defaults

**Files:**
- Modify: `group_vars/all.yml`
- Create: `roles/provision/defaults/main.yml`

- [ ] **Step 1: Add `postgres_osdba` to `group_vars/all.yml`**

Open `group_vars/all.yml`. In the postgres-related block (near `postgres_port` / `postgres_version`), add:

```yaml
# OS database superuser: the OS account that owns the PostgreSQL data
# directory and runs the `postgres` process. Named for the Oracle
# `OSDBA` group convention (cf. the `oracle` software owner). The
# provision role connects over the local Unix socket as this account
# (peer auth — no password on the wire).
postgres_osdba: postgres
```

- [ ] **Step 2: Write provision role defaults**

```yaml
---
# roles/provision/defaults/main.yml
# Variables prefixed `provision_`. The role consumes `postgres_*` lists from
# the response file (`postgres_users`, `postgres_databases`,
# `postgres_extensions`, `postgres_hba_rules`); knobs below tune behavior.
# `postgres_osdba` is cluster-wide and lives in group_vars/all.yml, not here.

# Patroni REST endpoint for leader lookup. Per-host; the role iterates
# until it gets a 200.
provision_patroni_rest_scheme: https
provision_patroni_rest_port: "{{ patroni_rest_port | default(8008) }}"
provision_patroni_rest_validate_certs: false
provision_patroni_rest_timeout: 5

# Connect as the OS database superuser over the local Unix socket.
# Avoids password-on-the-wire. `postgres_osdba` is defined in
# group_vars/all.yml; the default below is a safety net only.
provision_osdba: "{{ postgres_osdba | default('postgres') }}"
provision_osdba_socket_dir: /var/run/postgresql

# Where to install extensions when the response file doesn't say.
# Most cluster-level extensions (pg_stat_statements) live in `postgres`;
# operators can override per-extension via the dict form (Task 9).
provision_extensions_in_db: postgres

# pg_hba.conf management
provision_pg_hba_managed: true
provision_pg_hba_path: "{{ patroni_postgres_data_dir | default('/var/lib/pgsql/' ~ postgres_version ~ '/data') }}/pg_hba.conf"
# System rules always present at the top.
provision_pg_hba_system_rules:
  - { contype: local, databases: all, users: all, method: peer }
  - { contype: host, databases: all, users: postgres, source_addr: 127.0.0.1/32, method: trust }
  - { contype: host, databases: replication, users: "{{ patroni_replication_user | default('replicator') }}", source_addr: 127.0.0.1/32, method: trust }
  - { contype: hostssl, databases: replication, users: "{{ patroni_replication_user | default('replicator') }}", source_addr: 0.0.0.0/0, method: cert }
# Monitor rule: pg_monitor user from the monitor host. Empty until P5.
provision_pg_hba_monitor_rules: []
```

- [ ] **Step 3: Lint**

Run: `yamllint group_vars/all.yml roles/provision/defaults/main.yml`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add group_vars/all.yml roles/provision/defaults/main.yml
git commit -m "feat(provision): cluster-wide postgres_osdba + role defaults"
```

---

## Task 2: provision meta and README

**Files:**
- Create: `roles/provision/meta/main.yml`
- Create: `roles/provision/README.md`

- [ ] **Step 1: `meta/main.yml`**

```yaml
---
galaxy_info:
  role_name: provision
  author: pigsty-lite
  description: Apply declarative HBA, roles, databases, and extensions on the Patroni leader.
  license: Apache-2.0
  min_ansible_version: "2.16"
  platforms:
    - name: EL
      versions: ["10"]
dependencies: []
```

- [ ] **Step 2: `README.md`**

````markdown
# provision

Applies the declarative `postgres_*` lists from the response file to the
running cluster. Runs once per `make deploy`, only on the host that
currently holds the Patroni leader. After P3, day-2 workflow for "add a
database" / "add a user" / "add an extension" / "open HBA to a new CIDR"
is: edit the response file, `make deploy`.

## Inputs (from response file)

| Variable | Shape | Example |
|---|---|---|
| `postgres_users` | list of `{name, password, roles?}` | `[{name: app, password: "...", roles: [pg_read_all_data]}]` |
| `postgres_databases` | list of `{name, owner?, encoding?}` | `[{name: app, owner: app}]` |
| `postgres_extensions` | list of strings or `{name, db?}` dicts | `[pg_stat_statements, {name: pgvector, db: app}]` |
| `postgres_hba_rules` | list of `{db, user, source, method}` | `[{db: app, user: app, source: 10.0.0.0/8, method: scram-sha-256}]` |

## What this role owns

- `pg_hba.conf` (fully managed; hand-edits revert).
- Role/user existence and membership (not per-table privileges).
- Database existence and ownership.
- Extension presence in named databases.

## What this role does NOT own

- Per-table grants and ALTER DEFAULT PRIVILEGES.
- Schema migrations (use Liquibase/sqitch/etc.).
- Tablespaces (declared via `extra_parameters` on the cluster).

## Ordering

`hba` first (so a new user can immediately log in over the wire), then
`users`, then `databases`, then `extensions`. Reload PG once at the end.

## Idempotence

Second run is zero-change. The `postgresql_*` modules diff against
catalog state; the HBA module diffs against `pg_hba.conf` content.

## Tags

- `provision` — full role
- `provision,hba` — HBA only
- `provision,users` — users only
- `provision,databases` — databases only
- `provision,extensions` — extensions only
````

- [ ] **Step 3: Commit**

```bash
git add roles/provision/meta roles/provision/README.md
git commit -m "feat(provision): role meta and README"
```

---

## Task 3: provision tasks orchestration

**Files:**
- Create: `roles/provision/tasks/main.yml`

- [ ] **Step 1: Write `main.yml`**

```yaml
---
- name: Look up the Patroni leader
  ansible.builtin.import_tasks: _leader.yml
  tags: [provision, always]

- name: Validate preconditions
  ansible.builtin.import_tasks: _assert.yml
  tags: [provision, assert]

- name: Manage pg_hba.conf
  ansible.builtin.import_tasks: _hba.yml
  when: provision_pg_hba_managed | bool
  tags: [provision, hba, config]

- name: Manage PostgreSQL roles
  ansible.builtin.import_tasks: _users.yml
  tags: [provision, users, config]

- name: Manage databases
  ansible.builtin.import_tasks: _databases.yml
  tags: [provision, databases, config]

- name: Manage extensions
  ansible.builtin.import_tasks: _extensions.yml
  tags: [provision, extensions, config]
```

- [ ] **Step 2: Lint**

Run: `yamllint roles/provision/tasks/main.yml`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add roles/provision/tasks/main.yml
git commit -m "feat(provision): tasks orchestration"
```

---

## Task 4: provision leader lookup

**Files:**
- Create: `roles/provision/tasks/_leader.yml`

- [ ] **Step 1: Write `_leader.yml`**

```yaml
---
# Ask any reachable Patroni REST endpoint for the cluster view, then
# pick the leader by name. This runs once for the play and broadcasts
# `provision_leader_host` to all hosts in the play.
- name: Query any Patroni REST endpoint for cluster view
  ansible.builtin.uri:
    url: "{{ provision_patroni_rest_scheme }}://{{ ansible_host }}:{{ provision_patroni_rest_port }}/cluster"
    method: GET
    validate_certs: "{{ provision_patroni_rest_validate_certs }}"
    timeout: "{{ provision_patroni_rest_timeout }}"
    return_content: true
  register: provision_cluster_view
  retries: 30
  delay: 2
  until: provision_cluster_view.status == 200
  delegate_to: "{{ groups['postgres'][0] }}"
  run_once: true

- name: Extract leader hostname from cluster view
  ansible.builtin.set_fact:
    provision_leader_host: >-
      {{ (provision_cluster_view.json.members
          | selectattr('role', 'equalto', 'leader')
          | list | first).name }}
  run_once: true

- name: Announce leader
  ansible.builtin.debug:
    msg: "Patroni leader: {{ provision_leader_host }}; provisioning will run there."
  run_once: true
```

- [ ] **Step 2: Lint**

Run: `yamllint roles/provision/tasks/_leader.yml`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add roles/provision/tasks/_leader.yml
git commit -m "feat(provision): patroni leader lookup"
```

---

## Task 5: provision preconditions

**Files:**
- Create: `roles/provision/tasks/_assert.yml`

- [ ] **Step 1: Write `_assert.yml`**

```yaml
---
- name: Leader was identified
  ansible.builtin.assert:
    that:
      - provision_leader_host is defined
      - provision_leader_host | length > 0
    fail_msg: >-
      Could not determine Patroni leader from /cluster output. Check that
      Patroni REST is reachable and that the cluster has a leader.
  run_once: true

- name: psycopg3 is installed on the leader
  ansible.builtin.command: /usr/bin/python3 -c "import psycopg"
  register: provision_psycopg
  changed_when: false
  failed_when: provision_psycopg.rc != 0
  delegate_to: "{{ provision_leader_host }}"
  run_once: true

- name: Local socket directory exists on the leader
  ansible.builtin.stat:
    path: "{{ provision_osdba_socket_dir }}"
  register: provision_socket_dir
  delegate_to: "{{ provision_leader_host }}"
  run_once: true

- name: Local socket directory present
  ansible.builtin.assert:
    that:
      - provision_socket_dir.stat.exists
      - provision_socket_dir.stat.isdir
    fail_msg: "PostgreSQL Unix socket dir {{ provision_osdba_socket_dir }} missing on {{ provision_leader_host }}."
  run_once: true
```

- [ ] **Step 2: Lint**

Run: `yamllint roles/provision/tasks/_assert.yml`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add roles/provision/tasks/_assert.yml
git commit -m "feat(provision): assert preconditions"
```

---

## Task 6: provision pg_hba management

**Files:**
- Create: `roles/provision/tasks/_hba.yml`

- [ ] **Step 1: Write `_hba.yml`**

```yaml
---
# Render pg_hba.conf in three layers (system, monitor, operator). Each
# `community.postgresql.postgresql_pg_hba` invocation is idempotent: it
# diffs the file against desired state and writes only when needed.
# We delegate every task to the leader and run_once so we touch the
# file once per deploy.

- name: System HBA rules
  community.postgresql.postgresql_pg_hba:
    dest: "{{ provision_pg_hba_path }}"
    contype: "{{ item.contype }}"
    databases: "{{ item.databases }}"
    users: "{{ item.users }}"
    source: "{{ item.source_addr | default(omit) }}"
    method: "{{ item.method }}"
    create: true
    state: present
  loop: "{{ provision_pg_hba_system_rules }}"
  delegate_to: "{{ provision_leader_host }}"
  run_once: true
  notify: Reload PostgreSQL

- name: Monitor HBA rules
  community.postgresql.postgresql_pg_hba:
    dest: "{{ provision_pg_hba_path }}"
    contype: "{{ item.contype | default('hostssl') }}"
    databases: "{{ item.databases | default('all') }}"
    users: "{{ item.users }}"
    source: "{{ item.source_addr }}"
    method: "{{ item.method | default('scram-sha-256') }}"
    create: true
    state: present
  loop: "{{ provision_pg_hba_monitor_rules }}"
  delegate_to: "{{ provision_leader_host }}"
  run_once: true
  notify: Reload PostgreSQL

- name: Operator HBA rules from response file
  community.postgresql.postgresql_pg_hba:
    dest: "{{ provision_pg_hba_path }}"
    contype: "{{ item.contype | default('hostssl') }}"
    databases: "{{ item.db }}"
    users: "{{ item.user }}"
    source: "{{ item.source }}"
    method: "{{ item.method | default('scram-sha-256') }}"
    create: true
    state: present
  loop: "{{ postgres_hba_rules | default([]) }}"
  delegate_to: "{{ provision_leader_host }}"
  run_once: true
  notify: Reload PostgreSQL
```

- [ ] **Step 2: Lint**

Run: `yamllint roles/provision/tasks/_hba.yml`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add roles/provision/tasks/_hba.yml
git commit -m "feat(provision): pg_hba management"
```

---

## Task 7: provision users

**Files:**
- Create: `roles/provision/tasks/_users.yml`

- [ ] **Step 1: Write `_users.yml`**

```yaml
---
# Connect over the local Unix socket as the OS database superuser. `login_unix_socket`
# avoids passing a password and avoids depending on pg_hba ordering for
# the bootstrap-time `local all all peer` rule.

- name: Create / update PostgreSQL roles
  community.postgresql.postgresql_user:
    name: "{{ item.name }}"
    password: "{{ item.password | default(omit) }}"
    role_attr_flags: "{{ item.role_attr_flags | default('LOGIN') }}"
    db: "{{ item.db | default(omit) }}"
    state: present
    login_unix_socket: "{{ provision_osdba_socket_dir }}"
    login_user: "{{ provision_osdba }}"
    encrypted: true
  become: true
  become_user: "{{ provision_osdba }}"
  loop: "{{ postgres_users | default([]) }}"
  loop_control:
    label: "{{ item.name }}"
  delegate_to: "{{ provision_leader_host }}"
  run_once: true
  no_log: true

- name: Apply role memberships
  community.postgresql.postgresql_membership:
    source_role: "{{ item.0.name }}"
    target_roles: "{{ item.1 }}"
    state: present
    login_unix_socket: "{{ provision_osdba_socket_dir }}"
    login_user: "{{ provision_osdba }}"
  become: true
  become_user: "{{ provision_osdba }}"
  loop: >-
    {{ (postgres_users | default([]))
       | subelements('roles', skip_missing=True) }}
  loop_control:
    label: "{{ item.0.name }} -> {{ item.1 }}"
  delegate_to: "{{ provision_leader_host }}"
  run_once: true
```

- [ ] **Step 2: Lint**

Run: `yamllint roles/provision/tasks/_users.yml`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add roles/provision/tasks/_users.yml
git commit -m "feat(provision): manage PostgreSQL roles and memberships"
```

---

## Task 8: provision databases

**Files:**
- Create: `roles/provision/tasks/_databases.yml`

- [ ] **Step 1: Write `_databases.yml`**

```yaml
---
- name: Create / update databases
  community.postgresql.postgresql_db:
    name: "{{ item.name }}"
    owner: "{{ item.owner | default(provision_osdba) }}"
    encoding: "{{ item.encoding | default('UTF8') }}"
    lc_collate: "{{ item.lc_collate | default('C.UTF-8') }}"
    lc_ctype: "{{ item.lc_ctype | default('C.UTF-8') }}"
    template: "{{ item.template | default('template1') }}"
    state: present
    login_unix_socket: "{{ provision_osdba_socket_dir }}"
    login_user: "{{ provision_osdba }}"
  become: true
  become_user: "{{ provision_osdba }}"
  loop: "{{ postgres_databases | default([]) }}"
  loop_control:
    label: "{{ item.name }}"
  delegate_to: "{{ provision_leader_host }}"
  run_once: true
```

- [ ] **Step 2: Lint**

Run: `yamllint roles/provision/tasks/_databases.yml`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add roles/provision/tasks/_databases.yml
git commit -m "feat(provision): manage databases"
```

---

## Task 9: provision extensions

**Files:**
- Create: `roles/provision/tasks/_extensions.yml`

- [ ] **Step 1: Write `_extensions.yml`**

```yaml
---
# Extensions can be declared as bare strings (installed in
# provision_extensions_in_db, default `postgres`) or as dicts with
# explicit `db` (e.g. {name: pgvector, db: app}).

- name: Normalize extension list
  ansible.builtin.set_fact:
    provision_extensions_normalized: >-
      {{ (postgres_extensions | default([])) | map('community.general.dict_kv', 'name')
         | list
         | map('combine', {'db': provision_extensions_in_db}, recursive=False)
         | list
      if (postgres_extensions | default([])) | map('type_debug') | list | unique == ['str']
      else (postgres_extensions | default([])) }}
  run_once: true

# The set_fact above keeps the existing string entries usable. When an
# operator declares dicts directly, those dicts win as-is.
- name: Build per-entry extension list (full normalization)
  ansible.builtin.set_fact:
    provision_extensions_full: >-
      {{ (postgres_extensions | default([])) | map(
           'community.general.dict_kv', 'name'
         ) | list
         if (postgres_extensions | default([])) and
            (postgres_extensions[0] is string)
         else (postgres_extensions | default([])) }}
  run_once: true

- name: Install / upgrade extensions
  community.postgresql.postgresql_ext:
    name: "{{ item.name }}"
    db: "{{ item.db | default(provision_extensions_in_db) }}"
    state: present
    login_unix_socket: "{{ provision_osdba_socket_dir }}"
    login_user: "{{ provision_osdba }}"
  become: true
  become_user: "{{ provision_osdba }}"
  loop: "{{ provision_extensions_full }}"
  loop_control:
    label: "{{ item.name }} in {{ item.db | default(provision_extensions_in_db) }}"
  delegate_to: "{{ provision_leader_host }}"
  run_once: true
```

- [ ] **Step 2: Lint**

Run: `yamllint roles/provision/tasks/_extensions.yml`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add roles/provision/tasks/_extensions.yml
git commit -m "feat(provision): manage extensions"
```

---

## Task 10: provision reload handler

**Files:**
- Create: `roles/provision/handlers/main.yml`

- [ ] **Step 1: Write handler**

```yaml
---
- name: Reload PostgreSQL
  community.postgresql.postgresql_query:
    query: "SELECT pg_reload_conf()"
    login_unix_socket: "{{ provision_osdba_socket_dir }}"
    login_user: "{{ provision_osdba }}"
    db: postgres
  become: true
  become_user: "{{ provision_osdba }}"
  delegate_to: "{{ provision_leader_host }}"
  run_once: true
```

- [ ] **Step 2: Lint**

Run: `yamllint roles/provision/handlers/main.yml`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add roles/provision/handlers/main.yml
git commit -m "feat(provision): pg_reload_conf handler"
```

---

## Task 11: shrink Patroni bootstrap pg_hba

**Files:**
- Modify: `roles/patroni/templates/patroni.yml.j2:63-68`

- [ ] **Step 1: Read the current pg_hba block**

Run: `sed -n '63,68p' roles/patroni/templates/patroni.yml.j2`
Expected output:

```
  pg_hba:
    - local all all peer
    - host all postgres 127.0.0.1/32 trust
    - host replication {{ patroni_replication_user }} 127.0.0.1/32 trust
    - hostssl replication {{ patroni_replication_user }} 0.0.0.0/0 cert
    - hostssl all all 0.0.0.0/0 scram-sha-256
```

- [ ] **Step 2: Replace the wildcard line**

Edit `roles/patroni/templates/patroni.yml.j2`. Replace:

```
    - hostssl all all 0.0.0.0/0 scram-sha-256
```

with:

```
    # Steady-state HBA is rendered by roles/provision (P3). The bootstrap
    # block above contains only what initdb needs to elect the first
    # leader and join replicas. Operators add wildcard auth via the
    # response file's postgres.hba_rules.
```

- [ ] **Step 3: Lint**

Run: `yamllint roles/patroni/templates/patroni.yml.j2`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add roles/patroni/templates/patroni.yml.j2
git commit -m "feat(patroni): drop bootstrap wildcard HBA (now owned by P3 provision)"
```

---

## Task 12: response schema — postgres.users / databases / extensions

**Files:**
- Modify: `bin/_response_schema.py`
- Modify: `tests/configure/test_schema.py`

- [ ] **Step 1: Add failing tests**

Add to `tests/configure/test_schema.py`:

```python
def test_postgres_users_must_be_list_of_dicts():
    response = _minimal_single_response()
    response["postgres"]["users"] = ["bare-string-not-dict"]
    with pytest.raises(SchemaError, match=r"postgres\.users\[0\]: must be a mapping"):
        validate(response)


def test_postgres_users_require_name():
    response = _minimal_single_response()
    response["postgres"]["users"] = [{"password": "pw"}]
    with pytest.raises(SchemaError, match=r"postgres\.users\[0\]\.name"):
        validate(response)


def test_postgres_databases_must_be_list_of_dicts():
    response = _minimal_single_response()
    response["postgres"]["databases"] = ["bare-string"]
    with pytest.raises(SchemaError, match=r"postgres\.databases\[0\]: must be a mapping"):
        validate(response)


def test_postgres_databases_require_name():
    response = _minimal_single_response()
    response["postgres"]["databases"] = [{"owner": "app"}]
    with pytest.raises(SchemaError, match=r"postgres\.databases\[0\]\.name"):
        validate(response)


def test_postgres_extensions_must_be_strings_or_name_dicts():
    response = _minimal_single_response()
    response["postgres"]["extensions"] = [42]
    with pytest.raises(SchemaError, match=r"postgres\.extensions\[0\]"):
        validate(response)


def test_postgres_extensions_dict_requires_name():
    response = _minimal_single_response()
    response["postgres"]["extensions"] = [{"db": "app"}]
    with pytest.raises(SchemaError, match=r"postgres\.extensions\[0\]\.name"):
        validate(response)
```

If `_minimal_single_response()` does not exist yet, copy the helper from the existing tests. If the existing tests use a different helper name, use that name verbatim instead — open the file and check.

- [ ] **Step 2: Run tests; expect failure**

Run: `pytest tests/configure/test_schema.py -k postgres -v`
Expected: at least 6 FAILs (the new tests; pre-existing tests still pass).

- [ ] **Step 3: Implement schema rules**

Edit `bin/_response_schema.py`. Inside `_validate_postgres()`, after the existing `_validate_hba_rules(...)` call, add:

```python
    _validate_users(postgres)
    _validate_databases(postgres)
    _validate_extensions(postgres)
```

Then add the three helpers at module scope (above `_validate_postgres`):

```python
def _validate_users(postgres: dict) -> None:
    users = postgres.get("users", [])
    if not isinstance(users, list):
        raise SchemaError("postgres.users: must be a list")
    for index, user in enumerate(users):
        path = f"postgres.users[{index}]"
        if not isinstance(user, dict):
            raise SchemaError(f"{path}: must be a mapping")
        _require_str(user, "name", path)
        roles = user.get("roles", [])
        if not isinstance(roles, list):
            raise SchemaError(f"{path}.roles: must be a list of role names")


def _validate_databases(postgres: dict) -> None:
    dbs = postgres.get("databases", [])
    if not isinstance(dbs, list):
        raise SchemaError("postgres.databases: must be a list")
    for index, db in enumerate(dbs):
        path = f"postgres.databases[{index}]"
        if not isinstance(db, dict):
            raise SchemaError(f"{path}: must be a mapping")
        _require_str(db, "name", path)


def _validate_extensions(postgres: dict) -> None:
    exts = postgres.get("extensions", [])
    if not isinstance(exts, list):
        raise SchemaError("postgres.extensions: must be a list")
    for index, ext in enumerate(exts):
        path = f"postgres.extensions[{index}]"
        if isinstance(ext, str):
            continue
        if isinstance(ext, dict):
            _require_str(ext, "name", path)
            continue
        raise SchemaError(f"{path}: must be a string or a mapping with at least 'name'")
```

- [ ] **Step 4: Run tests; expect pass**

Run: `pytest tests/configure/test_schema.py -k postgres -v`
Expected: all PASS, including the six new tests and any pre-existing postgres-section tests.

- [ ] **Step 5: Commit**

```bash
git add bin/_response_schema.py tests/configure/test_schema.py
git commit -m "feat(configure): validate postgres users, databases, extensions"
```

---

## Task 13: provision playbook

**Files:**
- Create: `playbooks/_provision.yml`

- [ ] **Step 1: Write the playbook**

```yaml
---
- name: Apply declarative provisioning to the Patroni leader
  hosts: postgres
  become: true
  gather_facts: false
  roles:
    - role: provision
      tags: [provision]
```

- [ ] **Step 2: Commit**

```bash
git add playbooks/_provision.yml
git commit -m "feat(playbooks): _provision.yml entry point"
```

---

## Task 14: wire _provision.yml into site.yml

**Files:**
- Modify: `playbooks/site.yml`
- Modify: `playbooks/tags.md`

- [ ] **Step 1: Add the import to `site.yml`**

Edit `playbooks/site.yml`. After the `_vip_manager.yml` import block (lines ~27-29), add:

```yaml
- name: Import P3 provision playbook
  import_playbook: _provision.yml
  tags: [provision]
```

- [ ] **Step 2: Add `provision` to module tags**

Edit `playbooks/tags.md`. Under `## Module tags`, after the `vip_manager` line, add:

```
- `provision`
```

Under `## Examples`, after the `vip_manager` example, add:

```
- `--tags provision` - re-apply HBA / users / dbs / extensions on the leader.
- `--tags provision,hba` - re-render pg_hba.conf only.
- `--tags provision,users` - reconcile roles only.
```

- [ ] **Step 3: Lint**

Run: `yamllint playbooks/site.yml playbooks/tags.md`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add playbooks/site.yml playbooks/tags.md
git commit -m "feat(playbooks): wire P3 provision into site"
```

---

## Task 15: Molecule scenario — provision/default (single node)

**Files:**
- Create: `tests/molecule/provision/molecule/default/molecule.yml`
- Create: `tests/molecule/provision/molecule/default/prepare.yml`
- Create: `tests/molecule/provision/molecule/default/converge.yml`
- Create: `tests/molecule/provision/molecule/default/verify.yml`

- [ ] **Step 1: `molecule.yml`**

```yaml
---
driver:
  name: podman
platforms:
  - name: pigsty-lite-provision-default-1
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
        pigsty_pki_dir: /etc/pki/pigsty
        postgres_version: 18
        postgres_port: 5432
        certs_subject_alternative_names:
          - "DNS:{{ inventory_hostname }}"
          - "DNS:{{ inventory_hostname }}.test.local"
        postgres_users:
          - name: app
            password: "app-test-pw"
            roles: [pg_read_all_data]
        postgres_databases:
          - name: app
            owner: app
        postgres_extensions:
          - pg_stat_statements
          - { name: pgvector, db: app }
        postgres_hba_rules:
          - { db: app, user: app, source: 127.0.0.1/32, method: scram-sha-256 }
      etcd:
        etcd_initial_cluster_state: new
      postgres:
        patroni_superuser_password: superuser-test-pw
        patroni_replication_password: replicator-test-pw
        patroni_rewind_password: rewind-test-pw
        postgres_tune_profile: tiny
    host_vars:
      pigsty-lite-provision-default-1:
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
- name: Bring up full P0+P1+P2a stack
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

- name: Pin ansible_host
  hosts: all
  gather_facts: true
  tasks:
    - name: Set ansible_host to default IPv4
      ansible.builtin.set_fact:
        ansible_host: "{{ ansible_default_ipv4.address }}"
```

- [ ] **Step 3: `converge.yml`**

```yaml
---
- name: Apply provisioning role
  ansible.builtin.import_playbook: ../../../../playbooks/_provision.yml
```

- [ ] **Step 4: `verify.yml`**

```yaml
---
- name: Verify role / db / ext / HBA on leader
  hosts: postgres
  become: true
  become_user: postgres
  gather_facts: false
  tasks:
    - name: Role 'app' exists
      community.postgresql.postgresql_query:
        query: "SELECT 1 FROM pg_roles WHERE rolname = 'app'"
        db: postgres
        login_unix_socket: /var/run/postgresql
      register: q_role
      changed_when: false

    - name: Assert role exists
      ansible.builtin.assert:
        that:
          - q_role.rowcount == 1

    - name: Database 'app' exists
      community.postgresql.postgresql_query:
        query: "SELECT 1 FROM pg_database WHERE datname = 'app'"
        db: postgres
        login_unix_socket: /var/run/postgresql
      register: q_db
      changed_when: false

    - name: Assert database exists
      ansible.builtin.assert:
        that:
          - q_db.rowcount == 1

    - name: Extension pg_stat_statements installed in postgres db
      community.postgresql.postgresql_query:
        query: "SELECT 1 FROM pg_extension WHERE extname = 'pg_stat_statements'"
        db: postgres
        login_unix_socket: /var/run/postgresql
      register: q_pgss
      changed_when: false

    - name: Assert pg_stat_statements present
      ansible.builtin.assert:
        that:
          - q_pgss.rowcount == 1

    - name: Extension pgvector installed in app db
      community.postgresql.postgresql_query:
        query: "SELECT 1 FROM pg_extension WHERE extname = 'vector'"
        db: app
        login_unix_socket: /var/run/postgresql
      register: q_pgv
      changed_when: false

    - name: Assert pgvector present in app
      ansible.builtin.assert:
        that:
          - q_pgv.rowcount == 1

    - name: pg_hba contains app rule
      ansible.builtin.command: grep -E "^hostssl[[:space:]]+app[[:space:]]+app[[:space:]]+127\.0\.0\.1/32" /var/lib/pgsql/18/data/pg_hba.conf
      register: hba_grep
      changed_when: false
      failed_when: hba_grep.rc != 0

    - name: psql as 'app' over the wire works
      ansible.builtin.command: >
        /usr/pgsql-18/bin/psql
        "host=127.0.0.1 dbname=app user=app password=app-test-pw sslmode=require"
        -tAc "select 1"
      register: probe
      changed_when: false
      failed_when: probe.rc != 0 or probe.stdout.strip() != "1"
```

- [ ] **Step 5: Commit**

```bash
git add tests/molecule/provision/molecule/default
git commit -m "test(provision): default scenario verifies role/db/ext/hba/login"
```

---

## Task 16: Molecule scenario — provision/ha (3-node)

**Files:**
- Create: `tests/molecule/provision/molecule/ha/molecule.yml`
- Create: `tests/molecule/provision/molecule/ha/prepare.yml`
- Create: `tests/molecule/provision/molecule/ha/converge.yml`
- Create: `tests/molecule/provision/molecule/ha/verify.yml`

- [ ] **Step 1: `molecule.yml`**

```yaml
---
driver:
  name: podman
platforms:
  - name: pigsty-lite-provision-ha-1
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
      - name: pigsty-lite-provision
  - name: pigsty-lite-provision-ha-2
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
      - name: pigsty-lite-provision
  - name: pigsty-lite-provision-ha-3
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
      - name: pigsty-lite-provision
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
        pigsty_pki_dir: /etc/pki/pigsty
        postgres_version: 18
        postgres_port: 5432
        certs_subject_alternative_names:
          - "DNS:{{ inventory_hostname }}"
          - "DNS:{{ inventory_hostname }}.test.local"
        postgres_users:
          - name: app
            password: "app-test-pw"
            roles: [pg_read_all_data]
        postgres_databases:
          - name: app
            owner: app
        postgres_extensions:
          - pg_stat_statements
        postgres_hba_rules:
          - { db: app, user: app, source: 0.0.0.0/0, method: scram-sha-256 }
      etcd:
        etcd_initial_cluster_state: new
      postgres:
        patroni_superuser_password: superuser-test-pw
        patroni_replication_password: replicator-test-pw
        patroni_rewind_password: rewind-test-pw
        postgres_tune_profile: tiny
    host_vars:
      pigsty-lite-provision-ha-1:
        postgres_role: primary
        etcd_seq: 1
      pigsty-lite-provision-ha-2:
        postgres_role: replica
        etcd_seq: 2
      pigsty-lite-provision-ha-3:
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

Same as Task 15 Step 2 prepare.yml. Copy verbatim — the engineer may be reading tasks out of order, so do not "see Task 15."

```yaml
---
- name: Bring up full P0+P1+P2a stack
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

- name: Pin ansible_host
  hosts: all
  gather_facts: true
  tasks:
    - name: Set ansible_host to default IPv4
      ansible.builtin.set_fact:
        ansible_host: "{{ ansible_default_ipv4.address }}"
```

- [ ] **Step 3: `converge.yml`**

```yaml
---
- name: Apply provisioning role
  ansible.builtin.import_playbook: ../../../../playbooks/_provision.yml
```

- [ ] **Step 4: `verify.yml`**

```yaml
---
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
      register: cluster

    - name: Set leader fact
      ansible.builtin.set_fact:
        leader_host: >-
          {{ (cluster.json.members
              | selectattr('role', 'equalto', 'leader')
              | list | first).name }}

- name: Verify provisioning state propagates to every node
  hosts: postgres
  become: true
  become_user: postgres
  gather_facts: false
  tasks:
    - name: Role 'app' visible on every host (via streaming replication)
      community.postgresql.postgresql_query:
        query: "SELECT 1 FROM pg_roles WHERE rolname = 'app'"
        db: postgres
        login_unix_socket: /var/run/postgresql
      register: q_role
      changed_when: false

    - name: Assert role visible everywhere
      ansible.builtin.assert:
        that:
          - q_role.rowcount == 1
        fail_msg: "Role 'app' not visible on {{ inventory_hostname }}"

    - name: Database 'app' visible on every host
      community.postgresql.postgresql_query:
        query: "SELECT 1 FROM pg_database WHERE datname = 'app'"
        db: postgres
        login_unix_socket: /var/run/postgresql
      register: q_db
      changed_when: false

    - name: Assert database visible everywhere
      ansible.builtin.assert:
        that:
          - q_db.rowcount == 1
        fail_msg: "Database 'app' not visible on {{ inventory_hostname }}"

- name: Verify pg_hba on the leader contains the app rule
  hosts: postgres
  become: true
  run_once: true
  tasks:
    - name: Grep app rule on the leader's pg_hba
      ansible.builtin.command: grep -E "^hostssl[[:space:]]+app[[:space:]]+app[[:space:]]+0\.0\.0\.0/0" /var/lib/pgsql/18/data/pg_hba.conf
      register: hba_grep
      changed_when: false
      delegate_to: "{{ leader_host }}"
      failed_when: hba_grep.rc != 0

- name: Probe psql 5433 (HAProxy not in this scenario; use leader directly)
  hosts: postgres
  become: true
  run_once: true
  tasks:
    - name: psql as app over TLS to the leader
      ansible.builtin.command: >
        /usr/pgsql-18/bin/psql
        "host={{ hostvars[leader_host].ansible_host }} dbname=app user=app
         password=app-test-pw sslmode=require"
        -tAc "select 1"
      register: probe
      changed_when: false
      failed_when: probe.rc != 0 or probe.stdout.strip() != "1"
```

- [ ] **Step 5: Commit**

```bash
git add tests/molecule/provision/molecule/ha
git commit -m "test(provision): ha scenario verifies replication carries provisioning state"
```

---

## Task 17: extend CI matrix

**Files:**
- Modify: `.github/workflows/molecule.yml`

- [ ] **Step 1: Read the matrix entries**

Run: `sed -n '10,45p' .github/workflows/molecule.yml`
Confirm the matrix is a list of `{role, scenario}` rows ending with `vip_manager / default`.

- [ ] **Step 2: Append two rows**

Edit `.github/workflows/molecule.yml`. After the `vip_manager / default` matrix entry, add:

```yaml
          - role: provision
            scenario: default
          - role: provision
            scenario: ha
```

- [ ] **Step 3: Lint**

Run: `yamllint .github/workflows/molecule.yml`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/molecule.yml
git commit -m "ci(molecule): add provision default and ha scenarios"
```

---

## Task 18: docs — first-run + day-2 runbook

**Files:**
- Modify: `docs/operations/firstrun.md`
- Create: `docs/operations/day2-provisioning.md`

- [ ] **Step 1: Append a P3 section to firstrun.md**

Edit `docs/operations/firstrun.md`. At the end of the file (after the last numbered step), append:

```markdown

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
```

- [ ] **Step 2: Create `docs/operations/day2-provisioning.md`**

```markdown
# Day-2 provisioning

All provisioning is declarative. Edit `responses/site.rsp.yml`, then
`make deploy`. The `provision` role runs once per deploy on whichever
host is the current Patroni leader; replicas pick up the changes via
streaming replication. To target only the provisioning step, pass
`--tags provision`.

## Add a database

```yaml
postgres:
  databases:
    - { name: app,       owner: app }
    - { name: analytics, owner: analytics }   # added
```

```bash
make deploy           # full deploy
# or:
ansible-playbook playbooks/site.yml --tags provision,databases
```

## Add a user

```yaml
postgres:
  users:
    - name: app
      password: !vault |
        $ANSIBLE_VAULT;1.1;AES256
        ...
      roles: [pg_read_all_data]
    - name: analytics                     # added
      password: !vault | ...
      roles: [pg_write_all_data]
```

The vault password file must be available (`ANSIBLE_VAULT_PASSWORD_FILE`
or `--ask-vault-pass`).

## Add an extension

```yaml
postgres:
  extensions:
    - pg_stat_statements
    - { name: pgvector, db: app }   # in app db only, not postgres
```

The extension package itself must already be installed on the host. PG
extensions ship as `postgresql<ver>-contrib` (in PGDG) or as separate
RPMs. If the extension is missing at the OS level, `make deploy` fails
on the `postgresql_ext` task with a clear error from PostgreSQL.

## Open HBA to a new CIDR

```yaml
postgres:
  hba_rules:
    - { db: app, user: app, source: 10.20.40.0/24, method: scram-sha-256 }
    - { db: app, user: app, source: 10.20.41.0/24, method: scram-sha-256 } # added
```

The `provision` role rewrites `pg_hba.conf` and signals
`pg_reload_conf()`. No restart, no client disruption.

## Common gotchas

- **Extension missing at OS level**: install the RPM (`dnf install postgresql18-contrib`) and re-deploy.
- **Vault password unavailable**: `make deploy` fails at variable templating with a clear error before any task runs.
- **Hand-edited `pg_hba.conf` reverts**: that's intentional. The role owns the file.
- **Per-table grants**: out of scope. Issue them by hand or via a future migration tool; we won't manage them.
```

- [ ] **Step 3: Lint**

Run: `markdownlint docs/operations/firstrun.md docs/operations/day2-provisioning.md` (or `make lint`)
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add docs/operations/firstrun.md docs/operations/day2-provisioning.md
git commit -m "docs(ops): P3 provisioning firstrun + day-2 runbook"
```

---

## Task 19: README roadmap flip

**Files:**
- Modify: `README.md:5-7,77` (status sentence and roadmap row)

- [ ] **Step 1: Read current roadmap rows**

Run: `grep -n "P2c\|P3\|provisioning" README.md`
Confirm there is a row `| P3 | Provisioning ... | pending |` or similar.

- [ ] **Step 2: Flip status to done**

Edit `README.md`. In the roadmap table, change the P3 row's status column from `pending` to `done`. In the "Status" sentence in the file header (around lines 5-7), add P3 to the comma-separated list of completed phases.

If the exact wording differs from the assumption above, just update what's there in the same shape; do not add new sections.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs(readme): mark P3 done in roadmap"
```

---

## Task 20: end-to-end smoke (no commit)

**Files:** none

- [ ] **Step 1: Run the new role's molecule scenarios**

```bash
cd tests/molecule/provision && molecule test -s default
cd tests/molecule/provision && molecule test -s ha
```

Expected: both pass through `destroy → create → prepare → converge → idempotence → verify → destroy`. Idempotence is the key signal — a second `converge` must show 0 changed tasks.

- [ ] **Step 2: Run the unit test suite**

```bash
pytest tests/configure -v
```

Expected: all tests pass (existing + the six new ones from Task 12).

- [ ] **Step 3: Run lint**

```bash
make lint
```

Expected: clean.

No commit for this task — it's verification only.

---

## Self-review notes

1. **Spec coverage check.** Spec §3.4 lists `provision (primary only)`; addressed by Tasks 4–10 (delegation to leader). Spec §4 row "`provision` … HBA + roles + databases + extensions via `community.postgresql` modules" → Tasks 6/7/8/9. Spec §7.6 "pg_hba.conf rendered in order: system rules, monitor rule, operator rules. Fully managed — hand edits revert." → Task 6 plus Task 1 defaults defining the three layers. Spec §8.1 step 8 "`_provision` — HBA, roles, dbs, extensions on primary." → Task 13 + Task 14 wires it into site.yml after `_vip_manager.yml` (matching spec §5.1's site.yml ordering). Spec §8.7 "`_provision` SQL fails | Fail; operator inspects" → no fancy `failed_when: false`; modules raise. Spec §9.2 day-2 changes (add db, add user, change `max_connections`, etc.) → Task 18 day-2 runbook covers add db/user/ext/hba; `extra_parameters` is patroni-side and already in P2a. Spec §11 repos — no new packages required (community.postgresql is from Galaxy not RPM; psycopg3 already installed by P2a). Spec §12 "PostgreSQL provisioning → community.postgresql modules" → all four module families used in this plan.

2. **Placeholder scan.** No `TBD`, no `TODO`, no `implement later`. Task 16 step 2 explicitly repeats the prepare.yml from Task 15 verbatim per the no-placeholder rule. Task 19 step 2 acknowledges minor wording drift in README and gives a fallback ("update what's there") but the engineer has the exact strings to look for via Step 1 grep.

3. **Variable / type consistency.** `provision_leader_host` is set in Task 4 and referenced in Tasks 5, 6, 7, 8, 9, 10, 16. `provision_osdba` and `provision_osdba_socket_dir` defined in Task 1 and used everywhere consistently. `provision_extensions_in_db` defined Task 1, used Task 9. `provision_pg_hba_path` defined Task 1, used Task 6. `provision_pg_hba_system_rules` defined Task 1, consumed Task 6. `postgres_users`, `postgres_databases`, `postgres_extensions`, `postgres_hba_rules` come from `group_vars/response.yml` (already populated by `bin/_generate_response_vars.py`). `patroni_replication_user`, `patroni_superuser` defaults match the existing patroni role.

4. **Migration risk for Task 11 (Patroni HBA shrink).** Task 11 removes the bootstrap-time `hostssl all all 0.0.0.0/0 scram-sha-256` line. **For new clusters**, this is fine — `provision` runs on the same `make deploy` and adds the operator's HBA rules before any client tries to connect. **For pre-P3 clusters**, `pg_hba.conf` was already initdb'd with the wildcard; removing it from the bootstrap template does NOT touch a running cluster's pg_hba.conf, because Patroni's bootstrap block is only consulted at `initdb` time. So existing deployments are not regressed by Task 11's text change. The first `make deploy` after upgrading to P3 will rewrite pg_hba via `provision`, which will preserve operator rules (they're still in the response file).

5. **Ordering inside Task 3.** `hba` runs before `users` so a freshly-created user can immediately log in over the wire if the matching HBA rule is also new. `databases` after `users` so `owner:` references resolve. `extensions` after `databases` so the target db exists. Single reload at the end via the `Reload PostgreSQL` handler — handlers fire after all tasks. This matches PG operational best practice.

6. **What's deliberately out of scope.** Per-table grants, ALTER DEFAULT PRIVILEGES, schema migrations, tablespaces, vault end-to-end CI fixture, monitor pg_hba rule (will be filled in by P5 by setting `provision_pg_hba_monitor_rules`).

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-13-p3-provisioning.md`. Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
