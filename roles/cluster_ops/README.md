# cluster_ops

A library role for the P6 lifecycle playbooks. It is **never run as a
whole role**; `tasks/main.yml` is intentionally empty and playbooks
include specific task files via `include_role` + `tasks_from:`.

## Included task files

### `find_leader.yml`

Queries any reachable Patroni REST `/cluster` endpoint.

**Sets facts:**
- `cluster_ops_leader_host` — inventory hostname of the current leader.
- `cluster_ops_replica_hosts` — list of replica inventory hostnames.
- `cluster_ops_member_count` — total members reported by Patroni.

### `assert_healthy.yml`

Asserts that:
- there is exactly one leader,
- every member reports `state: running`,
- each replica is under `cluster_ops_replication_lag_max_bytes`.

### `assert_recent_backup.yml`

Runs `pgbackrest info --output=json` on the leader and asserts the most
recent successful backup is within `cluster_ops_backup_max_age_hours`.

### `wait_member_converged.yml`

Given `cluster_ops_target_member`, polls Patroni REST until the member
is running and (if replica) lag is below the configured threshold.
