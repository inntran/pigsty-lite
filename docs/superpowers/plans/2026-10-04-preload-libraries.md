# Preload libraries (`shared_preload_libraries`) plan

## Problem

`CREATE EXTENSION timescaledb` fails unless `timescaledb` is in
`shared_preload_libraries`. Today the value is hard-coded to
`'pg_stat_statements'` in each tuning file (`roles/patroni/files/tuning/*.conf`).
The only override is `postgres.extra_parameters.shared_preload_libraries`, which
replaces the whole value and renders a **duplicate YAML key** in
`patroni.yml.j2` (the tuning loop and the extra-parameters loop both emit it).

Upstream Pigsty solves this with an operator-owned `pg_libs` string plus
conditional companion parameters in its tuning templates. No catalog. We follow
the same model, which matches our extension spec ("operator owns the mapping").

## Design

### Variables

Response file (`postgres:` section), both optional, default `[]`:

```yaml
postgres:
  preload_libraries:
    prepend: []   # loaded before the base list, e.g. [citus] (citus must be first)
    append: []    # loaded after the base list, e.g. [timescaledb, auto_explain]
```

Flattened by `bin/_generate_response_vars.py` to:

- `postgres_preload_libraries_prepend` (list of str)
- `postgres_preload_libraries_append` (list of str)

Base list: patroni role default `patroni_preload_libraries_base: [pg_stat_statements]`
(not in the response file; same behaviour as today).

Final value, computed in `patroni.yml.j2`:

```
unique(prepend + base + append)   # order preserved, first occurrence wins
```

joined with `,`. Escape hatch unchanged: `postgres.extra_parameters.shared_preload_libraries`
wins outright.

Schema (`bin/_response_schema.py`): `preload_libraries` must be a mapping with
only `prepend`/`append` keys; each must be a list of non-empty strings with no
comma or whitespace-only entries (a comma would silently split a name).

### Template: merged parameter dict

Keep the existing `ssl_*` lines exactly as they are. Replace the tuning loop and
the `postgres_extra_parameters` loop in `roles/patroni/templates/patroni.yml.j2`
with one merged dict, emitted once:

1. tuning-file entries (same parsing as today; values stay strings, so the
   rendered quoting is unchanged),
2. computed `shared_preload_libraries` (string, comma-joined),
3. `postgres_extra_parameters` (overrides anything above),
4. companion parameters (below), added only for keys **not** already set by
   `postgres_extra_parameters`.

Jinja: assignments inside a `for` loop don't persist, so build the dict with
`{% set ns = namespace(params={}) %}` and `{% set ns.params = dict(ns.params, **{k: v}) %}`
(or `ns.params.update(...)` via `{% set _ = ... %}`), then emit
`{{ k }}: {{ v | to_json }}` per key. Parameter names are plain identifiers
with dots, safe as bare YAML keys (same as today).

Remove the `shared_preload_libraries = ...` line from all three tuning files.

### Companion parameters (from Pigsty's tuning templates)

Derived from the **effective** library list, i.e. after
`extra_parameters.shared_preload_libraries` is applied (split on `,`, strip
whitespace), so an override that adds or removes timescaledb is honoured:

- `timescaledb` → `timescaledb.telemetry_level: "off"`
- `timescaledb` or `citus` → `max_locks_per_transaction: (max_connections | int) * 2`
  (max_connections is a string parsed from the tuning file; oltp 200 → 400,
  olap 100 → 200, tiny 50 → 100). Rendered as a string like the other tuning
  values (Patroni accepts either).

Not ported (YAGNI): pgsodium, pg_duckdb, citus `max_prepared_transactions`,
`timescaledb.max_background_workers`, `cron.database_name`.

### Role defaults (isolated runs)

`roles/patroni/defaults/main.yml`:

```yaml
patroni_preload_libraries_base: [pg_stat_statements]
patroni_preload_libraries_prepend: "{{ postgres_preload_libraries_prepend | default([]) }}"
patroni_preload_libraries_append: "{{ postgres_preload_libraries_append | default([]) }}"
```

So molecule patroni scenarios (which supply only `postgres_tune_profile`)
keep rendering `pg_stat_statements`.

### Docs / examples

- `responses/{aio,spof,ha}.rsp.yml.example` and `responses/site.rsp.yml`:
  add a commented `preload_libraries` block next to `extension_packages` /
  `extensions`, with a note that an extension like timescaledb needs all three
  (package, library, extension) and that changing the list on a running
  cluster requires a rolling restart (the patroni handler already warns).
- Generated fixtures: `inventory/group_vars/all/response.yml` and
  `examples/*/generated/group_vars-all-response.yml` — regenerate / add the two
  new keys (empty lists) so they match the generator.
- `roles/patroni` README / defaults comment and `docs/variables.md` if it lists
  postgres_* vars.

## Behaviour on a running cluster

`postgresql.parameters` is Patroni's local config: re-running the patroni role
reloads Patroni, the parameter becomes `pending_restart`, and the existing
handler warns to do a rolling restart. Provision must run after that restart.
No new restart automation.

## Tests

- `tests/configure/test_patroni_template.py`:
  - default render → `shared_preload_libraries == "pg_stat_statements"`, no
    `timescaledb.*`, no `max_locks_per_transaction`.
  - prepend `[citus]` + append `[timescaledb]` → `"citus,pg_stat_statements,timescaledb"`,
    `max_locks_per_transaction == 400` (oltp), telemetry off.
  - duplicates removed (append `[pg_stat_statements]` → unchanged).
  - `extra_parameters` override of `shared_preload_libraries` wins, and
    companions follow the override (override adds timescaledb → companions
    present; override drops it → companions absent).
  - `extra_parameters.max_locks_per_transaction` wins over the companion.
  - No duplicate keys: load the render with a SafeLoader subclass whose
    mapping constructor raises on a repeated key (plain `safe_load` silently
    accepts duplicates); apply it in `_render_patroni_config` so every test
    checks it.
  - Add the new variables to the test render context.
- `tests/configure/test_schema.py`: valid block, non-list, non-string, comma
  in name, unknown key → SchemaError.
- `tests/configure/test_generate_response_vars.py`: defaults `[]` and
  pass-through.
- Run full `pytest tests/configure`. Molecule not required for this change
  (template-only); run the postgres/patroni scenario if cheap.

## Out of scope

- Auto-deriving libraries from `postgres_extensions` (no catalog).
- Provision pre-check for missing preload.
- Automatic restarts.
