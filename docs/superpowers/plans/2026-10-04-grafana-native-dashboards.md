# Grafana: Pigsty dashboards rewritten to native metrics

## Goal

Ship a small set of useful dashboards for regular users, adapted from Pigsty's
Grafana JSON, querying only metrics pigsty-lite already collects. No new SQL,
no new exporters, no new collector flags, no recording rules.

Non-goal: Pigsty parity.

## Inputs

- Pigsty dashboards: `github.com/pgsty/pigsty` @ `745a4ea`
  (`files/grafana/**.json`, Apache-2.0). Recording rules used by those
  dashboards: `files/victoria/rules/*.yml`.
- `pg_exporter` collector configs (`../pg_exporter/config/*.yml`, Apache-2.0):
  reference only, for the meaning and unit (`scale`) of each Pigsty metric.
  Nothing from it is shipped.
- Metric names actually collected, snapshotted from the AIO host
  (`/api/v1/label/__name__/values`, 2026-10-04, 1,123 names).

## Dashboards (new, in `roles/grafana/files/dashboards/`)

| New file | Source (Pigsty) | Panels that map to our metrics |
|---|---|---:|
| `node-instance.json` | `node/node-instance.json` | ~78 of 93 |
| `postgresql-instance.json` | `pgsql/pgsql-instance.json` | ~16 of 34 |
| `postgresql-database.json` | `pgsql/pgsql-database.json` | ~35 of 47 |
| `postgresql-pgbouncer.json` | `pgsql/pgsql-pgbouncer.json` | ~24 of 31 |
| `postgresql-patroni.json` | `pgsql/pgsql-patroni.json` | ~12 of 18 |

Existing `pigsty-lite-overview.json` gets one fix: its replication-lag panel
queried `pg_replication_lag_bytes`, which postgres_exporter does not emit; it
now uses `pg_stat_replication_pg_wal_lsn_diff` (stat_replication collector,
enabled by default, series only on a primary with replicas). Existing
`pgsql-exporter.json` changes only its title, `PGSQL Exporter` →
`PostgreSQL Exporter`; its file name and `uid` stay, so bookmarks and the
Molecule check keep working.

**Naming.** User-visible text says `PostgreSQL`, never `PGSQL`: dashboard
titles (`PostgreSQL Instance`, `PostgreSQL Database`, `PostgreSQL pgBouncer`,
`PostgreSQL Patroni`), row and panel titles, link titles and descriptions.
New files and `uid`s use the `postgresql-` prefix. Tags keep the existing
`postgresql` tag.

Not shipped: Pigsty's PGSQL Tables / Table. `postgres_exporter` connects with
`dbname=postgres` (`roles/monitoring_agents/defaults/main.yml`), and
`pg_stat_user_tables_*` / `pg_statio_user_tables_*` describe only the connected
database, so they would never show application tables. Fixing that needs
exporter configuration, which is outside this plan. For the same reason, drop
any table-level panel from the other dashboards.

## Rewrite rules (apply to every new dashboard)

1. **Drop** any panel with a query that still needs a metric we do not collect
   after rules 2-4. Do not leave empty panels. Re-flow `gridPos` so no
   gaps remain; drop rows left empty.
2. **Inline recording rules.** Replace each Pigsty recording-rule name
   (`pg:ins:*`, `node:ins:*`, …) with its defining expression from
   `files/victoria/rules/*.yml`, recursively, then apply rule 3.
3. **Rename metrics** only through the explicit table below. A Pigsty
   metric not in the table has no equivalent: drop the panel (rule 1). Names on
   the right were checked against the AIO metric snapshot; labels against the
   exporter source (postgres_exporter v0.20.1, pgbouncer_exporter v0.12.1).

   | Pigsty | Native | Labels / notes |
   |---|---|---|
   | `pg_db_{numbackends,xact_commit,xact_rollback,blks_hit,blks_read,tup_returned,tup_fetched,tup_inserted,tup_updated,tup_deleted,temp_files,temp_bytes}` | `pg_stat_database_<same>` | `datid`, `datname` |
   | `pg_db_{blk_read_time,blk_write_time}` (s) | `pg_stat_database_<same>` (**ms**) | divide by 1000 |
   | `pg_db_active_time` (s) | `pg_stat_database_active_time_seconds_total` (s) | |
   | `pg_db_xact_total` | `xact_commit + xact_rollback` | derived |
   | `pg_db_blks_access` | `blks_hit + blks_read` | derived |
   | `pg_db_tup_modified` | `tup_inserted + tup_updated + tup_deleted` | derived |
   | `pg_db_conn_limit` | `pg_database_connection_limit` | `datname` |
   | `pg_size_bytes{type="db"}` | `pg_database_size_bytes` | `datname` |
   | `pg_activity_count` | `pg_stat_activity_count` | `datname`, `state` |
   | `pg_activity_max_tx_duration` | `pg_stat_activity_max_tx_duration` | `datname`, `state` |
   | `pg_lock_count` | `pg_locks_count` | `datname`, `mode` |
   | `pg_in_recovery` | `pg_replication_is_replica` | |
   | `pg_setting_<x>` | `pg_settings_<x>` (check unit suffix, e.g. `_bytes`, `_seconds`) | |
   | `pg_archiver_finish_count` / `failed_count` | `pg_stat_archiver_archived_count` / `failed_count` | |
   | `pg_bgwriter_buffers_{alloc,clean}` | `pg_stat_bgwriter_buffers_{alloc,clean}_total` | |
   | `pgbouncer_pool_active_clients` | `pgbouncer_pools_client_active_connections` | `database`, `user` |
   | `pgbouncer_pool_waiting_clients` | `pgbouncer_pools_client_waiting_connections` | `database`, `user` |
   | `pgbouncer_pool_{active,idle,used,login}_servers` | `pgbouncer_pools_server_{active,idle,used,login}_connections` | `database`, `user` |
   | `pgbouncer_pool_tested_servers` | `pgbouncer_pools_server_testing_connections` | `database`, `user` |
   | `pgbouncer_pool_maxwait` (s) | `pgbouncer_pools_client_maxwait_seconds` (s) | `database`, `user` |
   | `pgbouncer_database_{pool_size,reserve_pool,max_connections,current_connections,paused,disabled}` | `pgbouncer_databases_<same>` | `name` (pgbouncer alias), `database` (real db) |
   | `pgbouncer_stat_total_query_count` | `pgbouncer_stats_totals_queries_pooled_total` | `database` |
   | `pgbouncer_stat_total_xact_count` | `pgbouncer_stats_totals_sql_transactions_pooled_total` | `database` |
   | `pgbouncer_stat_total_query_time` | `pgbouncer_stats_totals_queries_duration_seconds_total` (s) | `database`; check the Pigsty unit in `pg_exporter/config/0930-pgbouncer_stat.yml` |
   | `pgbouncer_stat_total_xact_time` | `pgbouncer_stats_totals_server_in_transaction_seconds_total` (s) | `database`; same check |
   | `pgbouncer_stat_total_{received,sent}` | `pgbouncer_stats_totals_{received,sent}_bytes_total` | `database` |
   | `patroni_*` | unchanged (native Patroni `/metrics`) | |
   | `node_*` | unchanged (node_exporter) | |
   | `<x>_up`, `<x>_exporter_up` | `up{job="<job>"}` | |

4. **Audit units per panel.** For every retained panel, record the source
   metric, its unit on both sides (Pigsty: `pg_exporter` `scale`; native:
   exporter source), and any conversion. Never apply a Pigsty `scale` to a
   native metric that is already in base units (pgBouncer times are already
   seconds, traffic already bytes). Write this audit into the PR description,
   not the repo.
5. **Labels.** `cls` → `cluster`, `ins` → `instance`. Remove `ip` filters and
   `ip` variables. Map Pigsty `job` values to ours (`node`, `postgres`,
   `pgbouncer`, `pgbackrest`, `patroni`). pgBouncer `datname` → `database`.
   Verify label names per metric against the exporter, not by analogy.
6. **Variables.** Keep `cluster` and `instance` (plus `datname` where the
   source has it), populated by `label_values()` on our metrics.
7. **Datasource.** Every panel and variable uses
   `{"type": "victoriametrics-metrics-datasource", "uid": "VictoriaMetrics"}`,
   matching the existing dashboards.
8. **Links.** Keep links only to dashboards we ship; remove the rest.
9. **Layout.** After the rewrite, re-pack `gridPos` so each row section has no
   horizontal or vertical holes: keep reading order and relative widths, fill
   each line to 24 columns, give a line's panels one height.
10. **Identity.** Set `uid` to the new file's stem (`postgresql-instance`,
   …; `node-instance` for the node dashboard), rewrite links between shipped
   dashboards to those `uid`s, reset `id` to `null`, and set `description` to
   `Adapted from Pigsty <path> @745a4ea (Apache-2.0) for pigsty-lite native metrics.`

## Tests

- **New** `tests/configure/test_grafana_dashboards_static.py` (pytest, no network):
  - every file in `roles/grafana/files/dashboards/` is valid JSON with a unique `uid`;
  - every datasource reference is the `VictoriaMetrics` uid;
  - no dashboard, row, panel or link title contains `PGSQL`;
  - no PromQL contains `cls=`, `ins=`, `ip=`, or a recording-rule name (`:`);
  - every metric name referenced in PromQL is in
    `tests/configure/fixtures/grafana_metric_names.txt` (the AIO snapshot,
    with a header recording its date and exporter versions, plus a short
    commented section for replica-only names verified from exporter source).
  This is a guardrail against Pigsty names leaking through, not proof that
  queries work; the live check under Verification covers that.
- **Extend** `tests/molecule/grafana/molecule/default/verify.yml` to check each
  new `uid` is provisioned.

## Docs

- `roles/grafana/README.md`: one section listing the shipped dashboards, the
  Pigsty source commit, the rewrite rules in brief, and how to refresh the
  metric-name fixture.
- `docs/operations/day2-monitoring.md`: list the dashboards.
- `roles/grafana/README.md`: rename the `## PGSQL Exporter dashboard` heading
  to `## PostgreSQL Exporter dashboard`.

## Out of scope (follow-ups)

- Replication / slots: AIO has no replicas, so they can't be verified against
  the snapshot. Needs an HA-cluster metric snapshot first.
- pgBackRest / PITR: per-backup `pgbackrest_backup_*` metrics are absent on the
  AIO host; find out why before building panels.
- Query (`pg_stat_statements`), checkpointer, `pg_stat_io`, PGCAT, etcd,
  HAProxy, VIP.

## Verification

1. `make test` (includes the new static test) and `make lint`.
2. Grafana molecule scenario.
3. Deploy to the AIO host and run light `pgbench` load. Then, for each shipped
   dashboard, through Grafana's `/api/ds/query` with the dashboard's default
   variables:
   - every query executes without error;
   - every variable returns at least one value;
   - the core panels listed per dashboard below return at least one series.
   Panels that may legitimately be empty (locks, waiting clients, paused/
   disabled pools, Patroni pending-restart/failsafe, etc.) are listed per
   dashboard in the PR description.

   Core panels that must return data:
   - NODE Instance: CPU usage, load, memory usage, disk IOPS, filesystem space,
     network bandwidth.
   - PostgreSQL Instance: aliveness, sessions, TPS, CRUD.
   - PostgreSQL Database: transactions, rows returned/fetched, blocks hit ratio,
     database size.
   - PostgreSQL pgBouncer: QPS, servers & clients, client state.
   - PostgreSQL Patroni: members, leadership, DCS last seen.
