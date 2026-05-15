# Major PostgreSQL upgrade

Major upgrades (for example, `17 -> 18`) change on-disk format.
pigsty-lite ships **no playbook** for this; it is a DBA-supervised
operation.

## Path A — logical replication cutover

1. Stand up a new cluster at the target major.
2. Create publication on old primary:
   `CREATE PUBLICATION pgupgrade FOR ALL TABLES;`
3. Copy schema from old to new.
4. Create subscription on new primary.
5. Wait for sync and catch-up.
6. Stop writes on old, cut traffic to new.
7. Decommission old cluster after validation.

## Path B — `pg_upgrade` in place

1. Take and verify a fresh backup.
2. Install new-major packages alongside old.
3. Stop cluster (Patroni paused).
4. Run `pg_upgrade --check`.
5. Run `pg_upgrade` (prefer `--link` when valid).
6. Rebuild replicas from upgraded primary.
7. Update response `postgres.version` and run `make deploy`.
8. Resume Patroni and verify with `patronictl list`.

## Why no playbook

Major upgrades involve cluster-specific decisions (extension
compatibility, downtime, rollback posture, logical vs. pg_upgrade), so
pigsty-lite documents the runbook and keeps the operator in the loop.
