# Lifecycle operations

Day-2 cluster operations are driven by dedicated operator-entry
playbooks, wrapped by `make` targets. None of these are part of
`site.yml` / `make deploy`; they are run on demand.

All five accept `-e auto_confirm=true` to skip the interactive
confirmation prompt.

## Switchover — `make switchover`

Controlled, planned handover of the leader role to a replica. The
current leader must be healthy. Patroni performs the demote/promote.

- Picks the first replica as the candidate, or pass `-e candidate=<host>`.
- Preconditions checked: one leader, all members running, replication
  lag within 1 MiB.
- Brief interruption of in-flight sessions; clients reconnect through
  HAProxy to the new leader.

## Failover — `make failover CANDIDATE=<host>`

Unplanned promotion, for when the leader is unhealthy or gone. Unlike
switchover, a candidate **must** be named. Patroni's `/failover` does
not require the old leader to be reachable.

## Minor upgrade — `make minor-upgrade`

Rolling same-major PostgreSQL upgrade (for example, `18.3 -> 18.4`):

1. Refuses if `postgres.pin_version` targets a different major.
2. Refuses if no successful backup exists within
   `postgres.minor_upgrade.require_recent_backup_hours` (default 24).
3. Upgrades replicas one at a time.
4. Switches over off the original primary.
5. Upgrades the demoted old primary.
6. Verifies cluster health.

Pin a specific minor with `postgres.pin_version: "18.4"` in the
response file; without a pin, `dnf` takes the latest available.

## Scale add replica — `make scale-add-replica HOST=<host>`

Adds a new replica. **Prerequisite:** host is already in inventory with
`postgres_role: pg_replica`, and `./configure -s -f ...` has been
rerun.

After it finishes, run `make deploy` once so backup-store keys and
monitoring scrape config include the new host.

## Scale remove replica — `make scale-remove-replica HOST=<host>`

Decommissions a replica: stops Patroni on it, removes it from Patroni
DCS, and stops its services. It refuses to remove the current leader.

After it finishes, remove the host from inventory and rerun
`./configure -s -f ...`.

## Manual libvirt validation

Lifecycle playbooks require a real multi-node cluster. Suggested checks
on an `HA` libvirt cluster:

1. `make switchover`
2. `make failover CANDIDATE=<replica>`
3. `make minor-upgrade`
4. `make scale-add-replica HOST=<new-node>`
5. `make scale-remove-replica HOST=<new-node>`
