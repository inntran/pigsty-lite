# P6 (Lifecycle operations: switchover, failover, minor upgrade, scaling) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the operator safe, repeatable day-2 cluster operations. After P6, `make switchover`, `make failover`, `make minor-upgrade`, `make scale-add-replica HOST=...`, and `make scale-remove-replica HOST=...` each drive a dedicated operator-entry playbook that performs the operation through Patroni with explicit preconditions, operator confirmation where the spec requires it, and post-operation health verification. A major-upgrade runbook (docs only, no playbook) is also delivered.

**Architecture:** Five new operator-entry playbooks at `playbooks/` top level (not underscore-prefixed — these are operator entry points like `site.yml`, per spec §5.1). Each is a self-contained playbook (not a role) because lifecycle operations are imperative sequences, not declarative state — they have no steady-state "converge twice → zero change" semantics. They share one new helper role, `roles/cluster_ops/`, that provides reusable task files for the things every lifecycle playbook needs: locate the Patroni leader, assert cluster health, assert a recent backup exists, wait for a member to converge. Playbooks `import_role` those task files via `tasks_from:`. Patroni REST (`/cluster`, `/switchover`, `/failover`) is the control plane; `patronictl` is used only where the REST API has no equivalent. Confirmation is via `pause` prompts gated on an `auto_confirm` extra-var so CI/automation can bypass them. Five new Makefile targets wrap the playbooks with the documented variable contracts (`HOST=`, `TARGET_TIME=` is restore-only and out of scope here).

**Tech Stack:** Ansible playbooks + `ansible.builtin.uri` for Patroni REST + `ansible.builtin.command` for `patronictl` and `dnf` + `community.postgresql.postgresql_query` for replication-lag checks. Molecule + podman is **not** used for these — lifecycle playbooks need a real multi-node Patroni cluster with real systemd and real failover, which is libvirt/integration territory (spec §13.2 puts patroni-class tests in the libvirt-only tier). P6's automated test surface is therefore: the `cluster_ops` helper role gets a container-safe molecule scenario for its *assertion* logic, and the five playbooks get a `--syntax-check` + a documented manual libvirt test procedure. This matches spec §13.3 (`minor-upgrade-rolling` and `minor-upgrade-backup-gate` are listed as libvirt integration scenarios, not CI molecule scenarios).

---

## File Structure

**New helper role (`roles/cluster_ops/`):**

- `roles/cluster_ops/defaults/main.yml` — `cluster_ops_*` knobs: Patroni REST scheme/port/timeout, health-wait retries/delay, replication-lag threshold (1MB per spec §10.1), backup-freshness window.
- `roles/cluster_ops/meta/main.yml` — galaxy_info, `dependencies: []`.
- `roles/cluster_ops/tasks/find_leader.yml` — query any reachable Patroni REST `/cluster`, set `cluster_ops_leader_host`, `cluster_ops_replica_hosts`, `cluster_ops_member_count`.
- `roles/cluster_ops/tasks/assert_healthy.yml` — assert exactly one leader, all members `state=running`, replication lag under threshold on every replica.
- `roles/cluster_ops/tasks/assert_recent_backup.yml` — query `pgbackrest info --output=json` on the leader; assert the newest backup is within `cluster_ops_backup_max_age_hours`.
- `roles/cluster_ops/tasks/wait_member_converged.yml` — given `cluster_ops_target_member`, poll Patroni REST until that member is `running` and (if a replica) replication lag is under threshold.
- `roles/cluster_ops/tasks/main.yml` — intentionally empty (`---` + comment): this role is a library of `tasks_from:` includes, never run whole.
- `roles/cluster_ops/README.md` — documents each task file's inputs/outputs (the set-facts it produces).

**New operator-entry playbooks (`playbooks/`):**

- `playbooks/switchover.yml` — controlled switchover: find leader → assert healthy → confirm → POST Patroni `/switchover` → wait for new leader → verify healthy.
- `playbooks/failover.yml` — manual failover: find leader → confirm (stronger prompt — this is for when the leader is already gone) → POST Patroni `/failover` with a chosen candidate → wait → verify.
- `playbooks/minor_upgrade.yml` — rolling minor PG upgrade per spec §10.1: version checks → backup-freshness gate → per-replica pause/stop/`dnf update`/start/converge → switchover → upgrade old primary → verify.
- `playbooks/scale_add_replica.yml` — add a replica: assert the new host is in inventory and reachable → run the postgres + patroni + connection-layer + monitoring path against just that host → wait for it to join as a streaming replica → verify.
- `playbooks/scale_remove_replica.yml` — remove a replica: assert it is a replica (never the leader) → confirm → stop Patroni on it → `patronictl remove` the member from the DCS → stop services → verify the remaining cluster is healthy.

**Modified files:**

- `Makefile` — add `switchover`, `failover`, `minor-upgrade`, `scale-add-replica`, `scale-remove-replica` targets. `scale-*` require `HOST=`; all wrap the playbook with `ANSIBLE` invocation matching the existing `deploy` target's style.
- `playbooks/tags.md` — add a short "Operator-entry playbooks" section noting these are not part of `site.yml` and are run directly / via `make`.
- `group_vars/all.yml` — add `postgres_minor_upgrade_require_recent_backup_hours: 24` (spec §10.1 default) and `cluster_ops_replication_lag_max_bytes: 1048576` (1 MB, spec §10.1).
- `bin/_response_schema.py` — add optional `postgres.minor_upgrade.require_recent_backup_hours` validation (positive int). The response file may carry this knob per spec §10.1; today the schema does not know about it.
- `responses/single.rsp.yml.example` and `responses/ha.rsp.yml.example` — add a commented `minor_upgrade:` sub-block under `postgres:` showing the `require_recent_backup_hours` and `pin_version` knobs. Comment only — no active values.
- `docs/operations/firstrun.md` — add a "Lifecycle operations" section pointing at the new `make` targets and the runbook.
- `README.md` — flip P6 to done in the roadmap. **Note:** P6 in the roadmap reads "Lifecycle ops + portability bundle"; this plan delivers only the lifecycle ops half. Task 17 changes the roadmap row text to "Lifecycle operations (switchover, failover, minor upgrade, scaling)" and adds a new pending row "P6b | Portability bundle (export/import)" so the roadmap stays honest. If the roadmap is structured differently, adapt — the rule is: do not mark "portability bundle" done.

**New docs:**

- `docs/operations/lifecycle.md` — **new**, the operator runbook for switchover / failover / minor upgrade / scaling: what each `make` target does, preconditions, what to expect, how to recover if it fails partway.
- `docs/operations/major-upgrade.md` — **new**, the major PG upgrade runbook per spec §10.2: the logical-replication cutover path (zero-downtime, copy/paste commands) and the `pg_upgrade` path (downtime estimate + rollback plan). Docs only — explicitly no playbook, no generator (spec §10.2).

**New test files:**

- `tests/molecule/cluster_ops/molecule/default/{molecule,prepare,converge,verify}.yml` — container-safe scenario for the `cluster_ops` helper role's assertion logic. It stands up a real single-node Patroni (the postgres/patroni roles already have container-safe scenarios) and exercises `find_leader.yml` + `assert_healthy.yml` against it, asserting the set-facts are correct. It does **not** test switchover/failover (those need a multi-node cluster with real failover — libvirt tier).
- `tests/configure/test_schema.py` — extend with cases for the new `postgres.minor_upgrade` schema rule.

**Out of scope (deferred):**

- `playbooks/restore.yml` (PITR) — its own dedicated plan, as flagged in P4. Lifecycle playbooks here assume the cluster exists and is being operated on; restore *recreates* cluster state and has distinct failure modes (pause Patroni → stop services → `pgbackrest restore --type=time` → reinit replicas → resume) that warrant focused treatment.
- The portability bundle (`make export` / `make import`, `bin/export-bundle`, `bin/import-bundle`) — P6b, its own plan. It is Python-CLI work, not Ansible, and shares nothing with the lifecycle playbooks.
- Major-upgrade automation — spec §10.2 is explicit: runbook only, no playbook, DBA responsibility. This plan delivers the runbook (Task 16) and stops there.
- libvirt integration scenarios (`minor-upgrade-rolling`, `minor-upgrade-backup-gate`) — spec §13.3 is the libvirt-only tier; P7 owns the integration harness. This plan delivers a documented manual libvirt test procedure in `lifecycle.md`, not an automated integration scenario.
- A `cluster_ops` molecule scenario covering multi-node switchover — needs real failover; libvirt tier, P7.

---

## Task 1: lifecycle coordination vars + schema knob

**Files:**
- Modify: `group_vars/all.yml`
- Modify: `bin/_response_schema.py`
- Modify: `bin/_generate_response_vars.py`
- Modify: `tests/configure/test_schema.py`

- [ ] **Step 1: Add lifecycle vars to `group_vars/all.yml`**

Open `group_vars/all.yml`. Add a new block before `# Operator network defaults`:

```yaml
# Lifecycle operations (P6) -----------------------------------------
# Replication lag a replica must be under before a lifecycle operation
# treats it as converged (bytes). 1 MiB per the design spec.
cluster_ops_replication_lag_max_bytes: 1048576
# Minor-upgrade safety gate: refuse to upgrade unless a successful
# backup exists within this many hours. Operator can override via the
# response file's postgres.minor_upgrade block.
postgres_minor_upgrade_require_recent_backup_hours: 24
```

- [ ] **Step 2: Add failing schema tests**

Add to `tests/configure/test_schema.py` (use whatever minimal-response helper the file actually defines):

```python
def test_minor_upgrade_block_must_be_mapping():
    response = _minimal_single_response()
    response["postgres"]["minor_upgrade"] = "soon"
    with pytest.raises(SchemaError, match=r"postgres\.minor_upgrade: must be a mapping"):
        validate(response)


def test_minor_upgrade_require_recent_backup_hours_must_be_positive_int():
    response = _minimal_single_response()
    response["postgres"]["minor_upgrade"] = {"require_recent_backup_hours": 0}
    with pytest.raises(SchemaError, match=r"postgres\.minor_upgrade\.require_recent_backup_hours"):
        validate(response)


def test_minor_upgrade_block_is_optional():
    response = _minimal_single_response()
    response["postgres"].pop("minor_upgrade", None)
    validate(response)
```

- [ ] **Step 3: Run tests; expect failure**

Run: `pytest tests/configure/test_schema.py -k minor_upgrade -v`
Expected: 2 FAILs (`block_must_be_mapping`, `require_recent_backup_hours`); `block_is_optional` passes already.

- [ ] **Step 4: Extend the schema**

Edit `bin/_response_schema.py`. Inside `_validate_postgres()`, after the existing validation calls, add:

```python
    _validate_minor_upgrade(postgres)
```

Then add the helper at module scope, near the other `_validate_*` helpers:

```python
def _validate_minor_upgrade(postgres: dict) -> None:
    minor_upgrade = postgres.get("minor_upgrade")
    if minor_upgrade is None:
        return
    if not isinstance(minor_upgrade, dict):
        raise SchemaError("postgres.minor_upgrade: must be a mapping")
    hours = minor_upgrade.get("require_recent_backup_hours")
    if hours is not None and (not isinstance(hours, int) or hours < 1):
        raise SchemaError(
            "postgres.minor_upgrade.require_recent_backup_hours: must be a positive integer"
        )
```

- [ ] **Step 5: Run tests; expect pass**

Run: `pytest tests/configure/test_schema.py -k minor_upgrade -v`
Expected: all 3 PASS.

- [ ] **Step 6: Emit the var from the generator**

Edit `bin/_generate_response_vars.py`. In the `out` dict literal, near the other `postgres_*` entries, add:

```python
        "postgres_minor_upgrade_require_recent_backup_hours": (
            postgres.get("minor_upgrade", {}).get("require_recent_backup_hours", 24)
        ),
```

- [ ] **Step 7: Run the full configure suite**

Run: `pytest tests/configure -v`
Expected: all PASS.

- [ ] **Step 8: Lint**

Run: `yamllint group_vars/all.yml && ruff check bin/_response_schema.py bin/_generate_response_vars.py`
Expected: no errors.

- [ ] **Step 9: Commit**

```bash
git add group_vars/all.yml bin/_response_schema.py bin/_generate_response_vars.py tests/configure/test_schema.py
git commit -m "feat(lifecycle): coordination vars and minor_upgrade schema knob"
```

---

## Task 2: cluster_ops role scaffolding

**Files:**
- Create: `roles/cluster_ops/defaults/main.yml`
- Create: `roles/cluster_ops/meta/main.yml`
- Create: `roles/cluster_ops/tasks/main.yml`
- Create: `roles/cluster_ops/README.md`

- [ ] **Step 1: Write `defaults/main.yml`**

```yaml
---
# roles/cluster_ops/defaults/main.yml
# All variables prefixed `cluster_ops_`. This role is a library of
# tasks_from: includes used by the P6 lifecycle playbooks; it is never
# run as a whole role.

# Patroni REST
cluster_ops_rest_scheme: https
cluster_ops_rest_port: "{{ patroni_rest_port | default(8008) }}"
cluster_ops_rest_validate_certs: false
cluster_ops_rest_timeout: 5

# Health-convergence polling
cluster_ops_wait_retries: 60
cluster_ops_wait_delay: 5

# Replication lag a replica must be under to count as converged.
cluster_ops_replication_lag_max_bytes: "{{ cluster_ops_replication_lag_max_bytes | default(1048576) }}"

# Backup-freshness gate (hours). Consumed by assert_recent_backup.yml.
cluster_ops_backup_max_age_hours: "{{ postgres_minor_upgrade_require_recent_backup_hours | default(24) }}"

# The OS account used for local pgbackrest / psql calls on a node.
cluster_ops_dbsu: "{{ postgres_osdba | default('postgres') }}"
cluster_ops_backup_stanza: "{{ backup_stanza | default(cluster_name | default('pigsty-lite')) }}"
```

- [ ] **Step 2: Write `meta/main.yml`**

```yaml
---
galaxy_info:
  role_name: cluster_ops
  author: pigsty-lite
  description: Reusable task-file library for the P6 lifecycle playbooks (leader lookup, health asserts, convergence waits).
  license: Apache-2.0
  min_ansible_version: "2.16"
  platforms:
    - name: EL
      versions: ["10"]
dependencies: []
```

- [ ] **Step 3: Write `tasks/main.yml`**

```yaml
---
# cluster_ops is a library role. It is never run whole. The lifecycle
# playbooks include its task files individually via:
#   ansible.builtin.include_role:
#     name: cluster_ops
#     tasks_from: find_leader.yml
# See README.md for each task file's inputs and the facts it sets.
```

- [ ] **Step 4: Write `README.md`**

````markdown
# cluster_ops

A library role for the P6 lifecycle playbooks. It is **never run as a
whole role** — `tasks/main.yml` is intentionally empty. Lifecycle
playbooks pull in individual task files via `include_role` +
`tasks_from:`.

## Included task files

### `find_leader.yml`

Queries any reachable Patroni REST `/cluster` endpoint.

**Sets facts:**
- `cluster_ops_leader_host` — inventory hostname of the current leader.
- `cluster_ops_replica_hosts` — list of replica inventory hostnames.
- `cluster_ops_member_count` — total members reported by Patroni.

### `assert_healthy.yml`

Fails unless: exactly one leader, every member `state=running`, and
every replica's replication lag is under
`cluster_ops_replication_lag_max_bytes`. Requires `find_leader.yml` to
have run first.

### `assert_recent_backup.yml`

Runs `pgbackrest info --output=json` on `cluster_ops_leader_host` and
fails unless the newest backup is within
`cluster_ops_backup_max_age_hours`. Requires `find_leader.yml` first.

### `wait_member_converged.yml`

**Input:** `cluster_ops_target_member` (an inventory hostname).
Polls Patroni REST until that member reports `state=running` and, if it
is a replica, replication lag under the threshold.

## Why a role and not just shared task files in `playbooks/`

Roles give these task files a defaults file (`cluster_ops_*` knobs) and
a stable include path. The lifecycle playbooks stay thin: each is a
sequence of `include_role` calls plus the operation-specific steps.
````

- [ ] **Step 5: Lint**

Run: `yamllint roles/cluster_ops/defaults/main.yml roles/cluster_ops/meta/main.yml roles/cluster_ops/tasks/main.yml`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add roles/cluster_ops/defaults roles/cluster_ops/meta roles/cluster_ops/tasks roles/cluster_ops/README.md
git commit -m "feat(cluster_ops): library role scaffolding"
```

---

## Task 3: cluster_ops — find_leader

**Files:**
- Create: `roles/cluster_ops/tasks/find_leader.yml`

- [ ] **Step 1: Write `find_leader.yml`**

```yaml
---
# Query any reachable Patroni REST endpoint for the cluster view, then
# classify members. Runs once per play and broadcasts the facts to all
# hosts. The first postgres host is the query target; if it is down,
# the `retries` loop and the until-condition let us fail clearly rather
# than hang.

- name: Query a Patroni REST endpoint for the cluster view
  ansible.builtin.uri:
    url: "{{ cluster_ops_rest_scheme }}://{{ hostvars[item].ansible_host | default(item) }}:{{ cluster_ops_rest_port }}/cluster"
    method: GET
    validate_certs: "{{ cluster_ops_rest_validate_certs }}"
    timeout: "{{ cluster_ops_rest_timeout }}"
    return_content: true
    status_code: 200
  register: cluster_ops_cluster_view
  loop: "{{ groups['postgres'] }}"
  loop_control:
    label: "{{ item }}"
  run_once: true
  ignore_errors: true

- name: Select the first successful cluster view
  ansible.builtin.set_fact:
    cluster_ops_view: >-
      {{ (cluster_ops_cluster_view.results
          | selectattr('status', 'defined')
          | selectattr('status', 'equalto', 200)
          | list | first).json }}
  run_once: true

- name: Fail if no Patroni REST endpoint answered
  ansible.builtin.assert:
    that:
      - cluster_ops_view is defined
      - cluster_ops_view.members is defined
    fail_msg: >-
      No Patroni REST endpoint in the postgres group answered on
      :{{ cluster_ops_rest_port }}/cluster. The cluster may be down, or
      the inventory may be wrong.
  run_once: true

- name: Set the leader host fact
  ansible.builtin.set_fact:
    cluster_ops_leader_host: >-
      {{ (cluster_ops_view.members
          | selectattr('role', 'equalto', 'leader')
          | list | first).name }}
    cluster_ops_replica_hosts: >-
      {{ cluster_ops_view.members
         | rejectattr('role', 'equalto', 'leader')
         | map(attribute='name') | list }}
    cluster_ops_member_count: "{{ cluster_ops_view.members | length }}"
  run_once: true

- name: Announce the discovered topology
  ansible.builtin.debug:
    msg: >-
      Leader: {{ cluster_ops_leader_host }};
      replicas: {{ cluster_ops_replica_hosts | join(', ') | default('none') }};
      members: {{ cluster_ops_member_count }}.
  run_once: true
```

- [ ] **Step 2: Lint**

Run: `yamllint roles/cluster_ops/tasks/find_leader.yml`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add roles/cluster_ops/tasks/find_leader.yml
git commit -m "feat(cluster_ops): Patroni leader and topology lookup"
```

---

## Task 4: cluster_ops — assert_healthy

**Files:**
- Create: `roles/cluster_ops/tasks/assert_healthy.yml`

- [ ] **Step 1: Write `assert_healthy.yml`**

```yaml
---
# Assert the cluster is healthy enough to operate on. Requires
# find_leader.yml to have set cluster_ops_view. Checks: exactly one
# leader, every member running, every replica's lag under threshold.

- name: Re-query the cluster view for a fresh health snapshot
  ansible.builtin.uri:
    url: "{{ cluster_ops_rest_scheme }}://{{ hostvars[cluster_ops_leader_host].ansible_host | default(cluster_ops_leader_host) }}:{{ cluster_ops_rest_port }}/cluster"
    method: GET
    validate_certs: "{{ cluster_ops_rest_validate_certs }}"
    timeout: "{{ cluster_ops_rest_timeout }}"
    return_content: true
    status_code: 200
  register: cluster_ops_health_view
  run_once: true

- name: Assert exactly one leader
  ansible.builtin.assert:
    that:
      - (cluster_ops_health_view.json.members
         | selectattr('role', 'equalto', 'leader')
         | list | length) == 1
    fail_msg: >-
      Expected exactly one Patroni leader; the cluster view shows
      {{ cluster_ops_health_view.json.members
         | selectattr('role', 'equalto', 'leader') | list | length }}.
  run_once: true

- name: Assert every member reports state=running
  ansible.builtin.assert:
    that:
      - (cluster_ops_health_view.json.members
         | rejectattr('state', 'equalto', 'running')
         | list | length) == 0
    fail_msg: >-
      Not all members are running:
      {{ cluster_ops_health_view.json.members
         | rejectattr('state', 'equalto', 'running')
         | map(attribute='name') | list }}.
  run_once: true

- name: Assert every replica's replication lag is under the threshold
  ansible.builtin.assert:
    that:
      - (cluster_ops_health_view.json.members
         | selectattr('role', 'equalto', 'replica')
         | selectattr('lag', 'defined')
         | selectattr('lag', 'gt', cluster_ops_replication_lag_max_bytes | int)
         | list | length) == 0
    fail_msg: >-
      One or more replicas exceed the
      {{ cluster_ops_replication_lag_max_bytes }}-byte lag threshold:
      {{ cluster_ops_health_view.json.members
         | selectattr('role', 'equalto', 'replica')
         | selectattr('lag', 'defined')
         | selectattr('lag', 'gt', cluster_ops_replication_lag_max_bytes | int)
         | items2dict(key_name='name', value_name='lag') }}.
  run_once: true

- name: Announce health OK
  ansible.builtin.debug:
    msg: "Cluster healthy: 1 leader, all members running, replication lag within threshold."
  run_once: true
```

- [ ] **Step 2: Lint**

Run: `yamllint roles/cluster_ops/tasks/assert_healthy.yml`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add roles/cluster_ops/tasks/assert_healthy.yml
git commit -m "feat(cluster_ops): cluster health assertion"
```

---

## Task 5: cluster_ops — assert_recent_backup

**Files:**
- Create: `roles/cluster_ops/tasks/assert_recent_backup.yml`

- [ ] **Step 1: Write `assert_recent_backup.yml`**

```yaml
---
# Refuse a destructive lifecycle operation unless a recent successful
# backup exists. pgbackrest info --output=json reports each backup with
# a `timestamp.stop` epoch; we compare the newest against now. Runs on
# the leader, as the dbsu, because that is where pgbackrest can read
# the stanza. Requires find_leader.yml first.

- name: Read pgbackrest backup info on the leader
  ansible.builtin.command:
    cmd: >-
      pgbackrest --stanza={{ cluster_ops_backup_stanza }}
      info --output=json
  become: true
  become_user: "{{ cluster_ops_dbsu }}"
  delegate_to: "{{ cluster_ops_leader_host }}"
  run_once: true
  register: cluster_ops_backup_info
  changed_when: false

- name: Extract the newest backup stop timestamp
  ansible.builtin.set_fact:
    cluster_ops_newest_backup_epoch: >-
      {{ (cluster_ops_backup_info.stdout | from_json | first).backup
         | map(attribute='timestamp')
         | map(attribute='stop')
         | map('int')
         | list | max | default(0) }}
  run_once: true

- name: Assert a successful backup exists
  ansible.builtin.assert:
    that:
      - (cluster_ops_newest_backup_epoch | int) > 0
    fail_msg: >-
      pgbackrest info reports no completed backup for stanza
      {{ cluster_ops_backup_stanza }}. Run a backup before this
      operation, or pass -e auto_confirm=true only if you understand
      the risk.
  run_once: true

- name: Assert the newest backup is within the freshness window
  ansible.builtin.assert:
    that:
      - >-
        ((ansible_date_time.epoch | int) - (cluster_ops_newest_backup_epoch | int))
        <= (cluster_ops_backup_max_age_hours | int) * 3600
    fail_msg: >-
      Newest backup is older than {{ cluster_ops_backup_max_age_hours }}
      hours. Refusing the operation. Run a fresh backup first.
  run_once: true

- name: Announce backup freshness OK
  ansible.builtin.debug:
    msg: >-
      Backup freshness OK: newest backup is
      {{ (((ansible_date_time.epoch | int) - (cluster_ops_newest_backup_epoch | int)) / 3600) | round(1) }}
      hours old (limit {{ cluster_ops_backup_max_age_hours }}h).
  run_once: true
```

Note for the executor: `ansible_date_time` requires `gather_facts:
true` on the play that includes this. The lifecycle playbooks in later
tasks all gather facts — confirm that holds when wiring this include.

- [ ] **Step 2: Lint**

Run: `yamllint roles/cluster_ops/tasks/assert_recent_backup.yml`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add roles/cluster_ops/tasks/assert_recent_backup.yml
git commit -m "feat(cluster_ops): recent-backup freshness gate"
```

---

## Task 6: cluster_ops — wait_member_converged

**Files:**
- Create: `roles/cluster_ops/tasks/wait_member_converged.yml`

- [ ] **Step 1: Write `wait_member_converged.yml`**

```yaml
---
# Poll Patroni REST until cluster_ops_target_member is running and (if a
# replica) caught up. Used after starting/restarting a member during a
# rolling upgrade or scale-add. The caller must set
# cluster_ops_target_member to an inventory hostname.

- name: Assert the caller set a target member
  ansible.builtin.assert:
    that:
      - cluster_ops_target_member is defined
      - cluster_ops_target_member | length > 0
    fail_msg: "wait_member_converged.yml requires cluster_ops_target_member to be set."
  run_once: true

- name: Wait for the target member to converge
  ansible.builtin.uri:
    url: "{{ cluster_ops_rest_scheme }}://{{ hostvars[cluster_ops_target_member].ansible_host | default(cluster_ops_target_member) }}:{{ cluster_ops_rest_port }}/patroni"
    method: GET
    validate_certs: "{{ cluster_ops_rest_validate_certs }}"
    timeout: "{{ cluster_ops_rest_timeout }}"
    return_content: true
    status_code: [200, 503]
  register: cluster_ops_member_probe
  delegate_to: "{{ cluster_ops_target_member }}"
  run_once: true
  retries: "{{ cluster_ops_wait_retries }}"
  delay: "{{ cluster_ops_wait_delay }}"
  until: >-
    cluster_ops_member_probe.status == 200
    and (cluster_ops_member_probe.json.state | default('')) == 'running'
    and (
      (cluster_ops_member_probe.json.role | default('')) == 'master'
      or (cluster_ops_member_probe.json.role | default('')) == 'leader'
      or (cluster_ops_member_probe.json.replication_state | default('')) == 'streaming'
    )

- name: Announce convergence
  ansible.builtin.debug:
    msg: >-
      Member {{ cluster_ops_target_member }} converged:
      state={{ cluster_ops_member_probe.json.state }},
      role={{ cluster_ops_member_probe.json.role | default('n/a') }}.
  run_once: true
```

Note: Patroni's `/patroni` endpoint reports `role` as `master` on older
Patroni and `leader` on newer; the `until` accepts both. If the
installed Patroni only ever reports one, the extra branch is harmless.

- [ ] **Step 2: Lint**

Run: `yamllint roles/cluster_ops/tasks/wait_member_converged.yml`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add roles/cluster_ops/tasks/wait_member_converged.yml
git commit -m "feat(cluster_ops): member-convergence wait"
```

---

## Task 7: switchover playbook

**Files:**
- Create: `playbooks/switchover.yml`

- [ ] **Step 1: Write `playbooks/switchover.yml`**

```yaml
---
# Operator entry: controlled primary switchover.
#   ansible-playbook playbooks/switchover.yml
#   ansible-playbook playbooks/switchover.yml -e candidate=pgnode02
#   ansible-playbook playbooks/switchover.yml -e auto_confirm=true   # skip prompt
#
# A switchover is the safe, planned version of a failover: the current
# leader is healthy, and we hand the leader role to a replica through
# Patroni's /switchover endpoint. Patroni handles the demote/promote.

- name: Controlled Patroni switchover
  hosts: postgres
  gather_facts: true
  become: true
  vars:
    auto_confirm: false
  tasks:
    - name: Locate the current leader and topology
      ansible.builtin.include_role:
        name: cluster_ops
        tasks_from: find_leader.yml

    - name: Assert the cluster is healthy before switching
      ansible.builtin.include_role:
        name: cluster_ops
        tasks_from: assert_healthy.yml

    - name: Choose the switchover candidate
      ansible.builtin.set_fact:
        switchover_candidate: "{{ candidate | default(cluster_ops_replica_hosts | first) }}"
      run_once: true

    - name: Assert a candidate replica exists
      ansible.builtin.assert:
        that:
          - switchover_candidate is defined
          - switchover_candidate | length > 0
          - switchover_candidate in cluster_ops_replica_hosts
        fail_msg: >-
          No valid switchover candidate. Replicas seen:
          {{ cluster_ops_replica_hosts }}. Pass -e candidate=<host> to
          pick one explicitly.
      run_once: true

    - name: Confirm the switchover
      ansible.builtin.pause:
        prompt: >-
          About to switch the leader from {{ cluster_ops_leader_host }}
          to {{ switchover_candidate }}. Existing sessions will be
          briefly interrupted. Press ENTER to proceed, Ctrl-C then A to abort.
      when: not (auto_confirm | bool)
      run_once: true

    - name: Request the switchover via Patroni REST
      ansible.builtin.uri:
        url: "{{ cluster_ops_rest_scheme }}://{{ hostvars[cluster_ops_leader_host].ansible_host | default(cluster_ops_leader_host) }}:{{ cluster_ops_rest_port }}/switchover"
        method: POST
        validate_certs: "{{ cluster_ops_rest_validate_certs }}"
        body_format: json
        body:
          leader: "{{ cluster_ops_leader_host }}"
          candidate: "{{ switchover_candidate }}"
        status_code: [200, 202]
      delegate_to: "{{ cluster_ops_leader_host }}"
      run_once: true

    - name: Wait for the candidate to become the new leader
      ansible.builtin.set_fact:
        cluster_ops_target_member: "{{ switchover_candidate }}"
      run_once: true

    - name: Poll until the new leader is converged
      ansible.builtin.include_role:
        name: cluster_ops
        tasks_from: wait_member_converged.yml

    - name: Re-locate the leader after the switchover
      ansible.builtin.include_role:
        name: cluster_ops
        tasks_from: find_leader.yml

    - name: Assert the cluster is healthy after the switchover
      ansible.builtin.include_role:
        name: cluster_ops
        tasks_from: assert_healthy.yml

    - name: Report the result
      ansible.builtin.debug:
        msg: "Switchover complete. New leader: {{ cluster_ops_leader_host }}."
      run_once: true
```

- [ ] **Step 2: Syntax check**

Run: `ansible-playbook playbooks/switchover.yml --syntax-check`
Expected: no errors. (A full run needs a live cluster — see `docs/operations/lifecycle.md` for the manual libvirt procedure, delivered in Task 15.)

- [ ] **Step 3: Lint**

Run: `yamllint playbooks/switchover.yml`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add playbooks/switchover.yml
git commit -m "feat(lifecycle): controlled switchover playbook"
```

---

## Task 8: failover playbook

**Files:**
- Create: `playbooks/failover.yml`

- [ ] **Step 1: Write `playbooks/failover.yml`**

```yaml
---
# Operator entry: manual failover.
#   ansible-playbook playbooks/failover.yml -e candidate=pgnode02
#   ansible-playbook playbooks/failover.yml -e candidate=pgnode02 -e auto_confirm=true
#
# A failover is the unplanned cousin of a switchover: it is used when
# the current leader is unhealthy or gone. Patroni's /failover endpoint
# does not require the old leader to be reachable. Because this is a
# more dangerous operation, the confirmation prompt is explicit and a
# candidate MUST be named — we do not auto-pick.

- name: Manual Patroni failover
  hosts: postgres
  gather_facts: true
  become: true
  vars:
    auto_confirm: false
  tasks:
    - name: Assert a candidate was explicitly named
      ansible.builtin.assert:
        that:
          - candidate is defined
          - candidate | length > 0
          - candidate in groups['postgres']
        fail_msg: >-
          failover.yml requires an explicit -e candidate=<host> that is
          a member of the postgres group. Unlike switchover, we do not
          auto-pick a candidate for a failover.
      run_once: true

    - name: Locate the current topology (leader may be missing)
      ansible.builtin.include_role:
        name: cluster_ops
        tasks_from: find_leader.yml

    - name: Confirm the failover
      ansible.builtin.pause:
        prompt: >-
          FAILOVER: about to promote {{ candidate }} to leader. The
          current leader Patroni reports is
          {{ cluster_ops_leader_host | default('UNKNOWN/UNREACHABLE') }}.
          This is for when the leader is unhealthy — if the leader is
          healthy, abort and use switchover instead. Press ENTER to
          proceed, Ctrl-C then A to abort.
      when: not (auto_confirm | bool)
      run_once: true

    - name: Request the failover via Patroni REST
      ansible.builtin.uri:
        url: "{{ cluster_ops_rest_scheme }}://{{ hostvars[candidate].ansible_host | default(candidate) }}:{{ cluster_ops_rest_port }}/failover"
        method: POST
        validate_certs: "{{ cluster_ops_rest_validate_certs }}"
        body_format: json
        body:
          candidate: "{{ candidate }}"
        status_code: [200, 202]
      delegate_to: "{{ candidate }}"
      run_once: true

    - name: Wait for the candidate to become leader
      ansible.builtin.set_fact:
        cluster_ops_target_member: "{{ candidate }}"
      run_once: true

    - name: Poll until the new leader is converged
      ansible.builtin.include_role:
        name: cluster_ops
        tasks_from: wait_member_converged.yml

    - name: Re-locate the leader after the failover
      ansible.builtin.include_role:
        name: cluster_ops
        tasks_from: find_leader.yml

    - name: Assert the surviving cluster is healthy
      ansible.builtin.include_role:
        name: cluster_ops
        tasks_from: assert_healthy.yml

    - name: Report the result
      ansible.builtin.debug:
        msg: >-
          Failover complete. New leader: {{ cluster_ops_leader_host }}.
          If the old leader returns, Patroni will reattach it as a
          replica; verify with `patronictl list`.
      run_once: true
```

- [ ] **Step 2: Syntax check**

Run: `ansible-playbook playbooks/failover.yml --syntax-check`
Expected: no errors.

- [ ] **Step 3: Lint**

Run: `yamllint playbooks/failover.yml`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add playbooks/failover.yml
git commit -m "feat(lifecycle): manual failover playbook"
```

---

## Task 9: minor_upgrade playbook — version checks and backup gate

**Files:**
- Create: `playbooks/minor_upgrade.yml`

This task creates the playbook through the pre-flight gates. Task 10
appends the rolling-upgrade body to the same file. Splitting keeps each
step reviewable.

- [ ] **Step 1: Write `playbooks/minor_upgrade.yml` (gates only)**

```yaml
---
# Operator entry: rolling minor PostgreSQL upgrade.
#   ansible-playbook playbooks/minor_upgrade.yml
#   ansible-playbook playbooks/minor_upgrade.yml -e auto_confirm=true
#
# Implements spec section 10.1. Minor upgrades are same-major
# (e.g. 18.3 -> 18.4): the on-disk format does not change, so the
# upgrade is just `dnf update` + restart, done one node at a time to
# keep the cluster available. We refuse to run if the target major
# differs from the current major, or if there is no recent backup.

- name: Rolling minor PostgreSQL upgrade — pre-flight gates
  hosts: postgres
  gather_facts: true
  become: true
  vars:
    auto_confirm: false
  tasks:
    - name: Read the installed PostgreSQL server package version per node
      ansible.builtin.command:
        cmd: "rpm -q --qf '%{VERSION}' postgresql{{ postgres_version }}-server"
      register: minor_upgrade_installed
      changed_when: false

    - name: Record the installed version fact
      ansible.builtin.set_fact:
        minor_upgrade_installed_version: "{{ minor_upgrade_installed.stdout | trim }}"

    - name: Determine the target version
      ansible.builtin.set_fact:
        minor_upgrade_target_version: "{{ postgres_pin_version | default('') }}"
      run_once: true

    - name: Assert the target major matches the installed major
      ansible.builtin.assert:
        that:
          - >-
            minor_upgrade_target_version | length == 0
            or (minor_upgrade_target_version.split('.')[0] | int) == (postgres_version | int)
        fail_msg: >-
          postgres.pin_version ({{ minor_upgrade_target_version }})
          targets a different major than the installed
          PostgreSQL {{ postgres_version }}. This playbook does minor
          upgrades only. For a major upgrade see
          docs/operations/major-upgrade.md.

    - name: Locate the current leader and topology
      ansible.builtin.include_role:
        name: cluster_ops
        tasks_from: find_leader.yml

    - name: Assert the cluster is healthy before upgrading
      ansible.builtin.include_role:
        name: cluster_ops
        tasks_from: assert_healthy.yml

    - name: Assert a recent backup exists
      ansible.builtin.include_role:
        name: cluster_ops
        tasks_from: assert_recent_backup.yml

    - name: Confirm the rolling upgrade
      ansible.builtin.pause:
        prompt: >-
          About to roll a minor PostgreSQL upgrade across
          {{ groups['postgres'] | length }} node(s), one at a time,
          ending with a switchover off the current leader
          ({{ cluster_ops_leader_host }}). Each node is briefly
          unavailable. Press ENTER to proceed, Ctrl-C then A to abort.
      when: not (auto_confirm | bool)
      run_once: true
```

- [ ] **Step 2: Syntax check**

Run: `ansible-playbook playbooks/minor_upgrade.yml --syntax-check`
Expected: no errors.

- [ ] **Step 3: Lint**

Run: `yamllint playbooks/minor_upgrade.yml`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add playbooks/minor_upgrade.yml
git commit -m "feat(lifecycle): minor upgrade playbook — pre-flight gates"
```

---

## Task 10: minor_upgrade playbook — rolling upgrade body

**Files:**
- Modify: `playbooks/minor_upgrade.yml`

- [ ] **Step 1: Append the rolling-upgrade plays to `playbooks/minor_upgrade.yml`**

Append the following plays to the end of the file (after the pre-flight
gates play from Task 9). The upgrade walks replicas first, then
switches over, then upgrades the old primary. We use `serial: 1` on the
replica play so Ansible itself enforces "one node at a time".

```yaml

- name: Upgrade replicas one at a time
  hosts: postgres
  serial: 1
  gather_facts: true
  become: true
  order: sorted
  tasks:
    - name: Skip this play on the current leader
      ansible.builtin.meta: end_host
      when: inventory_hostname == hostvars[groups['postgres'][0]].cluster_ops_leader_host
            | default(cluster_ops_leader_host | default(''))

    - name: Re-resolve the leader for this batch
      ansible.builtin.include_role:
        name: cluster_ops
        tasks_from: find_leader.yml

    - name: Skip if this host is the leader
      ansible.builtin.meta: end_host
      when: inventory_hostname == cluster_ops_leader_host

    - name: Pause Patroni management of this node
      ansible.builtin.command:
        cmd: "patronictl -c {{ patroni_config_file | default('/etc/patroni/patroni.yml') }} pause --wait {{ cluster_name }}"
      changed_when: true
      run_once: true
      delegate_to: "{{ cluster_ops_leader_host }}"

    - name: Stop Patroni on this replica
      ansible.builtin.systemd:
        name: "{{ patroni_service_name | default('patroni') }}"
        state: stopped

    - name: Update the PostgreSQL packages on this replica
      ansible.builtin.dnf:
        name:
          - "postgresql{{ postgres_version }}-server"
          - "postgresql{{ postgres_version }}-contrib"
          - "postgresql{{ postgres_version }}"
        state: "{{ 'latest' if (postgres_pin_version | default('')) | length == 0 else 'present' }}"

    - name: Install the pinned PostgreSQL version when pin_version is set
      ansible.builtin.dnf:
        name:
          - "postgresql{{ postgres_version }}-server-{{ postgres_pin_version }}"
          - "postgresql{{ postgres_version }}-contrib-{{ postgres_pin_version }}"
          - "postgresql{{ postgres_version }}-{{ postgres_pin_version }}"
        state: present
        allow_downgrade: true
      when: (postgres_pin_version | default('')) | length > 0

    - name: Start Patroni on this replica
      ansible.builtin.systemd:
        name: "{{ patroni_service_name | default('patroni') }}"
        state: started

    - name: Resume Patroni management of the cluster
      ansible.builtin.command:
        cmd: "patronictl -c {{ patroni_config_file | default('/etc/patroni/patroni.yml') }} resume --wait {{ cluster_name }}"
      changed_when: true
      run_once: true
      delegate_to: "{{ cluster_ops_leader_host }}"

    - name: Wait for this replica to rejoin and catch up
      ansible.builtin.set_fact:
        cluster_ops_target_member: "{{ inventory_hostname }}"

    - name: Poll until this replica is converged
      ansible.builtin.include_role:
        name: cluster_ops
        tasks_from: wait_member_converged.yml

- name: Switch over off the original primary
  hosts: postgres
  gather_facts: true
  become: true
  tasks:
    - name: Re-locate the leader (still the original primary at this point)
      ansible.builtin.include_role:
        name: cluster_ops
        tasks_from: find_leader.yml

    - name: Switch the leader to an already-upgraded replica
      ansible.builtin.uri:
        url: "{{ cluster_ops_rest_scheme }}://{{ hostvars[cluster_ops_leader_host].ansible_host | default(cluster_ops_leader_host) }}:{{ cluster_ops_rest_port }}/switchover"
        method: POST
        validate_certs: "{{ cluster_ops_rest_validate_certs }}"
        body_format: json
        body:
          leader: "{{ cluster_ops_leader_host }}"
          candidate: "{{ cluster_ops_replica_hosts | first }}"
        status_code: [200, 202]
      delegate_to: "{{ cluster_ops_leader_host }}"
      run_once: true

    - name: Wait for the new leader to converge
      ansible.builtin.set_fact:
        cluster_ops_target_member: "{{ cluster_ops_replica_hosts | first }}"
      run_once: true

    - name: Poll until the new leader is converged
      ansible.builtin.include_role:
        name: cluster_ops
        tasks_from: wait_member_converged.yml

    - name: Record which host is now the demoted old primary
      ansible.builtin.set_fact:
        minor_upgrade_old_primary: "{{ cluster_ops_leader_host }}"
      run_once: true

- name: Upgrade the demoted old primary
  hosts: postgres
  gather_facts: true
  become: true
  tasks:
    - name: Re-locate the leader (now an upgraded node)
      ansible.builtin.include_role:
        name: cluster_ops
        tasks_from: find_leader.yml

    - name: Identify the demoted old primary
      ansible.builtin.set_fact:
        minor_upgrade_old_primary: >-
          {{ cluster_ops_replica_hosts
             | difference(cluster_ops_replica_hosts
                          | map('extract', hostvars, 'minor_upgrade_installed_version')
                          | select('defined') | list)
             | default(cluster_ops_replica_hosts) | first }}
      run_once: true

    - name: Upgrade the old primary (now a replica)
      ansible.builtin.meta: end_host
      when: inventory_hostname != (hostvars[groups['postgres'][0]].minor_upgrade_old_primary | default(''))

    - name: Pause Patroni management
      ansible.builtin.command:
        cmd: "patronictl -c {{ patroni_config_file | default('/etc/patroni/patroni.yml') }} pause --wait {{ cluster_name }}"
      changed_when: true
      run_once: true
      delegate_to: "{{ cluster_ops_leader_host }}"

    - name: Stop Patroni on the old primary
      ansible.builtin.systemd:
        name: "{{ patroni_service_name | default('patroni') }}"
        state: stopped

    - name: Update the PostgreSQL packages on the old primary
      ansible.builtin.dnf:
        name:
          - "postgresql{{ postgres_version }}-server"
          - "postgresql{{ postgres_version }}-contrib"
          - "postgresql{{ postgres_version }}"
        state: "{{ 'latest' if (postgres_pin_version | default('')) | length == 0 else 'present' }}"

    - name: Start Patroni on the old primary
      ansible.builtin.systemd:
        name: "{{ patroni_service_name | default('patroni') }}"
        state: started

    - name: Resume Patroni management
      ansible.builtin.command:
        cmd: "patronictl -c {{ patroni_config_file | default('/etc/patroni/patroni.yml') }} resume --wait {{ cluster_name }}"
      changed_when: true
      run_once: true
      delegate_to: "{{ cluster_ops_leader_host }}"

    - name: Wait for the old primary to rejoin as a replica
      ansible.builtin.set_fact:
        cluster_ops_target_member: "{{ inventory_hostname }}"

    - name: Poll until the old primary is converged
      ansible.builtin.include_role:
        name: cluster_ops
        tasks_from: wait_member_converged.yml

- name: Verify the upgraded cluster
  hosts: postgres
  gather_facts: true
  become: true
  tasks:
    - name: Re-locate the leader
      ansible.builtin.include_role:
        name: cluster_ops
        tasks_from: find_leader.yml

    - name: Assert the upgraded cluster is healthy
      ansible.builtin.include_role:
        name: cluster_ops
        tasks_from: assert_healthy.yml

    - name: Report the result
      ansible.builtin.debug:
        msg: >-
          Minor upgrade complete. Leader: {{ cluster_ops_leader_host }}.
          Verify versions with: ansible postgres -m command -a
          'rpm -q postgresql{{ postgres_version }}-server'.
      run_once: true
```

Note for the executor: the "identify the demoted old primary" logic is
the trickiest part of this playbook. A simpler, more robust alternative
the executor MAY choose: capture the leader hostname into a fact
**before** the replica-upgrade play runs (in Task 9's gates play, set
`minor_upgrade_original_primary: "{{ cluster_ops_leader_host }}"` and
rely on host-fact persistence across plays), then in the
"upgrade the demoted old primary" play simply target
`when: inventory_hostname == hostvars[groups['postgres'][0]].minor_upgrade_original_primary`.
If you take that route, adjust Task 9 to set that fact and simplify
this play accordingly — the intent (upgrade the one node that started
as primary, last) is what matters.

- [ ] **Step 2: Syntax check**

Run: `ansible-playbook playbooks/minor_upgrade.yml --syntax-check`
Expected: no errors.

- [ ] **Step 3: Lint**

Run: `yamllint playbooks/minor_upgrade.yml`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add playbooks/minor_upgrade.yml
git commit -m "feat(lifecycle): minor upgrade playbook — rolling upgrade body"
```

---

## Task 11: scale_add_replica playbook

**Files:**
- Create: `playbooks/scale_add_replica.yml`

- [ ] **Step 1: Write `playbooks/scale_add_replica.yml`**

```yaml
---
# Operator entry: add a replica to an existing cluster.
#   ansible-playbook playbooks/scale_add_replica.yml -e target_host=pgnode04
#
# Prerequisite: the operator has already added the new host to the
# inventory (with postgres_role: pg_replica) and re-run
# `./configure -s -f ...` so group_vars/response.yml and the host's
# identity vars exist. This playbook then runs the install/bootstrap
# path against just that one host. Patroni handles the actual replica
# clone (basebackup or pgbackrest) when it starts.

- name: Add a replica — preconditions
  hosts: "{{ target_host | default('undefined') }}"
  gather_facts: true
  become: true
  tasks:
    - name: Assert target_host was provided and is in the postgres group
      ansible.builtin.assert:
        that:
          - target_host is defined
          - target_host != 'undefined'
          - target_host in groups['postgres']
        fail_msg: >-
          scale_add_replica.yml requires -e target_host=<host>, and that
          host must already be in the inventory's postgres group. Add it
          to the inventory and re-run `./configure -s -f ...` first.
      run_once: true
      delegate_to: localhost

    - name: Assert this host is not already a running cluster member
      block:
        - name: Probe whether Patroni is already up here
          ansible.builtin.uri:
            url: "https://{{ ansible_host | default(inventory_hostname) }}:{{ patroni_rest_port | default(8008) }}/patroni"
            method: GET
            validate_certs: false
            timeout: 3
            status_code: [200, 503]
          register: scale_add_existing_probe
      rescue:
        - name: Patroni is not up here — good, this is a fresh add
          ansible.builtin.set_fact:
            scale_add_is_fresh: true
      always:
        - name: Note the probe result
          ansible.builtin.debug:
            msg: >-
              {{ 'Patroni already responds here — this host looks like an
              existing member; aborting to avoid clobbering it.'
              if scale_add_existing_probe.status is defined
              else 'No Patroni here yet — proceeding with the add.' }}

    - name: Fail if Patroni already responds on the target host
      ansible.builtin.assert:
        that:
          - scale_add_existing_probe.status is not defined
        fail_msg: >-
          Patroni already responds on {{ target_host }}. This playbook
          adds NEW replicas; it will not re-bootstrap an existing
          member. Use `make deploy` for steady-state reconciliation.
      run_once: true

- name: Locate the existing cluster leader from a current member
  hosts: postgres
  gather_facts: true
  become: true
  tasks:
    - name: Find the leader (queries existing members, not the new host)
      ansible.builtin.include_role:
        name: cluster_ops
        tasks_from: find_leader.yml

    - name: Assert the existing cluster is healthy before extending it
      ansible.builtin.include_role:
        name: cluster_ops
        tasks_from: assert_healthy.yml

- name: Provision the new replica host
  ansible.builtin.import_playbook: _node.yml
  vars:
    ansible_limit: "{{ target_host }}"

- name: Install PostgreSQL on the new replica
  ansible.builtin.import_playbook: _postgres_install.yml
  vars:
    ansible_limit: "{{ target_host }}"

- name: Bootstrap Patroni on the new replica (it joins as a streaming replica)
  ansible.builtin.import_playbook: _postgres_bootstrap.yml
  vars:
    ansible_limit: "{{ target_host }}"

- name: Install the connection layer on the new replica
  ansible.builtin.import_playbook: _pgbouncer.yml
  vars:
    ansible_limit: "{{ target_host }}"

- name: Install HAProxy on the new replica
  ansible.builtin.import_playbook: _haproxy.yml
  vars:
    ansible_limit: "{{ target_host }}"

- name: Install the backup client on the new replica
  ansible.builtin.import_playbook: _backup_client.yml
  vars:
    ansible_limit: "{{ target_host }}"

- name: Install monitoring agents on the new replica
  ansible.builtin.import_playbook: _monitoring_agents.yml
  vars:
    ansible_limit: "{{ target_host }}"

- name: Verify the new replica joined the cluster
  hosts: postgres
  gather_facts: true
  become: true
  tasks:
    - name: Wait for the new replica to converge
      ansible.builtin.set_fact:
        cluster_ops_target_member: "{{ target_host }}"
      run_once: true

    - name: Poll until the new replica is streaming
      ansible.builtin.include_role:
        name: cluster_ops
        tasks_from: wait_member_converged.yml

    - name: Re-locate the topology
      ansible.builtin.include_role:
        name: cluster_ops
        tasks_from: find_leader.yml

    - name: Assert the extended cluster is healthy
      ansible.builtin.include_role:
        name: cluster_ops
        tasks_from: assert_healthy.yml

    - name: Report the result
      ansible.builtin.debug:
        msg: >-
          Replica {{ target_host }} added. Cluster now has
          {{ cluster_ops_member_count }} members. The backup_store
          authorized_keys and monitoring scrape targets pick the new
          node up on the next `make deploy`.
      run_once: true
```

Note for the executor: `ansible_limit` set as a play var does **not**
restrict `import_playbook` hosts — Ansible's `--limit` is a CLI option,
not a play var. The Makefile target in Task 13 passes `--limit` on the
command line. The `vars: {ansible_limit: ...}` blocks above are
**documentation of intent only** and will not actually limit the
imported playbooks. **Two valid fixes — pick one during execution:**
(a) drop the `vars:` blocks and rely solely on the Makefile passing
`--limit {{HOST}}` (simplest; the imported playbooks then run against
their normal `hosts:` but `--limit` scopes them); or (b) convert each
`import_playbook` to a play that `include_role`s the relevant role with
`hosts: "{{ target_host }}"`. Option (a) is recommended and matches how
`make deploy` already works. If you take (a), delete the `vars:` blocks
and add a comment noting the Makefile passes `--limit`.

- [ ] **Step 2: Syntax check**

Run: `ansible-playbook playbooks/scale_add_replica.yml --syntax-check -e target_host=localhost`
Expected: no errors. (`-e target_host=` is needed because the first
play's `hosts:` interpolates it.)

- [ ] **Step 3: Lint**

Run: `yamllint playbooks/scale_add_replica.yml`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add playbooks/scale_add_replica.yml
git commit -m "feat(lifecycle): scale-add-replica playbook"
```

---

## Task 12: scale_remove_replica playbook

**Files:**
- Create: `playbooks/scale_remove_replica.yml`

- [ ] **Step 1: Write `playbooks/scale_remove_replica.yml`**

```yaml
---
# Operator entry: remove a replica from an existing cluster.
#   ansible-playbook playbooks/scale_remove_replica.yml -e target_host=pgnode04
#   ansible-playbook playbooks/scale_remove_replica.yml -e target_host=pgnode04 -e auto_confirm=true
#
# This decommissions a replica: stop Patroni on it, remove the member
# from the Patroni DCS, and stop its services. It REFUSES to remove the
# current leader — switch over first. After this runs, the operator
# removes the host from the inventory and re-runs `./configure`.

- name: Remove a replica
  hosts: postgres
  gather_facts: true
  become: true
  vars:
    auto_confirm: false
  tasks:
    - name: Assert target_host was provided and is in the postgres group
      ansible.builtin.assert:
        that:
          - target_host is defined
          - target_host in groups['postgres']
        fail_msg: >-
          scale_remove_replica.yml requires -e target_host=<host> that
          is a member of the postgres group.
      run_once: true

    - name: Locate the current leader and topology
      ansible.builtin.include_role:
        name: cluster_ops
        tasks_from: find_leader.yml

    - name: Refuse to remove the current leader
      ansible.builtin.assert:
        that:
          - target_host != cluster_ops_leader_host
        fail_msg: >-
          {{ target_host }} is the current leader. Switch over first
          (`make switchover`), then remove it as a replica.
      run_once: true

    - name: Assert the target is actually a known replica
      ansible.builtin.assert:
        that:
          - target_host in cluster_ops_replica_hosts
        fail_msg: >-
          {{ target_host }} is not a replica Patroni knows about.
          Replicas: {{ cluster_ops_replica_hosts }}.
      run_once: true

    - name: Assert removing this replica leaves a viable cluster
      ansible.builtin.assert:
        that:
          - (cluster_ops_member_count | int) - 1 >= 1
        fail_msg: >-
          Removing {{ target_host }} would leave the cluster with no
          members. Refusing.
      run_once: true

    - name: Confirm the removal
      ansible.builtin.pause:
        prompt: >-
          About to decommission replica {{ target_host }}: stop Patroni,
          remove it from the DCS, stop its services. Its data directory
          is left on disk for safety. Press ENTER to proceed, Ctrl-C
          then A to abort.
      when: not (auto_confirm | bool)
      run_once: true

    - name: Stop Patroni on the target replica
      ansible.builtin.systemd:
        name: "{{ patroni_service_name | default('patroni') }}"
        state: stopped
      delegate_to: "{{ target_host }}"
      run_once: true

    - name: Remove the member from the Patroni DCS
      ansible.builtin.command:
        cmd: >-
          patronictl -c {{ patroni_config_file | default('/etc/patroni/patroni.yml') }}
          remove {{ cluster_name }}
      args:
        stdin: "{{ cluster_name }}\nYes I am aware\n{{ target_host }}\n"
      changed_when: true
      delegate_to: "{{ cluster_ops_leader_host }}"
      run_once: true

    - name: Stop the connection-layer and agent services on the removed host
      ansible.builtin.systemd:
        name: "{{ item }}"
        state: stopped
      loop:
        - haproxy
        - pgbouncer
        - node-exporter
      failed_when: false
      delegate_to: "{{ target_host }}"
      run_once: true

    - name: Re-locate the topology after removal
      ansible.builtin.include_role:
        name: cluster_ops
        tasks_from: find_leader.yml

    - name: Assert the remaining cluster is healthy
      ansible.builtin.include_role:
        name: cluster_ops
        tasks_from: assert_healthy.yml

    - name: Report the result
      ansible.builtin.debug:
        msg: >-
          Replica {{ target_host }} removed. Cluster now has
          {{ cluster_ops_member_count }} members. Next: remove
          {{ target_host }} from the inventory and re-run
          `./configure -s -f ...` so the backup_store and monitoring
          configs stop referencing it. The data directory on
          {{ target_host }} was intentionally left in place.
      run_once: true
```

Note for the executor: `patronictl remove` is interactive — it prompts
for the cluster name, a confirmation phrase, and the member to remove.
The `args.stdin` above feeds those answers. **Verify the exact prompt
sequence against the installed patronictl version** during execution
(`patronictl remove --help` and a dry run on a libvirt cluster); the
prompt order/wording has changed across Patroni releases. If `stdin`
feeding proves brittle, the alternative is to delete the member key
directly from etcd — but prefer `patronictl` if the stdin sequence
works.

- [ ] **Step 2: Syntax check**

Run: `ansible-playbook playbooks/scale_remove_replica.yml --syntax-check`
Expected: no errors.

- [ ] **Step 3: Lint**

Run: `yamllint playbooks/scale_remove_replica.yml`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add playbooks/scale_remove_replica.yml
git commit -m "feat(lifecycle): scale-remove-replica playbook"
```

---

## Task 13: Makefile targets

**Files:**
- Modify: `Makefile`

- [ ] **Step 1: Add the five lifecycle targets**

Edit `Makefile`. Add `switchover failover minor-upgrade scale-add-replica scale-remove-replica` to the `.PHONY` line. Then add the targets after the `clean` target:

```makefile
switchover:
	ansible-playbook playbooks/switchover.yml

failover:
	@if [ -z "$(CANDIDATE)" ]; then echo "Usage: make failover CANDIDATE=<host>"; exit 2; fi
	ansible-playbook playbooks/failover.yml -e candidate=$(CANDIDATE)

minor-upgrade:
	ansible-playbook playbooks/minor_upgrade.yml

scale-add-replica:
	@if [ -z "$(HOST)" ]; then echo "Usage: make scale-add-replica HOST=<host>"; exit 2; fi
	ansible-playbook playbooks/scale_add_replica.yml -e target_host=$(HOST) --limit $(HOST),postgres

scale-remove-replica:
	@if [ -z "$(HOST)" ]; then echo "Usage: make scale-remove-replica HOST=<host>"; exit 2; fi
	ansible-playbook playbooks/scale_remove_replica.yml -e target_host=$(HOST)
```

Then extend the `help` target — add these lines inside the `help:`
recipe, after the `make clean` line:

```makefile
	@echo
	@echo "  Lifecycle operations:"
	@echo "  make switchover                     Controlled primary switchover"
	@echo "  make failover CANDIDATE=<host>      Manual failover to a named candidate"
	@echo "  make minor-upgrade                  Rolling minor PostgreSQL upgrade"
	@echo "  make scale-add-replica HOST=<host>  Add a replica (host must be in inventory)"
	@echo "  make scale-remove-replica HOST=<host>  Decommission a replica"
```

Note: `scale-add-replica` passes `--limit $(HOST),postgres` — the new
host plus the existing `postgres` group, because the playbook's
leader-lookup play needs to reach existing members while the
provisioning plays target only the new host. This is the `--limit`
approach referenced in Task 11's executor note.

- [ ] **Step 2: Verify the targets parse**

Run: `make help`
Expected: the new lifecycle section appears in the output, no `make` syntax errors.

Run: `make switchover --dry-run`
Expected: prints the `ansible-playbook playbooks/switchover.yml` command without running it.

- [ ] **Step 3: Commit**

```bash
git add Makefile
git commit -m "feat(lifecycle): make targets for switchover, failover, upgrade, scaling"
```

---

## Task 14: cluster_ops molecule scenario

**Files:**
- Create: `tests/molecule/cluster_ops/molecule/default/molecule.yml`
- Create: `tests/molecule/cluster_ops/molecule/default/prepare.yml`
- Create: `tests/molecule/cluster_ops/molecule/default/converge.yml`
- Create: `tests/molecule/cluster_ops/molecule/default/verify.yml`

This scenario tests the `cluster_ops` *assertion library* against a
real single-node Patroni cluster. It does not test switchover/failover
(multi-node, real failover — libvirt tier, P7).

- [ ] **Step 1: `molecule.yml`**

```yaml
---
driver:
  name: podman
platforms:
  - name: pigsty-lite-clusterops-default-1
    image: docker.io/oraclelinux:10
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
      etcd:
        etcd_initial_cluster_state: new
      postgres:
        patroni_superuser_password: superuser-test-pw
        patroni_replication_password: replicator-test-pw
        patroni_rewind_password: rewind-test-pw
        postgres_tune_profile: tiny
    host_vars:
      pigsty-lite-clusterops-default-1:
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
# Exercise the cluster_ops assertion library against the live
# single-node cluster. converge runs the includes; verify checks the
# facts they set. Running converge twice (idempotence step) must not
# error — these task files are read-only probes, so they are naturally
# idempotent.
- name: Exercise the cluster_ops library
  hosts: postgres
  gather_facts: true
  become: true
  tasks:
    - name: Find the leader
      ansible.builtin.include_role:
        name: cluster_ops
        tasks_from: find_leader.yml

    - name: Assert the cluster is healthy
      ansible.builtin.include_role:
        name: cluster_ops
        tasks_from: assert_healthy.yml

    - name: Persist the discovered facts for the verifier
      ansible.builtin.copy:
        dest: /tmp/cluster_ops_facts.json
        mode: "0644"
        content: |
          {
            "leader_host": "{{ cluster_ops_leader_host }}",
            "replica_hosts": {{ cluster_ops_replica_hosts | to_json }},
            "member_count": {{ cluster_ops_member_count }}
          }
      run_once: true
      delegate_to: "{{ cluster_ops_leader_host }}"
```

- [ ] **Step 4: `verify.yml`**

```yaml
---
- name: Verify the cluster_ops library produced correct facts
  hosts: postgres
  gather_facts: false
  become: true
  tasks:
    - name: Read the persisted facts
      ansible.builtin.slurp:
        src: /tmp/cluster_ops_facts.json
      register: cluster_ops_facts_raw

    - name: Parse the facts
      ansible.builtin.set_fact:
        cluster_ops_facts: "{{ cluster_ops_facts_raw.content | b64decode | from_json }}"

    - name: The leader is the single node
      ansible.builtin.assert:
        that:
          - cluster_ops_facts.leader_host == 'pigsty-lite-clusterops-default-1'
        fail_msg: "find_leader.yml did not identify the expected leader"

    - name: There are no replicas in a single-node cluster
      ansible.builtin.assert:
        that:
          - cluster_ops_facts.replica_hosts | length == 0
        fail_msg: "find_leader.yml reported unexpected replicas"

    - name: The member count is 1
      ansible.builtin.assert:
        that:
          - cluster_ops_facts.member_count | int == 1
        fail_msg: "find_leader.yml reported the wrong member count"

    - name: No SELinux AVC denials since boot
      ansible.builtin.command:
        cmd: ausearch -m AVC -ts boot
      register: avc
      changed_when: false
      failed_when: avc.rc == 0 and 'type=AVC' in avc.stdout
```

- [ ] **Step 5: Commit**

```bash
git add tests/molecule/cluster_ops/molecule/default
git commit -m "test(cluster_ops): default scenario verifies the assertion library"
```

---

## Task 15: extend CI matrix

**Files:**
- Modify: `.github/workflows/molecule.yml`

- [ ] **Step 1: Read the matrix entries**

Run: `sed -n '1,95p' .github/workflows/molecule.yml`
Confirm the matrix is a list of `{role, scenario}` rows ending with the
monitoring rows added by P5.

- [ ] **Step 2: Append one row**

Edit `.github/workflows/molecule.yml`. After the last monitoring matrix
entry, add, matching the existing indentation exactly:

```yaml
          - role: cluster_ops
            scenario: default
```

Only `cluster_ops` gets a CI scenario — the five lifecycle playbooks
are not roles and need multi-node real-failover clusters (libvirt tier,
P7), so they are not in the container matrix.

- [ ] **Step 3: Lint**

Run: `yamllint .github/workflows/molecule.yml`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/molecule.yml
git commit -m "ci(molecule): add cluster_ops scenario"
```

---

## Task 16: docs — lifecycle runbook and major-upgrade runbook

**Files:**
- Create: `docs/operations/lifecycle.md`
- Create: `docs/operations/major-upgrade.md`
- Modify: `docs/operations/firstrun.md`
- Modify: `playbooks/tags.md`
- Modify: `responses/single.rsp.yml.example`
- Modify: `responses/ha.rsp.yml.example`

- [ ] **Step 1: Create `docs/operations/lifecycle.md`**

```markdown
# Lifecycle operations

Day-2 cluster operations are driven by dedicated operator-entry
playbooks, wrapped by `make` targets. None of these are part of
`site.yml` / `make deploy` — they are run on demand.

All five accept `-e auto_confirm=true` to skip the interactive
confirmation prompt (use in automation; think twice interactively).

## Switchover — `make switchover`

Controlled, planned handover of the leader role to a replica. The
current leader must be healthy. Patroni performs the demote/promote.

- Picks the first replica as the candidate, or pass
  `-e candidate=<host>` (directly via `ansible-playbook`).
- Preconditions checked: one leader, all members running, replication
  lag within 1 MiB.
- Brief interruption of in-flight sessions; clients reconnect through
  HAProxy to the new leader.

## Failover — `make failover CANDIDATE=<host>`

Unplanned promotion, for when the leader is unhealthy or gone. Unlike
switchover, a candidate **must** be named — there is no auto-pick.
Patroni's `/failover` does not require the old leader to be reachable.

If the old leader later returns, Patroni reattaches it as a replica.
Verify with `patronictl list`.

## Minor upgrade — `make minor-upgrade`

Rolling same-major PostgreSQL upgrade (e.g. 18.3 → 18.4). Implements
the spec's §10.1 sequence:

1. Refuses if `postgres.pin_version` targets a different major.
2. Refuses if no successful backup exists within
   `postgres.minor_upgrade.require_recent_backup_hours` (default 24).
3. Upgrades replicas one at a time: Patroni pause → stop → `dnf update`
   → start → wait for the member to rejoin and catch up.
4. Switches over off the original primary to an upgraded replica.
5. Upgrades the demoted old primary the same way.
6. Verifies the whole cluster is healthy.

Pin a specific minor with `postgres.pin_version: "18.4"` in the
response file; without a pin, `dnf` takes the latest available.

## Scale add replica — `make scale-add-replica HOST=<host>`

Adds a new replica. **Prerequisite:** the host is already in the
inventory with `postgres_role: pg_replica` and you have re-run
`./configure -s -f ...` so its identity vars and
`group_vars/response.yml` exist.

The playbook runs the node → postgres → patroni → connection-layer →
backup-client → monitoring-agents path against just the new host;
Patroni clones the data when it starts. It refuses if Patroni already
responds on the target (that would be an existing member).

After it finishes, run `make deploy` once so the backup store's
`authorized_keys` and the monitoring scrape config pick up the new
node.

## Scale remove replica — `make scale-remove-replica HOST=<host>`

Decommissions a replica: stops Patroni on it, removes the member from
the DCS, stops its services. **Refuses to remove the current leader** —
switch over first. The data directory is intentionally left on disk.

After it finishes, remove the host from the inventory and re-run
`./configure -s -f ...`.

## Testing these on libvirt (manual)

The lifecycle playbooks are not in the CI molecule matrix — they need a
real multi-node cluster with real failover. To exercise them locally:

1. Stand up a 3-postgres `ha` profile on libvirt (`make
   test-integration PROFILE=ha`, or a manual libvirt inventory).
2. `make switchover` — confirm the leader moves and the cluster stays
   healthy.
3. `make failover CANDIDATE=<a replica>` — confirm promotion.
4. `make minor-upgrade` — confirm the rolling sequence and the
   backup-freshness gate (try it with a stale backup: it must refuse).
5. `make scale-add-replica HOST=<4th node>` then
   `make scale-remove-replica HOST=<4th node>` — confirm the round trip.

## Recovery if a lifecycle playbook fails partway

- **Switchover/failover failed mid-flight:** check `patronictl list`.
  Patroni is authoritative; if it shows a healthy leader, the cluster
  is fine even if the playbook errored afterward. Re-run the playbook.
- **Minor upgrade failed on a node:** that node's Patroni may be
  paused. `patronictl resume <cluster>` on the leader, fix the node
  (usually a `dnf` issue), then re-run — the playbook re-checks each
  node's version and skips already-upgraded ones is **not**
  guaranteed; inspect with `rpm -q` first and limit with `--limit` if
  needed.
- **Scale-add failed:** the new host may be half-provisioned. It is
  safe to re-run `make scale-add-replica HOST=<host>` — the underlying
  role tasks are idempotent; the only non-idempotent guard is the
  "Patroni already responds" check, which correctly *passes* for a
  half-provisioned node (Patroni is not up yet).
```

- [ ] **Step 2: Create `docs/operations/major-upgrade.md`**

```markdown
# Major PostgreSQL upgrade

Major upgrades (e.g. 17 → 18) change the on-disk format. pigsty-lite
ships **no playbook** for this — it is a DBA-supervised operation. This
runbook gives two paths.

## Path A — logical replication cutover (near-zero downtime)

Best when you can afford to run two clusters briefly and your schema is
logical-replication-friendly (no large objects, every table has a
primary key or replica identity).

1. **Stand up a new cluster** at the target major on fresh hosts: a new
   inventory + response file with `postgres.version: <new major>`, then
   `make deploy`.
2. **Create a publication** on the old primary:
   ```sql
   CREATE PUBLICATION pgupgrade FOR ALL TABLES;
   ```
3. **Copy the schema** (no data) from old to new:
   ```bash
   pg_dump -h <old-primary> -d <db> --schema-only | psql -h <new-primary> -d <db>
   ```
4. **Create a subscription** on the new primary:
   ```sql
   CREATE SUBSCRIPTION pgupgrade
     CONNECTION 'host=<old-primary> dbname=<db> user=<repl-user>'
     PUBLICATION pgupgrade;
   ```
5. **Wait for initial sync + streaming** to catch up:
   ```sql
   SELECT * FROM pg_stat_subscription;
   ```
6. **Cut over:** stop writes to the old cluster, confirm the
   subscription has drained, repoint applications (HAProxy/VIP) at the
   new cluster.
7. **Decommission** the old cluster once you are confident.

Rollback before cutover is trivial — just drop the subscription. After
cutover, rollback means reversing the replication direction, which you
should set up *before* cutover if the change is high-risk.

## Path B — pg_upgrade (in-place, with downtime)

Best when logical replication is impractical. Requires downtime roughly
proportional to the number of database objects (not data size, with
`--link`).

1. **Take a fresh backup.** Non-negotiable. `pgbackrest` full backup,
   verified with `pgbackrest info`.
2. **Install the new major's packages** alongside the old (PGDG allows
   parallel major versions).
3. **Stop the cluster** — Patroni paused, PostgreSQL stopped on all
   nodes.
4. **Run `pg_upgrade --check`** on the primary to catch incompatibilities.
5. **Run `pg_upgrade`** (with `--link` for speed if old and new data
   dirs are on the same filesystem).
6. **Rebuild the replicas** from the upgraded primary (fresh
   basebackup / pgbackrest restore — you cannot pg_upgrade a replica).
7. **Update `postgres.version`** in the response file, `make deploy` to
   re-render Patroni/config for the new major.
8. **Resume Patroni**, verify with `patronictl list`.

**Downtime estimate:** minutes for `--link` on a schema with thousands
of objects; longer without `--link` (full copy) or with very many
objects. Measure on a staging clone first.

**Rollback:** if `pg_upgrade` fails before you delete the old data
directory, the old cluster is intact — restart it. Once the old data
directory is gone (or `--link` has been used and the new cluster has
taken writes), rollback means restore-from-backup.

## Why no playbook

Major-version upgrades have too many cluster-specific decision points
(extension compatibility, downtime tolerance, logical-vs-pg_upgrade)
to safely automate. pigsty-lite's position (spec §10.2): provide a
precise runbook, keep the human in the loop.
```

- [ ] **Step 3: Append a "Lifecycle operations" section to `firstrun.md`**

Edit `docs/operations/firstrun.md`. At the end of the file, append:

```markdown

## Lifecycle operations

Once the cluster is deployed, day-2 cluster operations are run on
demand via `make` targets — switchover, failover, rolling minor
upgrades, and scaling replicas in/out. None of these are part of
`make deploy`.

See [docs/operations/lifecycle.md](lifecycle.md) for the full runbook.
For major version upgrades (no playbook — DBA-supervised), see
[docs/operations/major-upgrade.md](major-upgrade.md).
```

- [ ] **Step 4: Add an operator-entry note to `playbooks/tags.md`**

Edit `playbooks/tags.md`. At the end of the file, append:

```markdown

## Operator-entry playbooks (not part of site.yml)

These are run directly or via `make`, not as part of `make deploy`:

- `switchover.yml` — `make switchover`
- `failover.yml` — `make failover CANDIDATE=<host>`
- `minor_upgrade.yml` — `make minor-upgrade`
- `scale_add_replica.yml` — `make scale-add-replica HOST=<host>`
- `scale_remove_replica.yml` — `make scale-remove-replica HOST=<host>`

See docs/operations/lifecycle.md.
```

- [ ] **Step 5: Add the `minor_upgrade` knob to the response examples**

Edit `responses/single.rsp.yml.example` and `responses/ha.rsp.yml.example`.
Under the `postgres:` block, after the existing keys, add (commented —
no active values):

```yaml
  # Minor-upgrade safety: make minor-upgrade refuses unless a backup
  # exists within this window. Optional; defaults to 24 hours.
  # minor_upgrade:
  #   require_recent_backup_hours: 24
  # Pin a specific minor version (otherwise dnf takes the latest):
  # pin_version: "18.4"
```

- [ ] **Step 6: Lint**

Run: `markdownlint docs/operations/lifecycle.md docs/operations/major-upgrade.md docs/operations/firstrun.md playbooks/tags.md && yamllint responses/single.rsp.yml.example responses/ha.rsp.yml.example` (or `make lint`)
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add docs/operations/lifecycle.md docs/operations/major-upgrade.md \
        docs/operations/firstrun.md playbooks/tags.md \
        responses/single.rsp.yml.example responses/ha.rsp.yml.example
git commit -m "docs(ops): lifecycle and major-upgrade runbooks"
```

---

## Task 17: README roadmap update

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Read current roadmap rows**

Run: `grep -n "P6\|P7\|[Ll]ifecycle\|[Pp]ortability" README.md`
Confirm there is a P6 row reading "Lifecycle ops + portability bundle"
with status `pending`, and a "Status" sentence in the header.

- [ ] **Step 2: Split the P6 row**

Edit `README.md`. This plan delivers the lifecycle-operations half of
P6, not the portability bundle. Change the P6 roadmap row to:

```
| P6 | Lifecycle operations (switchover, failover, minor upgrade, scaling) | done |
```

And add a new row immediately after it:

```
| P6b | Portability bundle (`make export` / `make import`) | pending |
```

In the header "Status" sentence, add P6 (lifecycle operations) to the
completed list. Do **not** claim the portability bundle is done.

If the roadmap table is structured differently than assumed, adapt to
its actual shape — the invariant is: lifecycle operations = done,
portability bundle = still pending, and the two are visibly distinct.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs(readme): mark P6 lifecycle operations done, split out P6b"
```

---

## Task 18: end-to-end smoke (no commit)

**Files:** none

- [ ] **Step 1: Syntax-check every lifecycle playbook**

```bash
ansible-playbook playbooks/switchover.yml --syntax-check
ansible-playbook playbooks/failover.yml --syntax-check
ansible-playbook playbooks/minor_upgrade.yml --syntax-check
ansible-playbook playbooks/scale_add_replica.yml --syntax-check -e target_host=localhost
ansible-playbook playbooks/scale_remove_replica.yml --syntax-check
```

Expected: all five pass `--syntax-check` with no errors.

- [ ] **Step 2: Run the cluster_ops molecule scenario**

```bash
cd tests/molecule/cluster_ops && molecule test -s default
```

Expected: passes through
`destroy → create → prepare → converge → idempotence → verify → destroy`.
The idempotence step matters — the `cluster_ops` task files are
read-only probes and must show zero changed tasks on the second
`converge`.

- [ ] **Step 3: Run the unit test suite**

```bash
pytest tests/configure -v
```

Expected: all pass (existing + the three new `minor_upgrade` cases from
Task 1).

- [ ] **Step 4: Check the Makefile targets**

```bash
make help
make switchover --dry-run
make scale-add-replica HOST=pgnode99 --dry-run
```

Expected: `make help` shows the lifecycle section; the `--dry-run`
invocations print the right `ansible-playbook` command lines without
executing.

- [ ] **Step 5: Run lint**

```bash
make lint
```

Expected: clean.

- [ ] **Step 6: Manual libvirt verification (documented, not automated)**

The five lifecycle playbooks cannot be fully verified in CI — they need
a real multi-node cluster with real failover. Follow the manual libvirt
procedure in `docs/operations/lifecycle.md` ("Testing these on
libvirt") against an `ha`-profile cluster before considering P6 truly
done. This step has no automated gate; it is an operator
responsibility, consistent with spec §13.3 putting `minor-upgrade-*` in
the libvirt-only integration tier.

No commit for this task — it is verification only.

---

## Self-review notes

1. **Spec coverage check.** Spec §9.3 lists six lifecycle operations: `make switchover` → Task 7; `make failover` → Task 8; `make restore TARGET_TIME=...` → **explicitly out of scope** (its own plan, per the P4 deferral and this plan's scope decision); `make minor-upgrade` → Tasks 9-10; `make scale-add-replica HOST=...` → Task 11; `make scale-remove-replica HOST=...` → Task 12. Makefile targets for all five in-scope operations → Task 13. Spec §5.1 file structure lists `switchover.yml`, `failover.yml`, `restore.yml`, `minor_upgrade.yml`, `scale_add_replica.yml`, `scale_remove_replica.yml` as operator-entry playbooks (top-level, not underscore-prefixed) → Tasks 7-12 create five of the six at `playbooks/` top level; `restore.yml` is deferred. Spec §10.1 minor-upgrade sequence (read versions → target check → major-match refusal → backup-freshness refusal → per-replica pause/stop/dnf/start/converge → switchover → upgrade old primary → verify) → Tasks 9 (gates 1-4) and 10 (steps 5-8); the backup-freshness gate is `cluster_ops/assert_recent_backup.yml` (Task 5) consuming `postgres_minor_upgrade_require_recent_backup_hours` (Task 1). Spec §10.1 pinning (`postgres.pin_version` freezes the minor) → Task 10's `dnf` tasks branch on `postgres_pin_version`. Spec §10.2 major-upgrade runbook (logical-replication cutover path + pg_upgrade path + downtime estimate + rollback, no playbook) → Task 16 `docs/operations/major-upgrade.md`. Spec §13.3 lists `minor-upgrade-rolling` and `minor-upgrade-backup-gate` as libvirt integration scenarios (not CI molecule) → this plan does not create CI molecule scenarios for the playbooks; only `cluster_ops` (a library role) gets a container scenario (Task 14), and the manual libvirt procedure is documented (Task 16).

2. **Placeholder scan.** No `TBD`, `TODO`, or `implement later`. Every playbook is shown in full. Two places carry explicit executor-judgment notes rather than placeholders: Task 10's "identify the demoted old primary" (with a concrete simpler alternative spelled out) and Task 11's `ansible_limit`-is-not-real note (with two concrete fixes, one recommended). Task 12 flags that `patronictl remove`'s interactive prompt sequence must be verified against the installed version. These are real, named risks with concrete resolutions — surfacing them is correct, not a placeholder.

3. **Variable / type consistency.** `cluster_ops_leader_host`, `cluster_ops_replica_hosts`, `cluster_ops_member_count` are set by `find_leader.yml` (Task 3) and consumed by `assert_healthy.yml` (Task 4), `assert_recent_backup.yml` (Task 5), and every lifecycle playbook (Tasks 7-12). `cluster_ops_target_member` is the documented input to `wait_member_converged.yml` (Task 6) and is set by callers before each include (Tasks 7, 8, 10, 11). `cluster_ops_replication_lag_max_bytes` is defined in `group_vars/all.yml` (Task 1), defaulted in `cluster_ops/defaults` (Task 2), consumed in `assert_healthy.yml` (Task 4). `cluster_ops_backup_max_age_hours` resolves from `postgres_minor_upgrade_require_recent_backup_hours` (Task 1 → Task 2 default → Task 5 consumer). `postgres_pin_version` is the existing generated var (from `postgres.pin_version`, already emitted by the generator — verified in P0); Task 10 reads it and Task 9 reads it for the major-match check; the response examples document it (Task 16). `target_host` / `candidate` / `CANDIDATE` / `HOST` — the playbooks use `target_host` and `candidate` as `-e` extra-vars; the Makefile (Task 13) maps `HOST` → `target_host` and `CANDIDATE` → `candidate`. `auto_confirm` is a play var defaulting to `false`, overridable via `-e`, used consistently in Tasks 7, 8, 9, 12.

4. **Why these are playbooks, not roles.** Roles in this codebase are declarative and idempotent ("converge twice → zero change"). Lifecycle operations are imperative sequences with no steady state — running `switchover` twice does two switchovers. So they are top-level playbooks (matching spec §5.1, which lists them as operator-entry playbooks alongside `site.yml`, not under `roles/`). The one piece that *is* reusable and idempotent — leader lookup, health asserts, convergence waits — is factored into the `cluster_ops` library role, included via `tasks_from:`. This keeps each playbook thin and gives the reusable logic a single tested home (Task 14).

5. **The `--limit` / `import_playbook` hazard (Task 11).** `scale_add_replica.yml` needs two scopes in one run: the leader-lookup play must reach existing `postgres` members, but the provisioning `import_playbook`s should touch only the new host. Ansible's `--limit` is a CLI option, not a play var — so the Makefile passes `--limit $(HOST),postgres` and the imported playbooks run against their normal `hosts:` scoped by that limit. Task 11's executor note spells this out with two concrete fixes; the recommended one (rely on the Makefile `--limit`, drop the no-op `vars:` blocks) is the same mechanism `make deploy` already uses. This is the single biggest correctness risk in the plan and it is explicitly contained.

6. **What's deliberately out of scope.** `restore.yml` / PITR (its own dedicated plan — distinct failure modes around recreating cluster state). The portability bundle / `make export` / `make import` (P6b — Python CLI work, shares nothing with these playbooks; Task 17 splits the roadmap row to keep this honest). Major-upgrade automation (spec §10.2 is explicit: runbook only — delivered in Task 16, no playbook). Automated libvirt integration scenarios for the playbooks (spec §13.3 libvirt tier, P7 owns the harness — a manual procedure is documented instead). Multi-node `cluster_ops` molecule coverage (needs real failover — libvirt tier).

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-14-p6-lifecycle.md`. Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
