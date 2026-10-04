# Migrating live databases into a new SPOF deployment

Moving a handful of existing, in-use PostgreSQL databases onto a freshly
deployed pigsty-lite `spof` node.

## The one constraint that decides everything

Patroni owns the target cluster. It bootstrapped the data directory, it
holds the leader key in etcd, and it rewrites `postgresql.conf` from its own
DCS config on every restart. **Do not move a data directory into place, do
not `pg_upgrade` into it, and do not restore a foreign physical backup over
it.** Any of those leave Patroni and the on-disk cluster disagreeing about
identity, and the first restart either reverts your work or refuses to start.

So every supported migration path is *logical*: the target stays the cluster
pigsty-lite built, and the data arrives over a SQL or replication connection.

That leaves two real options.

## Choosing between them

| | `pg_dump` / `pg_restore` | Logical replication |
| --- | --- | --- |
| Downtime | Length of the dump + restore | Seconds, at cutover |
| Setup effort | Low | Moderate |
| Source requirements | Any PG ≥ 9.x | PG ≥ 10, `wal_level = logical`, a restart to get it |
| Replicates DDL | n/a (full copy) | **No** — schema must be pre-loaded, and must not change during the sync |
| Sequences | Carried in the dump | **Not** advanced — must be re-synced by hand at cutover |
| Tables without a PK | Fine | Need `REPLICA IDENTITY FULL`, and are slow to apply |
| Large objects (`pg_largeobject`) | Carried in the dump | **Not replicated at all** |

**Recommendation: use `pg_dump`/`pg_restore` unless you have measured the
restore time and found the resulting downtime unacceptable.** For "a couple
of databases" on a SPOF node this is almost always the right call. The
dump path is one command per database, verifiable before cutover, and has
none of the sequence/LO/DDL footguns. Logical replication trades a
substantial increase in moving parts for a shorter window; take that trade
deliberately, not by default.

Time the restore first — on a throwaway copy of the target — and let the
number decide:

```bash
# On the source, for each database:
pg_dump -Fc -d orders -f /tmp/orders.dump
ls -lh /tmp/orders.dump
```

A compressed dump restoring at roughly 20–50 MB/s of dump size is a usable
first estimate, but measure rather than trust that.

## Step 0 (both paths): declare the databases before you load them

Do **not** let `pg_restore` create roles and databases. pigsty-lite
provisions those declaratively, and anything it does not know about will
drift — on the next `make deploy` the HBA rules get rewritten without your
users in them, and the login you just restored stops working.

Put them in the response file first:

```yaml
postgres:
  databases:
    - name: orders
      owner: orders_app
    - name: billing
      owner: billing_app
  users:
    - name: orders_app
      password: "{{ vault_orders_app_password }}"
    - name: billing_app
      password: "{{ vault_billing_app_password }}"
  hba_rules:
    - db: orders
      user: orders_app
      source: 10.20.40.0/24
      method: scram-sha-256
    - db: billing
      user: billing_app
      source: 10.20.40.0/24
      method: scram-sha-256
  # Any extension the source databases use must be installed and created
  # here too, or the restore fails on the first object that needs it. A bare
  # string creates the extension in `postgres` only; name the application
  # database explicitly with `db`.
  extension_packages:
    - "pgvector_{{ postgres_version }}"
  extensions:
    - pg_stat_statements
    - {name: vector, db: orders}
```

Then:

```bash
make deploy
```

`./configure` generates only the fixed infrastructure secrets (Patroni,
HAProxy stats, Grafana admin) — see [`../secrets.md`](../secrets.md).
Application logins are yours to supply, so add those `vault_*` keys
before deploying. The response file holds the *reference*; the vault holds
the value:

```bash
ansible-vault edit inventory/group_vars/all/vault.yml
# vault_orders_app_password: <the password the application already uses>
# vault_billing_app_password: <...>
```

Reuse the source's existing passwords here and the applications need no
config change at cutover — only a new host.

Confirm the inventory of extensions on the source so nothing is missed:

```sql
-- on the source, per database
SELECT extname, extversion FROM pg_extension ORDER BY 1;
```

## Path A — dump and restore (recommended)

### A1. Connect to PostgreSQL directly, not through pgBouncer

pgBouncer runs in transaction pooling mode. A restore issues session-level
commands (`SET session_replication_role`, advisory locks, `ALTER TABLE` in
long transactions) that pooling either breaks or silently reinterprets.
Target port 5432 on the node, not 6432.

### A2. Stop writes on the source

Whatever that means for your application — scale deployments to zero, flip
the LB, revoke `CONNECT`. The point is that the dump is a point-in-time
snapshot and anything written after it is lost.

```sql
-- belt and braces, on the source
REVOKE CONNECT ON DATABASE orders FROM PUBLIC, orders_app;
SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'orders';
```

### A3. Dump

Custom format (`-Fc`), one file per database, so the restore can be
parallelised and re-run selectively:

```bash
pg_dump -Fc --no-owner --no-acl --no-publications --no-subscriptions \
        -h source.example.internal -U postgres -d orders \
        -f /var/tmp/orders.dump
```

`--no-owner --no-acl` because ownership and grants on the target belong to
the roles pigsty-lite created in step 0; restoring the source's versions
reintroduces roles that are not declared anywhere.

### A4. Restore

```bash
pg_restore --no-owner --no-acl --exit-on-error \
           --jobs 4 \
           -h pgnode01 -p 5432 -U postgres -d orders \
           /var/tmp/orders.dump
```

`--exit-on-error` matters. Without it `pg_restore` reports errors and
carries on, and a "successful" restore can be missing constraints or
indexes. If it stops, fix the cause and re-run rather than ignoring it.

`--jobs` only works with `-Fc`/`-Fd`, and should not exceed the node's core
count.

### A5. Reassign ownership

Because the dump was taken `--no-owner`, every object now belongs to the
role that ran the restore (`postgres`):

```sql
-- as postgres, in each restored database
REASSIGN OWNED BY postgres TO orders_app;
```

If the source used several owners per database, dump the grants separately
with `pg_dump --section=pre-data` and apply them by hand instead — but
prefer collapsing to the declared owner if the application does not need
the distinction.

### A6. Analyze

The planner has no statistics for the freshly loaded tables, and until it
does the first queries can pick catastrophic plans:

```bash
psql -h pgnode01 -U postgres -d orders -c 'ANALYZE;'
```

### A7. Verify before you point traffic at it

Row counts per table, compared against the source:

```sql
SELECT relname, n_live_tup
FROM pg_stat_user_tables
ORDER BY relname;
```

`n_live_tup` is an estimate; for a definitive check on the tables that
matter, `SELECT count(*)` on both sides. Also confirm object counts match:

```sql
SELECT count(*) FROM information_schema.tables  WHERE table_schema NOT IN ('pg_catalog','information_schema');
SELECT count(*) FROM pg_indexes WHERE schemaname NOT IN ('pg_catalog','information_schema');
SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
  WHERE n.nspname NOT IN ('pg_catalog','information_schema');
SELECT conname FROM pg_constraint WHERE NOT convalidated;  -- must be empty
```

### A8. Take a backup immediately

A SPOF node has no replica. Backups are the only recovery path, and the
freshly loaded data is not in one yet:

```bash
ansible backup_server -m command -b \
  -a 'pgbackrest --stanza=<cluster_name> --type=full backup'
```

Then confirm it is there:

```bash
ansible backup_server -m command -b -a 'pgbackrest --stanza=<cluster_name> info'
```

## Path B — logical replication (short cutover window)

Use when the Path A downtime is too long. The data syncs while the source
keeps serving; downtime is just the cutover.

### B1. Prepare the source

Requires a restart, so this is itself a (brief) maintenance event:

```ini
# postgresql.conf on the source
wal_level = logical
max_replication_slots = 10
max_wal_senders = 10
```

Every table to be replicated needs a replica identity. Tables with a
primary key already have one; for the rest:

```sql
ALTER TABLE events REPLICA IDENTITY FULL;
```

`REPLICA IDENTITY FULL` makes the subscriber match rows by comparing every
column, with no index. On a large table that is very slow. Prefer adding a
unique key.

### B2. Pre-load the schema

Logical replication copies rows, never DDL:

```bash
pg_dump --schema-only --no-owner --no-acl \
        -h source.example.internal -U postgres -d orders \
  | psql -h pgnode01 -p 5432 -U postgres -d orders
```

From here until cutover, **the source schema must not change**. A migration
deployed mid-sync will break replication with a column mismatch that is
awkward to recover from.

### B3. Publish and subscribe

```sql
-- on the source
CREATE PUBLICATION pigsty_migration FOR ALL TABLES;
```

```sql
-- on the target, in the matching database
CREATE SUBSCRIPTION pigsty_migration
  CONNECTION 'host=source.example.internal dbname=orders user=replicator password=... sslmode=require'
  PUBLICATION pigsty_migration;
```

The target's `wal_level = replica` (from the tuning profile) is correct —
a subscriber does not need `logical`. Only set `wal_level` higher, via
`postgres.extra_parameters`, if this node will itself publish later.

### B4. Wait for the initial sync

```sql
-- on the target: every row must reach 'r' (ready)
SELECT srrelid::regclass, srsubstate FROM pg_subscription_rel;

-- on the source: lag must settle near zero
SELECT slot_name, confirmed_flush_lsn, pg_current_wal_lsn(),
       pg_wal_lsn_diff(pg_current_wal_lsn(), confirmed_flush_lsn) AS lag_bytes
FROM pg_replication_slots;
```

### B5. Cut over

1. Stop writes on the source.
2. Wait for `lag_bytes` to reach 0.
3. **Advance the sequences** — this is the step that most often gets
   forgotten, and it silently corrupts data afterwards with duplicate-key
   errors on the first insert:

   ```sql
   -- on the SOURCE, generate the statements
   SELECT format('SELECT setval(%L, %s);',
                 format('%I.%I', schemaname, sequencename),
                 last_value)
   FROM pg_sequences
   WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
     AND last_value IS NOT NULL;
   ```

   A sequence with `last_value IS NULL` has never been used, so the target's
   fresh sequence is already correct and needs no `setval`.

   Run the generated statements on the target.

4. Drop the subscription, then point the application at the new node:

   ```sql
   -- on the target
   DROP SUBSCRIPTION pigsty_migration;
   ```

5. `ANALYZE`, verify as in A7, and take the full backup from A8.

Also check for anything logical replication skipped: large objects, and any
table created after the publication if you did not use `FOR ALL TABLES`.

## What to watch after cutover

Grafana is already collecting from the node. The panels worth checking on
day one:

- **pg_stat_database** — connection count against `max_connections`, and
  rollback ratio.
- **Replication slots** — after Path B, confirm no orphaned slot is pinning
  WAL on either side. A forgotten slot fills the disk.
- **pgBackRest** — the exporter reports last-backup age. On a SPOF node
  this is the metric that matters most; alert on it.

```sql
-- orphaned slots pin WAL forever; this must be empty after cutover
SELECT slot_name, active, wal_status,
       pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn))
FROM pg_replication_slots;
```

## A note on SPOF

`spof` is one node. No replica, no HAProxy (`playbooks/site.yml` skips it
for this profile), no VIP to move. If these databases are production and
their loss would matter, the `ha` profile is the configuration that
survives losing the node — the response file converts by changing
`profile:` and adding two `pg_replica` entries under `nodes:`. Migrating
into SPOF and scaling out later is a second migration; deciding now is
cheaper.

## See also

- [`firstrun.md`](firstrun.md) — standing up the target in the first place
- [`day2-provisioning.md`](day2-provisioning.md) — the declarative
  database/user/HBA workflow referenced in step 0
- [`day2-backups.md`](day2-backups.md) — pgBackRest schedules and PITR
- [`../../examples/single-node/`](../../examples/single-node/) — a worked
  SPOF response file carrying two application databases
