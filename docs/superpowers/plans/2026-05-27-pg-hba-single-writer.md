# pg_hba.conf Single-Writer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Patroni the sole writer of `pg_hba.conf`, fed by HBA rule lists in `group_vars/postgres.yml`, so every node sees identical content; fix the unsatisfiable `method: cert` replicator rule as a side effect.

**Architecture:** Move HBA rule data from `roles/provision/defaults/main.yml` into `inventory/group_vars/postgres.yml` as `postgres_hba_system_rules` and `postgres_hba_monitor_rules` (operator-supplied `postgres_hba_rules` already lives in `group_vars/all/response.yml`). The `roles/patroni` template renders the full composed list under `postgresql.pg_hba` (applied every reconcile, ~10s, with non-disruptive `pg_reload_conf()`) and a minimal `bootstrap.pg_hba` for initdb. Delete `roles/provision/tasks/_hba.yml` and its references. The replicator `hostssl` rule's method flips from `cert` (unsatisfiable — no replicator-CN cert is issued anywhere) to `scram-sha-256`, matching what Patroni's authentication block already sends.

**Tech Stack:** Ansible, Jinja2, Patroni, PostgreSQL, Molecule (with podman driver), community.postgresql collection. Spec: `docs/superpowers/specs/2026-05-27-pg-hba-single-writer-design.md`.

---

## File Structure

**Files modified:**

- `inventory/group_vars/postgres.yml` — gains `postgres_hba_system_rules` and `postgres_hba_monitor_rules`.
- `roles/patroni/templates/patroni.yml.j2` — adds an HBA-line macro, shrinks `bootstrap.pg_hba` to the minimal set, replaces the gated `postgresql.pg_hba` block with an ungated composed list over the three rule layers.
- `roles/patroni/defaults/main.yml` — removes the `patroni_pg_hba_managed` knob.
- `roles/provision/tasks/main.yml` — removes the `Manage pg_hba.conf` import block.
- `roles/provision/defaults/main.yml` — removes `provision_pg_hba_managed`, `provision_pg_hba_path`, `provision_pg_hba_system_rules`, `provision_pg_hba_monitor_rules`.
- `tests/molecule/provision/molecule/default/verify.yml` — keeps the operator-rule grep (Patroni now writes it) but verifies via `patronictl`/postgres rather than a stale provision-write expectation; no structural change needed, the existing grep already passes against Patroni-rendered output.
- `tests/molecule/provision/molecule/ha/verify.yml` — adds a per-node byte-equality assertion on `pg_hba.conf`.

**Files deleted:**

- `roles/provision/tasks/_hba.yml`

**Files not touched (deliberately):**

- `roles/provision/handlers/main.yml` — `Reload PostgreSQL` is still notified by user/extension tasks. HBA changes no longer notify it; Patroni reloads PG itself.
- `playbooks/rotate_passwords.yml` — orthogonal; already shipped.
- `docs/superpowers/plans/2026-05-13-p3-provisioning.md` — historical plan, not edited.

---

## Task 1: Add the rule lists to `group_vars/postgres.yml`

**Files:**

- Modify: `inventory/group_vars/postgres.yml`

- [ ] **Step 1: Append the two new lists**

Add to the end of `inventory/group_vars/postgres.yml`:

```yaml

# pg_hba rules — Patroni renders these into pg_hba.conf via patroni.yml's
# postgresql.pg_hba block. Three layers: system (foundational), monitor
# (filled by monitoring role; empty by default), and operator
# (postgres_hba_rules, sourced from the response file). All three are
# concatenated by roles/patroni/templates/patroni.yml.j2 in the order
# below.
#
# Foundational rules: required for Patroni and PG to function as a
# cluster. Operators normally do not edit this.
postgres_hba_system_rules:
  - { contype: local,   databases: all,         users: all,                                                                                method: peer }
  - { contype: host,    databases: all,         users: postgres,                                                  source_addr: 127.0.0.1/32, method: trust }
  - { contype: host,    databases: replication, users: "{{ patroni_replication_user | default('replicator') }}",  source_addr: 127.0.0.1/32, method: trust }
  - { contype: hostssl, databases: replication, users: "{{ patroni_replication_user | default('replicator') }}",  source_addr: 0.0.0.0/0,    method: scram-sha-256 }
  - { contype: host,    databases: postgres,    users: "{{ patroni_rewind_user | default('rewind_user') }}",      source_addr: 127.0.0.1/32, method: trust }
  - { contype: hostssl, databases: postgres,    users: "{{ patroni_rewind_user | default('rewind_user') }}",      source_addr: 0.0.0.0/0,    method: scram-sha-256 }

# Monitor rules: populated by the monitoring role or overridden in
# host_vars; defaults to empty.
postgres_hba_monitor_rules: []
```

Note: the replicator `hostssl` rule uses `scram-sha-256`, not `cert`. The
old `cert` value was unsatisfiable because `roles/certs` never issues a
CN=`replicator` certificate.

- [ ] **Step 2: Lint the file**

Run: `yamllint inventory/group_vars/postgres.yml`
Expected: no output (exit 0).

- [ ] **Step 3: Commit**

```bash
git add inventory/group_vars/postgres.yml
git commit -m "feat(group_vars): add postgres_hba_system/monitor_rules"
```

---

## Task 2: Render the composed HBA list in `patroni.yml.j2`

**Files:**

- Modify: `roles/patroni/templates/patroni.yml.j2`

- [ ] **Step 1: Add the rendering macro at the top of the template**

Insert immediately after line 3 (the existing two-line comment header) and before `scope:`:

```jinja
{%- macro render_hba_line(r) -%}
{%-   set contype = r.contype | default('hostssl') -%}
{%-   set db      = r.databases if r.databases is defined else r.db -%}
{%-   set usr     = r.users     if r.users     is defined else r.user -%}
{%-   set addr    = r.source_addr if r.source_addr is defined else (r.source | default('')) -%}
{%-   set method  = r.method | default('scram-sha-256') -%}
{{ contype }} {{ db }} {{ usr }}{% if contype != 'local' %} {{ addr }}{% endif %} {{ method }}
{%- endmacro -%}
```

The macro accepts both rule shapes in use: `provision`-style
(`databases/users/source_addr`) and operator response-file style
(`db/user/source`). `local` lines deliberately omit the address column.

- [ ] **Step 2: Shrink `bootstrap.pg_hba` to the minimal set**

Replace lines 65-71 (the current `bootstrap.pg_hba` block):

```yaml
  pg_hba:
    - local all all peer
    - host all postgres 127.0.0.1/32 trust
    - host replication {{ patroni_replication_user }} 127.0.0.1/32 trust
    - hostssl replication {{ patroni_replication_user }} 0.0.0.0/0 scram-sha-256
    - host postgres {{ patroni_rewind_user }} 127.0.0.1/32 trust
    - hostssl postgres {{ patroni_rewind_user }} 0.0.0.0/0 scram-sha-256
```

with:

```yaml
  # Minimal HBA used only at initdb. The full composed list lives in
  # postgresql.pg_hba below and replaces this within one reconcile loop
  # (~10s after Patroni starts).
  pg_hba:
    - local all all peer
    - host all postgres 127.0.0.1/32 trust
    - host replication {{ patroni_replication_user }} 127.0.0.1/32 trust
    - host postgres {{ patroni_rewind_user }} 127.0.0.1/32 trust
```

- [ ] **Step 3: Replace the gated `postgresql.pg_hba` block with the composed list**

Replace lines 78-86 (the `{% if patroni_pg_hba_managed | bool %} ... {% endif %}` block) with:

```jinja
  pg_hba:
{%- for rule in (postgres_hba_system_rules
                 + postgres_hba_monitor_rules
                 + (postgres_hba_rules | default([]))) %}
    - {{ render_hba_line(rule) }}
{%- endfor %}
```

The block is now unconditional; the `patroni_pg_hba_managed` switch is
gone.

- [ ] **Step 4: yamllint the template**

Run: `yamllint roles/patroni/templates/patroni.yml.j2 || true`
Expected: yamllint cannot parse Jinja, so it may complain. The
authoritative check is `make lint` (yamllint configured to skip `.j2`
inside Patroni's tree, plus ansible-lint).

- [ ] **Step 5: Render the template by hand for a sanity check (no commit yet)**

Run a one-liner Jinja render against the file using ansible's
`template` filter to verify there are no syntax errors. From the repo
root:

```bash
ansible localhost -m debug -a "msg={{ lookup('template', 'roles/patroni/templates/patroni.yml.j2') | length }}" \
  -e "patroni_scope=test patroni_namespace=/test/ patroni_listen_address=0.0.0.0 patroni_rest_port=8008 \
      patroni_advertise_address=127.0.0.1 patroni_cert_file=/tmp/c patroni_key_file=/tmp/k \
      patroni_trusted_ca_file=/tmp/ca patroni_etcd_protocol=https patroni_shared_buffer_ratio=0.25 \
      patroni_replication_user=replicator patroni_rewind_user=rewind_user patroni_superuser=postgres \
      patroni_superuser_password=x patroni_replication_password=x patroni_rewind_password=x \
      patroni_postgres_listen_address=0.0.0.0 patroni_postgres_port=5432 \
      patroni_postgres_data_dir=/tmp/pg patroni_tune_profile=oltp postgres_version=18 \
      postgres_hba_system_rules='[{\"contype\":\"local\",\"databases\":\"all\",\"users\":\"all\",\"method\":\"peer\"}]' \
      postgres_hba_monitor_rules='[]' postgres_hba_rules='[]'" 2>&1 | tail -5
```

Expected: a non-zero length printed, no `TemplateSyntaxError` or
`UndefinedError`. If it errors with "groups undefined" (because the
template loops `groups['etcd']`), that's a known limitation of this
render-out-of-band check — proceed to step 6, the molecule run is the
real test.

- [ ] **Step 6: Commit**

```bash
git add roles/patroni/templates/patroni.yml.j2
git commit -m "feat(patroni): render full pg_hba.conf via postgresql.pg_hba"
```

---

## Task 3: Drop `patroni_pg_hba_managed` from patroni defaults

**Files:**

- Modify: `roles/patroni/defaults/main.yml:81-84`

- [ ] **Step 1: Remove the variable and its comment**

Delete lines 81-84 (the `pg_hba management` comment block and the
`patroni_pg_hba_managed: false` line). After deletion, the file
transitions directly from line 80 (blank) to what was line 86
(`# Health gates`).

- [ ] **Step 2: Confirm no other reference exists**

Run: `grep -rn "patroni_pg_hba_managed" roles/ playbooks/ inventory/ tests/`
Expected: no matches.

- [ ] **Step 3: Commit**

```bash
git add roles/patroni/defaults/main.yml
git commit -m "refactor(patroni): drop patroni_pg_hba_managed; Patroni always writes pg_hba"
```

---

## Task 4: Remove pg_hba management from `roles/provision`

**Files:**

- Modify: `roles/provision/tasks/main.yml:36-39`
- Modify: `roles/provision/defaults/main.yml:25-37`
- Delete: `roles/provision/tasks/_hba.yml`

- [ ] **Step 1: Remove the `Manage pg_hba.conf` block from `tasks/main.yml`**

Delete lines 36-39 (the four-line block starting `- name: Manage pg_hba.conf`).
The file transitions directly from line 35 (blank) to what was line 40
(`- name: Create / update PostgreSQL roles`).

- [ ] **Step 2: Remove the HBA-related variables from `defaults/main.yml`**

Delete lines 25-37 (the `# pg_hba.conf management` comment plus the four
`provision_pg_hba_*` definitions and the `# Monitor rule: ...` comment).
The file transitions from line 24 (the
`provision_extensions_in_db: postgres` line preceded by a blank line) to a
clean end.

- [ ] **Step 3: Delete the `_hba.yml` task file**

Run: `git rm roles/provision/tasks/_hba.yml`

- [ ] **Step 4: Confirm no other reference exists**

Run:
```bash
grep -rn "provision_pg_hba\|_hba.yml" roles/ playbooks/ inventory/ tests/
```
Expected: no matches.

- [ ] **Step 5: Commit**

```bash
git add roles/provision/tasks/main.yml roles/provision/defaults/main.yml
git commit -m "refactor(provision): remove pg_hba.conf management (Patroni owns it)"
```

---

## Task 5: Add per-node byte-equality assertion to the HA molecule scenario

**Files:**

- Modify: `tests/molecule/provision/molecule/ha/verify.yml`

- [ ] **Step 1: Add a fingerprint-collection play before the existing leader-grep play**

In `tests/molecule/provision/molecule/ha/verify.yml`, insert this play
after the existing `Verify pg_hba on the leader contains the app rule`
play (currently lines 58-71) and before the
`Probe psql 5433` play (currently lines 73-88):

```yaml
- name: Verify pg_hba.conf is byte-identical on every node
  hosts: postgres
  ignore_errors: "{{ lookup('ansible.builtin.env', 'MOLECULE_TASK_IGNORE_ERRORS') | default('', true) | bool }}"
  become: true
  gather_facts: false
  tasks:
    - name: Fingerprint pg_hba.conf on each node
      ansible.builtin.command: sha256sum /var/lib/pgsql/18/data/pg_hba.conf
      register: hba_sum
      changed_when: false

    - name: Collect fingerprints into a single fact
      ansible.builtin.set_fact:
        hba_sums_per_host: "{{ hba_sums_per_host | default({})
                                | combine({inventory_hostname: hba_sum.stdout.split()[0]}) }}"

    - name: Compare fingerprints across the cluster
      ansible.builtin.assert:
        that:
          - hostvars.values()
            | map(attribute='hba_sums_per_host', default={})
            | map('dict2items') | sum(start=[])
            | map(attribute='value') | unique | length == 1
        fail_msg: >-
          pg_hba.conf differs across nodes. Per-host sha256:
          {{ hostvars | dict2items
             | map(attribute='value.hba_sums_per_host', default={})
             | list }}
      run_once: true
```

- [ ] **Step 2: Lint**

Run: `yamllint tests/molecule/provision/molecule/ha/verify.yml`
Expected: no output.

- [ ] **Step 3: Commit (test added but not yet run)**

```bash
git add tests/molecule/provision/molecule/ha/verify.yml
git commit -m "test(provision/ha): assert pg_hba.conf byte-identical on every node"
```

---

## Task 6: Run lint and the affected molecule scenarios

**Files:** none modified

- [ ] **Step 1: Run `make lint` and ignore pre-known `.claude/worktrees` markdown noise**

Run: `make lint 2>&1 | grep -vE "\.claude/worktrees/" | tail -30`
Expected: yamllint, ansible-lint, ruff, ruff-format, markdownlint, shellcheck, xmllint all pass. The summary should show `Profile 'production' was required, and it passed.` from ansible-lint and no failures from any other tool (apart from the pre-existing markdown errors inside `.claude/worktrees/`, which we agreed to ignore).

If anything else fails: stop, fix it, then re-run.

- [ ] **Step 2: Run the `patroni` default molecule scenario**

Run: `make test ROLE=patroni`
Expected: scenario passes. This is the smoke test that the new
`patroni.yml.j2` renders cleanly and Patroni accepts the composed
`postgresql.pg_hba` block on a single-node cluster.

- [ ] **Step 3: Run the `provision` default molecule scenario**

Run: `make test ROLE=provision`
Expected: scenario passes. The existing `Pg_hba contains app rule` grep
assertion in `tests/molecule/provision/molecule/default/verify.yml` (line
~58) now passes against Patroni-rendered pg_hba instead of
provision-rendered pg_hba. Same content, different writer.

- [ ] **Step 4: Run the HA molecule scenarios that exercise replication**

Run: `make test ROLE=haproxy` (this scenario tree contains the only
multi-node Patroni topology).
Expected: replication comes up; the `ha` sub-scenario in
`tests/molecule/provision/molecule/ha/` runs its byte-equality
assertion and passes.

Also run: `make test ROLE=provision` (default + ha sub-scenarios) if the
provision-ha scenario isn't already covered above.

If replication fails to start on a replica with a "no pg_hba.conf entry
for replication" or "password authentication failed" error, that is the
exact bug this plan fixes; the fix lives in Task 1 step 1 (the
`scram-sha-256` method for the replicator `hostssl` rule). Verify Task 1
landed correctly.

- [ ] **Step 5: Commit nothing (lint+test pass is the deliverable for this task)**

If all three molecule runs succeeded, this task is complete. If any
test failed, do not proceed to Task 7 — go back and fix the underlying
issue first.

---

## Task 7: Push

**Files:** none modified

- [ ] **Step 1: Push to `main`**

Run: `git push`
Expected: all six commits land on `main`.

---

## Self-Review

**Spec coverage:**

- Spec §"Components → `inventory/group_vars/postgres.yml`" → Task 1.
- Spec §"Components → `roles/patroni/templates/patroni.yml.j2`" → Task 2.
- Spec §"Rule-shape normalization" macro → Task 2 step 1.
- Spec §"Components → `roles/provision`" → Task 4.
- Spec §"`patroni_pg_hba_managed` is removed" → Task 3.
- Spec §"Testing → Molecule → byte-identical assertion" → Task 5.
- Spec §"Testing → Replication smoke" → Task 6 step 4 (uses existing HA scenario; replicator method change in Task 1 makes the path satisfiable).
- Spec §"Testing → `tests/molecule/patroni` verify the composed list" — *deliberately not added as a separate task*. The patroni default scenario in Task 6 step 2 already starts Patroni successfully against the new template, which is the strongest acceptance signal. Adding a separate "grep the rendered patroni.yml" assertion would duplicate the smoke test. If you want one, add a step here:

```yaml
- name: Patroni rendered postgresql.pg_hba includes the system rules
  ansible.builtin.command: grep -E "^- (hostssl|host) " /etc/patroni/patroni.yml
  register: pg_hba_rendered
  changed_when: false
  failed_when: pg_hba_rendered.stdout_lines | length < 6
```

into `tests/molecule/patroni/molecule/default/verify.yml`. Optional, not
required for this plan to be complete.

- Spec §"Migration" — no dedicated task; first `make deploy` on an
existing cluster triggers patroni.yml change → Patroni restart →
reconcile rewrites pg_hba.conf. Documented in spec, no code needed.

**Placeholder scan:** none — every step has concrete content.

**Type consistency:** rule-dict keys (`contype/databases/users/source_addr/method` and the alternate `db/user/source` shape) are normalized by the macro in Task 2 step 1; both shapes appear in Task 1 (system rules use the former) and operator response-file rules (use the latter) and the macro accepts both. `postgres_hba_system_rules`, `postgres_hba_monitor_rules`, `postgres_hba_rules` names are consistent across Tasks 1, 2, 5.

**Scope:** one coherent change, no decomposition needed.

**Ambiguity:** the only judgment call is the optional patroni-template grep test (called out explicitly in spec coverage). Everything else is mechanical.
