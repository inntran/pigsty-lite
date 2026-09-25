# provision

Applies the declarative `postgres_*` lists from the response file to the
running cluster. Runs once per `make deploy`, only on the host that
currently holds the Patroni leader. After P3, day-2 workflow for "add a
database" / "add a user" / "add an extension" is: edit the response file,
`make deploy`.

`pg_hba.conf` is not managed here: Patroni writes it on every member from
`postgres.hba_rules` (see roles/patroni).

## Inputs (from response file)

| Variable | Shape | Example |
| --- | --- | --- |
| `postgres_users` | list of `{name, password, roles?}` | `[{name: app, password: "...", roles: [pg_read_all_data]}]` |
| `postgres_databases` | list of `{name, owner?, encoding?}` | `[{name: app, owner: app}]` |
| `postgres_extensions` | list of strings or `{name, db?}` dicts | `[pg_stat_statements, {name: pgvector, db: app}]` |
| `postgres_hba_rules` (consumed by roles/patroni) | list of `{db, user, source, method}` | `[{db: app, user: app, source: 10.0.0.0/8, method: scram-sha-256}]` |

## What this role owns

- Role/user existence and membership (not per-table privileges).
- Database existence and ownership.
- Extension presence in named databases.

## What this role does NOT own

- Per-table grants and ALTER DEFAULT PRIVILEGES.
- Schema migrations (use Liquibase/sqitch/etc.).
- Tablespaces (declared via `extra_parameters` on the cluster).

## Ordering

`users`, then `databases`, then `extensions`.

## Idempotence

Second run is zero-change. The `postgresql_*` modules diff against
catalog state.

## Tags

- `provision` - full role
- `provision,users` - users only
- `provision,databases` - databases only
- `provision,extensions` - extensions only
