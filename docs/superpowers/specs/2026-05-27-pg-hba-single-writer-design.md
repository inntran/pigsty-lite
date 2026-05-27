# pg_hba.conf single-writer design

## Problem

`pg_hba.conf` currently has two writers, and they disagree.

1. **Patroni** writes pg_hba at initdb from the `bootstrap.pg_hba` block in
   `/etc/patroni/patroni.yml`, and re-writes it on every reconcile loop when
   `postgresql.pg_hba` is set (gated today by `patroni_pg_hba_managed`,
   defaulted to `false`).
2. **`roles/provision`** renders pg_hba via `community.postgresql.postgresql_pg_hba`
   in `tasks/_hba.yml`, but only on the current Patroni leader
   (`delegate_to: provision_leader_host`, `run_once: true`).

Three observed bugs follow from this:

- **Per-node drift.** Only the leader-at-deploy-time gets the provision file.
  Other nodes keep Patroni's initdb pg_hba. When the leader role moves, the
  cluster sees different content on different nodes (operator reported one
  node had the original Patroni pg_hba with provision rules appended, the
  other had only the provision rules, no trailing newline).
- **Replicator auth is unsatisfiable.** `provision_pg_hba_system_rules` sets
  `hostssl replication replicator 0.0.0.0/0 method:cert`, but `roles/certs`
  only issues per-host certs (CN=hostname); no cert with CN=`replicator`
  exists, and there is no `pg_ident` map. Patroni connects with SSL+scram
  password; the server demands a client cert; libpq falls back to plaintext;
  the server rejects because no plaintext `host` rule exists; replication
  fails with the misleading "no encryption" error.
- **Rotated passwords aren't applied.** Patroni only sets the three managed
  roles' passwords at initdb. Rotating the vault values rewrites
  `/etc/patroni/patroni.yml` but never reconciles `pg_authid`. Already
  addressed by `playbooks/rotate_passwords.yml`; mentioned here only because
  it surfaced the deeper writer-mismatch issue.

## Goal

`pg_hba.conf` has exactly one writer. Every node renders the same content.
Operators add HBA rules in the response file the same way they do today.
Production HBA changes are reload-only, no PG restart.

## Non-goals

- Re-architecting client-cert authentication (no replicator-CN cert is
  introduced; the unsatisfiable `cert` rule is replaced with scram, which
  is what Patroni already sends).
- Re-architecting password rotation (`rotate_passwords.yml` already handles
  it).
- Adding new operator-facing HBA-rule shapes; the existing
  `postgres_hba_rules` schema in the response file is preserved.

## Approach

Patroni is the single writer. The rule data moves to `group_vars/postgres.yml`
(the established home for vars shared across `roles/patroni` and
`roles/postgres`). `roles/provision` no longer manages pg_hba.

### Why Patroni, not provision-on-every-node

Patroni already runs on every node, has a reconcile loop, validates HBA
syntax before writing, and issues `pg_reload_conf()` after a successful
write — so HBA changes are non-disruptive by construction. Provision would
need an Ansible-side loop and a handler chain to match that behavior, and
would still race against Patroni's bootstrap pg_hba on a fresh initdb.

### Why `group_vars/postgres.yml`, not a role default

The file's existing docstring states it encodes "shared decisions that span
both roles" (patroni + postgres). HBA data is consumed only by patroni for
writing, but the *meaning* of the rules (which users, which DBs, which auth
methods) is a cluster-wide decision, not a patroni internal. Co-locating it
with `patroni_*_password` (already in `group_vars/postgres.yml`) is
consistent.

## Architecture

```
inventory/group_vars/postgres.yml
  ├── postgres_hba_system_rules    foundational: local, loopback, replication, rewind
  ├── postgres_hba_monitor_rules   filled by monitoring role; defaults to []
  └── postgres_hba_rules           operator-defined, sourced from response.yml
        │
        ▼
roles/patroni/templates/patroni.yml.j2
  ├── bootstrap.pg_hba             minimal (initdb-time only)
  └── postgresql.pg_hba            composed: system + monitor + operator
        │
        ▼
Patroni daemon, every reconcile (~10s)
        │
        ▼
{{ postgres_data_dir }}/pg_hba.conf  +  pg_reload_conf()
```

## Components

### `inventory/group_vars/postgres.yml`

Add three variables:

```yaml
# Foundational HBA rules. Foundational means: required for Patroni and PG
# to function as a cluster. Operators normally do not edit this.
postgres_hba_system_rules:
  - { contype: local,   databases: all,         users: all,                                                  method: peer }
  - { contype: host,    databases: all,         users: postgres,                          source_addr: 127.0.0.1/32, method: trust }
  - { contype: host,    databases: replication, users: "{{ patroni_replication_user | default('replicator') }}",  source_addr: 127.0.0.1/32, method: trust }
  - { contype: hostssl, databases: replication, users: "{{ patroni_replication_user | default('replicator') }}",  source_addr: 0.0.0.0/0,    method: scram-sha-256 }
  - { contype: host,    databases: postgres,    users: "{{ patroni_rewind_user | default('rewind_user') }}",      source_addr: 127.0.0.1/32, method: trust }
  - { contype: hostssl, databases: postgres,    users: "{{ patroni_rewind_user | default('rewind_user') }}",      source_addr: 0.0.0.0/0,    method: scram-sha-256 }

# Monitoring rules; populated by the monitoring role or overridden here.
postgres_hba_monitor_rules: []
```

`postgres_hba_rules` (operator layer) is already supplied by the response
file and consumed today; no schema change.

The change versus the current `provision_pg_hba_system_rules`:
the replicator `hostssl` rule's `method` flips from `cert` to `scram-sha-256`,
fixing the unsatisfiable-mTLS bug.

### `roles/patroni/templates/patroni.yml.j2`

Two HBA blocks. Each is a list of `pg_hba.conf` lines.

**`bootstrap.pg_hba`** stays small. It only needs to let `initdb` finish and
let Patroni bring the cluster up before the first reconcile. The full list
arrives via `postgresql.pg_hba` within ~10 seconds:

```yaml
bootstrap:
  pg_hba:
    - local all all peer
    - host all postgres 127.0.0.1/32 trust
    - host replication {{ patroni_replication_user }} 127.0.0.1/32 trust
    - host postgres {{ patroni_rewind_user }} 127.0.0.1/32 trust
```

**`postgresql.pg_hba`** is the full canonical list, composed at render time
by iterating the three group_vars lists. A short Jinja macro converts each
rule dict to the `pg_hba.conf` line format:

```yaml
postgresql:
  pg_hba:
{%- for rule in (postgres_hba_system_rules
                 + postgres_hba_monitor_rules
                 + (postgres_hba_rules | default([]))) %}
    - {{ render_hba_line(rule) }}
{%- endfor %}
```

The existing `patroni_pg_hba_managed` switch is removed from
`roles/patroni/defaults/main.yml`; Patroni-writes is now the only path and
the gate adds no value. The template no longer wraps `postgresql.pg_hba`
in `{% if patroni_pg_hba_managed %}`.

### Rule-shape normalization

Two rule shapes are in flight today: `provision_pg_hba_system_rules` uses
keys `contype, databases, users, source_addr, method`, while the operator
`postgres_hba_rules` from the response file uses `contype, db, user, source,
method`. The patroni template normalizes both before rendering. A Jinja
macro at the top of the template:

```jinja
{%- macro render_hba_line(r) -%}
{%-   set contype = r.contype | default('hostssl') -%}
{%-   set db      = r.databases | default(r.db) -%}
{%-   set usr     = r.users | default(r.user) -%}
{%-   set addr    = r.source_addr | default(r.source | default('')) -%}
{%-   set method  = r.method | default('scram-sha-256') -%}
{{ contype }} {{ db }} {{ usr }}{% if contype != 'local' %} {{ addr }}{% endif %} {{ method }}
{%- endmacro -%}
```

The macro is the only place that knows the on-disk line format. Either
key set is accepted, so the operator's response-file schema stays
unchanged.

### `roles/provision`

- Delete `tasks/_hba.yml`.
- Remove the "Manage pg_hba.conf" import block and the
  `provision_pg_hba_managed` reference from `tasks/main.yml`.
- Remove `provision_pg_hba_managed`, `provision_pg_hba_path`,
  `provision_pg_hba_system_rules`, and `provision_pg_hba_monitor_rules`
  from `defaults/main.yml`.
- Keep `handlers/main.yml` "Reload PostgreSQL"; it is still notified by
  other provision tasks (extensions, role attributes). HBA changes no
  longer notify it — Patroni reloads PG itself.

### Other roles

A repo-wide grep confirms `provision_pg_hba_monitor_rules` is referenced
only inside `roles/provision` itself (defaults + `_hba.yml`). No other
role appends to it today, so the rename is a strict deletion of the
provision variables and a clean introduction of `postgres_hba_monitor_rules`
in `group_vars/postgres.yml`. Future monitoring HBA contributions go to
the new variable.

## Data flow on a production HBA change

```
operator edits inventory or response file → postgres_hba_rules updated
make plan       renders new /etc/patroni/patroni.yml in check mode, shows diff
make deploy     writes /etc/patroni/patroni.yml on every node
                handler restarts patroni only if the file changed
Patroni reconcile, within ~10 s on each node:
  validates the new pg_hba list
  rewrites {{ postgres_data_dir }}/pg_hba.conf
  issues pg_reload_conf() to PG
Existing sessions undisturbed; new connections use new rules.
```

No leader delegation, no `run_once`. Every node renders its own
`patroni.yml`. Patroni — not Ansible — is responsible for getting the
right content onto disk and into PG.

## Error handling

- **Bad HBA syntax.** Patroni validates each line before writing. On
  error it logs to `journalctl -u patroni` and keeps the existing
  `pg_hba.conf`. The cluster keeps running with the previous rules.
  Operator fixes and re-deploys. No data-path impact.
- **`patroni.yml` render fails on a node.** The Ansible task fails on that
  host; existing `/etc/patroni/patroni.yml` is unchanged; Patroni keeps
  using the old config. No partial write.
- **Patroni restart fails after the handler fires.** Covered by the
  existing health gate in
  `roles/cluster_ops/tasks/wait_member_converged.yml`, invoked from
  `playbooks/_postgres_bootstrap.yml`. Deploy fails loudly.
- **A node misses the deploy run.** That node keeps its old
  `patroni.yml`. This is a deploy-completeness issue, not an HBA-specific
  one — same risk model as any other patroni.yml change today.
- **Race between Patroni's bootstrap pg_hba and the first reconcile.**
  Window is ~10 s. During the window only local-loopback connections
  work; external clients receive "no pg_hba.conf entry" until reconcile
  completes. Acceptable: only happens at initial cluster bootstrap, and
  Patroni's leader-election + health checks already gate external traffic
  during bootstrap.

## Testing

### Unit-ish (template rendering)

Existing `tests/configure` covers response-file → inventory translation;
no change needed there.

### Molecule

- **`tests/molecule/patroni`** (existing scenario):
  add a verify task that reads `/etc/patroni/patroni.yml` and asserts the
  `postgresql.pg_hba` block contains:
    - every rule in `postgres_hba_system_rules`
    - every rule in `postgres_hba_rules`
    - the minimal `bootstrap.pg_hba` set
- **`tests/molecule/provision`** (existing scenario):
  remove any verify task that asserts `pg_hba.conf` content via provision.
  Add a negative assertion: `tasks/_hba.yml` does not exist.
- **`tests/molecule/haproxy/molecule/ha`** (existing HA scenario, the only
  scenario with two PG nodes): after the cluster is up, assert
  `pg_hba.conf` is byte-identical on both nodes (excluding the trailing
  newline normalization — diff with `cmp` or `diff -q`). This catches the
  per-node drift bug directly.
- **Replication smoke**: the HA scenario already brings up streaming
  replication; with the `method: cert` → `scram-sha-256` change, the
  replica must reach `state=streaming`. Existing verify asserts this; no
  change needed, but the test now genuinely exercises the new auth path.

### Manual smoke on the operator's cluster

After landing:

```
make deploy
ansible -i inventory postgres -m shell -a 'md5sum /var/lib/pgsql/18/data/pg_hba.conf'
# all nodes return the same hash
ansible -i inventory postgres -m shell -a 'patronictl list'
# all members streaming
```

## Migration

Existing clusters have a mix-of-writers `pg_hba.conf` today. After this
change, the first `make deploy` triggers a Patroni restart on each node
(because `/etc/patroni/patroni.yml` changes), and Patroni then rewrites
`pg_hba.conf` on the next reconcile. No manual cleanup needed; Patroni's
`pg_reload_conf()` is non-disruptive.

If the cluster is on the unsatisfiable `method: cert` rule today and
streaming replication is currently *failing*, the deploy will heal it:
the new scram-sha-256 rule matches what Patroni sends. Operators with a
healthy cluster running on the old rule (somehow — perhaps via a now-lost
replicator-CN cert) should verify replication after deploy.

The `patroni_pg_hba_managed` variable is removed. Any inventory or
response file that sets it explicitly will fail with an "undefined
variable used in template" error if it was ever templated elsewhere —
grep confirms it is not. Removing it is safe.

## Out-of-scope follow-ups

- True mTLS for replication (cert auth with a `replicator`-CN cert and
  `pg_ident` map). Would be a separate spec.
- Same single-writer treatment for `postgresql.conf` (Patroni already
  owns it via `postgresql.parameters`; less urgent because the
  postmaster doesn't have two parallel writers).
- Removing `patroni_pg_hba_managed` from inventory examples / docs is
  part of this change, but a broader audit of "managed flags" elsewhere
  is out of scope.
