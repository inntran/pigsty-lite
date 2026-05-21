# PostgreSQL Extension RPMs & Creation — design

Date: 2026-05-21

## Problem

`postgres_extensions` declares **SQL extension names** that the `provision`
role runs `CREATE EXTENSION` for on the Patroni leader. But the **RPM** that
provides an extension's files (`.so`, `.control`, SQL scripts) is never
installed on the PostgreSQL nodes.

`pg_stat_statements` works only because it ships inside `postgresql18-contrib`,
which the `postgres` role already installs. `pgvector` does not — declaring
`pgvector` in `postgres_extensions` today produces a `CREATE EXTENSION vector`
failure because `pgvector_18` was never installed.

We need a way to install extension RPMs (e.g. `pgvector_18` from PGDG) so that
declaring an extension results in both the RPM being installed and, where
wanted, the extension being created.

## Solution

Two **independent, file-only** variables that sit next to each other in the
response file:

- `postgres_extension_packages` — RPM packages to install on every PG node.
  Makes extensions *available*.
- `postgres_extensions` — extension names to `CREATE EXTENSION`, and in which
  database. Registers them. This variable already exists and is unchanged.

The two are **fully decoupled**:

- Install an RPM without ever creating the extension — e.g. an extension used
  purely via `shared_preload_libraries`.
- Create a contrib extension (`pg_stat_statements`) with no RPM, because its
  files already ship in `postgresql<version>-contrib`.

There is **no catalog and no name-mapping**. The operator owns the relationship
between an RPM package name (e.g. `pgvector_18`) and the SQL extension name
(e.g. `vector`). The two variables live adjacently in the response file with
comments so the two-part requirement is visible.

Neither variable is surfaced in the interactive `configure` wizard — both are
edited directly in the response file.

### Response file shape

```yaml
postgres:
  # RPM packages providing extension files — installed on every PG node.
  # Makes extensions *available* (CREATE EXTENSION is separate, see below).
  # Values pass through Jinja, so "pgvector_{{ postgres_version }}" auto-syncs
  # with the PG major version. Browse available packages for your platform at:
  #   https://download.postgresql.org/pub/repos/yum/18/redhat/rhel-10-x86_64/
  extension_packages:
    - "pgvector_{{ postgres_version }}"
  # Extensions to CREATE EXTENSION, and in which database — run on the leader.
  extensions:
    - {name: vector, db: app}
    - pg_stat_statements
```

The generated inventory variables are `postgres_extension_packages` and
`postgres_extensions`.

### Data flow

```
postgres_extension_packages ──> [postgres role]  dnf install   (all PG nodes)
postgres_extensions         ──> [provision role] CREATE EXTENSION (leader only)
```

## Components

### 1. `postgres` role — install extension RPMs

- New default in `roles/postgres/defaults/main.yml`:
  `postgres_extension_packages: []`.
- New install task in `roles/postgres/tasks/main.yml`, after the existing
  server/contrib/libs install:

  ```yaml
  - name: Install PostgreSQL extension packages
    ansible.builtin.dnf:
      name: "{{ postgres_extension_packages }}"
      state: present
    when: postgres_extension_packages | length > 0
    tags: [postgres, install]
  ```

Runs on every PG node — extension libraries must exist on replicas too, not
only the leader. Jinja in list values (e.g. `pgvector_{{ postgres_version }}`)
is evaluated naturally by Ansible.

### 2. `provision` role — unchanged

The `provision` role already consumes `postgres_extensions` for
`CREATE EXTENSION`. String and `{name, db}` dict forms keep working as the
existing schema allows. No changes.

### 3. Schema & response generation

- `bin/_response_schema.py` — add `_validate_extension_packages`, mirroring the
  existing `_validate_extensions` pattern: `postgres.extension_packages` must be
  a list of strings. Call it from the postgres validation path.
- `bin/_generate_response_vars.py` — emit `postgres_extension_packages` from
  `postgres.get("extension_packages", [])`, placed adjacent to
  `postgres_extensions` in the generated output.

No wizard prompt — both fields are edited directly in the response file.

### 4. Defaults & examples

- `roles/postgres/defaults/main.yml` — `postgres_extension_packages: []`.
- `responses/ha.rsp.yml.example` and `responses/spof.rsp.yml.example` — add the
  `extension_packages` key in the `postgres:` block, directly above
  `extensions`, with the comment block shown above (including the PGDG browse
  link).
- `roles/postgres/README.md` — document `postgres_extension_packages`.

## Error handling

- **Missing RPM / bad version pin** — if PGDG has no build for the templated
  name (e.g. `pgvector_13`), `dnf` fails immediately with
  `No match for argument`. No pre-check; the error is clear and honest.
- **Extension declared but RPM not listed** — `CREATE EXTENSION` fails in the
  `provision` role with PostgreSQL's own "extension is not available" error.
  This means the operator listed the extension but forgot the package half.
  Acceptable: the two adjacent variables and their comments make the two-part
  requirement visible.

## Testing

- `tests/configure/`:
  - schema test that `postgres.extension_packages` rejects non-string entries.
  - `_generate_response_vars` test that `postgres_extension_packages` passes
    through to the generated vars.
- Molecule — extend the `postgres` scenario to declare
  `postgres_extension_packages: ["pgvector_{{ postgres_version }}"]` and assert
  the RPM is installed. If a scenario exercises `provision`, assert
  `CREATE EXTENSION vector` then succeeds. Check whether existing scenarios
  redeclare `postgres_extensions`/`postgres_extension_packages` and align them.

## Out of scope

- An extension catalog or friendly-name → RPM mapping. Operators write literal
  RPM names and literal SQL extension names.
- Wizard prompts for either variable.
- Per-extension `shared_preload_libraries` management (extensions that need it
  are still configured via existing tuning files / `postgres_extra_parameters`).
