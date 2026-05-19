# Troubleshooting

Operator-facing diagnostic commands for the distributed components in
pigsty-lite. Each section starts with a one-shot "is this healthy?"
block, then drills into common failure modes that have actually occurred
during development. Copy commands verbatim.

Conventions:

- `${CLUSTER}` is the cluster name (`cluster_name`, also `patroni_scope`).
- `${HOST}` is the FQDN or IP of the host you're diagnosing from.
- TLS material lives at `/etc/pki/pigsty/` — CA at `ca.crt`, per-host
  cert/key at `<inventory_hostname>.crt|.key`. Commands hard-code this
  path; change it if your deploy overrides `pigsty_pki_dir`.
- All commands assume the operator is `root` (or prefixed with `sudo`).

Components covered: [etcd](#etcd), [Patroni](#patroni),
[HAProxy](#haproxy), [vip-manager](#vip-manager), [pgBackRest](#pgbackrest).

---

## etcd

Patroni's source of truth. If etcd is unhealthy, every dependent
component (Patroni, vip-manager, monitoring agents that watch DCS) will
misbehave.

### Health check

```bash
# Service status on each member.
systemctl status etcd

# Cluster-wide health (run from any member).
etcdctl --endpoints=https://${HOST}:2379 \
  --cacert=/etc/pki/pigsty/ca.crt --cert=/etc/pki/pigsty/$(hostname).crt --key=/etc/pki/pigsty/$(hostname).key \
  endpoint health --cluster

# Cluster membership and leader.
etcdctl --endpoints=https://${HOST}:2379 \
  --cacert=/etc/pki/pigsty/ca.crt --cert=/etc/pki/pigsty/$(hostname).crt --key=/etc/pki/pigsty/$(hostname).key \
  endpoint status --cluster -w table
```

A healthy cluster reports one `IS LEADER=true` and the rest `false`,
all `errors=` empty, and an `RAFT TERM` that matches across members.

### Inspect Patroni's keys in etcd

```bash
etcdctl --endpoints=https://${HOST}:2379 \
  --cacert=/etc/pki/pigsty/ca.crt --cert=/etc/pki/pigsty/$(hostname).crt --key=/etc/pki/pigsty/$(hostname).key \
  get --prefix / --keys-only

# Read the leader key directly.
etcdctl --endpoints=https://${HOST}:2379 \
  --cacert=/etc/pki/pigsty/ca.crt --cert=/etc/pki/pigsty/$(hostname).crt --key=/etc/pki/pigsty/$(hostname).key \
  get /${CLUSTER}/${CLUSTER}/leader
```

The actual leader-key path is `<patroni_namespace><patroni_scope>/leader`.
With the defaults that's `/${CLUSTER}/${CLUSTER}/leader`.

### Logs

```bash
journalctl -u etcd -n 200 --no-pager
journalctl -u etcd --since "10 min ago" -p err --no-pager
```

### Common failures

| Symptom | Likely cause | Action |
| --- | --- | --- |
| `etcdctl` returns `context deadline exceeded` | All endpoints unreachable; check firewalld (port 2379) and TLS chain. | `firewall-cmd --list-all`, `openssl s_client -connect ${HOST}:2379 -CAfile /etc/pki/pigsty/ca.crt` |
| `member already exists` on startup | Stale data dir after a member ID changed. | Stop etcd, `rm -rf /var/lib/etcd/*`, restart and re-join. |
| `request cluster ID mismatch` | A peer rejoined with a different cluster ID (e.g. after a wipe). | Wipe the offending member's data dir and let it re-bootstrap. |
| `leader changed` storm | Disk latency, clock skew, or network jitter. | `iostat -x 1`, `chronyc tracking`, check `dmesg` for soft lockups. |

---

## Patroni

PostgreSQL HA controller. Owns the Postgres lifecycle on every node.

### Health check

```bash
# On any postgres host.
patronictl -c /etc/patroni/patroni.yml list

# Same data via REST (works without psql).
curl -sk --cacert /etc/pki/pigsty/ca.crt https://${HOST}:8008/cluster | jq

# Per-node role.
curl -sk --cacert /etc/pki/pigsty/ca.crt -o /dev/null -w "%{http_code}\n" \
  https://${HOST}:8008/leader        # 200 on leader, 503 elsewhere
curl -sk --cacert /etc/pki/pigsty/ca.crt -o /dev/null -w "%{http_code}\n" \
  https://${HOST}:8008/replica       # 200 on a healthy replica
curl -sk --cacert /etc/pki/pigsty/ca.crt -o /dev/null -w "%{http_code}\n" \
  https://${HOST}:8008/read-only     # 200 on primary OR a healthy replica
```

A healthy cluster: one `Leader` with `State=running`, rest `Replica` with
`State=streaming`, `Lag=0`. The `TL` (timeline) matches across members.

### Inspect Patroni configuration (dynamic vs static)

```bash
# Static: file on disk.
cat /etc/patroni/patroni.yml

# Dynamic: stored in DCS (etcd). This is what's actually live.
curl -sk --cacert /etc/pki/pigsty/ca.crt https://${HOST}:8008/config | jq
```

Anything inside `bootstrap.dcs` in the static file is only consulted at
first bootstrap. Live changes go via `PATCH /config`.

### Logs

```bash
journalctl -u patroni -n 200 --no-pager
tail -F /var/log/patroni/patroni.log

# PostgreSQL log paths (Patroni owns the lifecycle).
ls /var/lib/pgsql/18/data/log/
tail -F /var/lib/pgsql/18/data/log/postgresql-*.log
```

### Manual switchover or failover

```bash
# Graceful switchover (planned, primary cooperates).
patronictl -c /etc/patroni/patroni.yml switchover \
  --leader <current-leader> --candidate <target> --force

# Forced failover (use when the current leader is unreachable).
patronictl -c /etc/patroni/patroni.yml failover \
  --candidate <target> --force

# Reinit a replica that's drifted (destructive on that node only).
patronictl -c /etc/patroni/patroni.yml reinit ${CLUSTER} <replica-name> --force
```

### Common failures

| Symptom | Likely cause | Action |
| --- | --- | --- |
| No leader elected; all members `replica` | etcd unreachable, or quorum lost. Patroni refuses to promote without DCS consensus. | Fix etcd first; check `journalctl -u patroni` for "DCS is not accessible". |
| Replica stuck `creating replica` | `pg_basebackup` from leader failed (auth, disk, TLS). | Inspect `journalctl -u patroni` on the replica; check `replication_password`, primary's `pg_hba.conf`. |
| Replica `streaming` but `Lag` grows | Replica I/O bound, or `wal_keep_size` too small and replica fell behind. | `iostat -x 1`, raise `wal_keep_size`, or `patronictl reinit`. |
| REST returns 503 on all members for `/leader` | No node is the leader (election in progress, or quorum lost). | `patronictl list` to see state; check etcd health. |
| REST returns 503 on `/leader` from the leader itself, but `GET /cluster` shows it as Leader | Patroni considers itself unsafe (e.g. failed sync standby check). | `curl https://${HOST}:8008/patroni \| jq` shows the unsafe reason. |

---

## HAProxy

Per-host L4 router in front of Patroni. Listens on 5433 (primary) and
5434 (replica), backends are pgbouncer (default) or postgres direct.

### Health check

```bash
systemctl status haproxy

# Stats CSV via the loopback stats endpoint.
curl -s -u "pigsty:$(grep '^haproxy_stats_password' \
    /etc/ansible-vault/... 2>/dev/null || echo 'haproxy-dev-stats-change-me')" \
  "http://127.0.0.1:7000/;csv" | column -t -s,

# Cleaner view: just backend states.
curl -s -u "pigsty:haproxy-dev-stats-change-me" "http://127.0.0.1:7000/;csv" \
  | awk -F, 'NR==1 || /^pg-(default|primary|replica)/{print $1,$2,$18,$37,$38}' OFS=,
```

For each backend (pg-default / pg-primary / pg-replica), the leader's
row should show `status=UP, check_status=L7OK, check_code=200`; the
replicas' rows should show `status=DOWN, check_status=L7STS,
check_code=503` for pg-primary and the inverse for pg-replica. That's
correct behavior, not a fault.

### End-to-end probe

```bash
# Primary path (port 5433 must reach a primary).
psql "host=127.0.0.2 port=5433 dbname=postgres user=postgres password=<pw> sslmode=disable" \
  -tAc "select inet_server_addr(), pg_is_in_recovery()"

# Replica path (port 5434 must reach a replica).
psql "host=127.0.0.2 port=5434 dbname=postgres user=postgres password=<pw> sslmode=disable" \
  -tAc "select inet_server_addr(), pg_is_in_recovery()"
```

If vip-manager is enabled, also probe via the VIP — the result must be
identical regardless of which host you dial from:

```bash
psql "host=<vip> port=5433 ..." -tAc "select pg_is_in_recovery()"
```

### Logs

```bash
journalctl -u haproxy -n 200 --no-pager
tail -F /var/log/haproxy.log   # if rsyslog forwards local2.*
```

Watch for `Layer7 wrong status, code: 503` — that means the L7 health
check (`http-check expect status 200` against `/leader` or `/replica`)
got a 503 from Patroni, which is **expected** for replicas being checked
against `/leader` and vice versa.

### Common failures

| Symptom | Likely cause | Action |
| --- | --- | --- |
| `psql ... server closed the connection unexpectedly` via 5433 / 5434, but stats show backends `UP` | HAProxy can connect to backend's L7 check port (8008) but the data-plane port (6432 or 5432) is blocked on the backend host. | Check the backend host's `firewall-cmd --list-all`. The haproxy role opens this port from postgres peers; verify the rule landed. |
| All backends `DOWN` with `Layer7 wrong status, code: 503` everywhere — including on the actual leader | Patroni REST is up but the L7 check URL/method is wrong, or Patroni considers every node unsafe. | `curl -k -X OPTIONS https://${HOST}:8008/leader` from a peer; should return 200 on the leader, 503 elsewhere. |
| All backends `DOWN` with `Layer7 invalid response` | TLS handshake to Patroni REST failing (cert path, CA, hostname). | `openssl s_client -connect ${HOST}:8008 -CAfile /etc/pki/pigsty/ca.crt`. |
| HAProxy fails to bind to a VIP IP | `net.ipv4.ip_nonlocal_bind` not set (vip-manager hasn't parked the IP yet, but HAProxy must be able to bind anyway). | `sysctl net.ipv4.ip_nonlocal_bind` should be `1`; reload `/etc/sysctl.d/90-pigsty-lite-haproxy-vip.conf`. |

---

## vip-manager

Parks a single virtual IP on whichever node currently holds Patroni's
leader key. Runs on every postgres node; only one owns the VIP at a time.

### Health check

```bash
systemctl status vip-manager
cat /etc/vip-manager/vip-manager.yml | grep -E 'trigger-key|trigger-value|ip|interface'

# Who owns the VIP right now?
for h in <host1> <host2> <host3>; do
  echo "=== $h ==="
  ssh $h "ip -4 addr show | grep -E '\<<vip-ip>\>/[0-9]+' && echo HOLDS || echo no"
done
```

Exactly one node should report `HOLDS`. That node should also be the
Patroni leader — cross-check with `patronictl list`.

### Trigger key alignment

`trigger-key` in the config must exactly match the etcd key Patroni
writes for the leader. With the defaults:

```
trigger-key = ${patroni_namespace}${patroni_scope}/leader
            = /${CLUSTER}/${CLUSTER}/leader
```

Read both to confirm they match:

```bash
grep trigger-key /etc/vip-manager/vip-manager.yml

etcdctl --endpoints=https://${HOST}:2379 \
  --cacert=/etc/pki/pigsty/ca.crt --cert=/etc/pki/pigsty/$(hostname).crt --key=/etc/pki/pigsty/$(hostname).key \
  get /${CLUSTER}/${CLUSTER}/leader
```

The etcd value (the leader hostname) must match exactly one node's
`trigger-value` — otherwise no node holds the VIP.

### Logs

```bash
journalctl -u vip-manager -n 100 --no-pager
```

Healthy steady state on the leader:

```
INFO  IP address <vip>/<prefix> is up, must be up
```

On replicas:

```
INFO  IP address <vip>/<prefix> is down, must be down
```

### Common failures

| Symptom | Likely cause | Action |
| --- | --- | --- |
| `Failed to initialize leader checker: cannot load CA file: open /path/to/etcd/trusted/ca/file` | vip-manager is reading its built-in defaults — your config file isn't where the systemd unit expects. | Confirm `ExecStart=...--config=/etc/vip-manager/vip-manager.yml` matches the rendered path. |
| All nodes log `must be down` | `trigger-key` points at the wrong path, or `trigger-value` doesn't match any node's identity. | See "Trigger key alignment" above. |
| Two nodes hold the VIP briefly during failover | Expected — IP migration is not atomic. Connections see RSTs for a few seconds. | If unacceptable, shorten Patroni's `ttl` and vip-manager's `interval`. |
| VIP holder is correct but clients can't connect to it | `net.ipv4.ip_nonlocal_bind` not set on the VIP holder, so HAProxy on that node can't accept on the VIP. | `sysctl net.ipv4.ip_nonlocal_bind=1`. |

---

## pgBackRest

WAL archiving + base backups. Two-host topology: clients on every
postgres node, server on `backup_server`. Both sides run a pgbackrest
TLS daemon on port 8432.

### Health check

```bash
# On the backup_server.
sudo -u postgres pgbackrest --stanza=${CLUSTER} info

# Healthy output: a 'status: code 0' line and at least one backup OR
# the message 'no valid backups' (immediately after stanza-create,
# before the first --type=full backup).

# On any postgres node, confirm the role wiring + archive_command.
psql -U postgres -tAc "SHOW archive_command"
# Must contain 'pgbackrest archive-push', not the bootstrap placeholder.
```

### Inspect archived WAL

```bash
# On the backup_server.
ls /var/lib/pgbackrest/archive/${CLUSTER}/
cat /var/lib/pgbackrest/archive/${CLUSTER}/archive.info

# WAL push lag (must be small under sustained writes).
psql -U postgres -tAc "
  SELECT now() - last_archived_time AS lag,
         last_archived_wal, last_failed_wal, last_failed_time
  FROM pg_stat_archiver;
"
```

### Force a backup or stanza check

```bash
# On the backup_server (full backup).
sudo -u postgres pgbackrest --stanza=${CLUSTER} --type=full backup

# Stanza health check (validates archive_command is wired correctly).
sudo -u postgres pgbackrest --stanza=${CLUSTER} check
```

### Logs

```bash
tail -F /var/log/pgbackrest/${CLUSTER}-*.log

# pgbackrest TLS daemon (server-side).
journalctl -u pgbackrest -n 100 --no-pager

# Per-host pgbackrest is invoked by archive_command from inside Postgres,
# so failures also surface in the PG log.
tail -F /var/lib/pgsql/18/data/log/postgresql-*.log | grep -i archive
```

### Common failures

| Symptom | Likely cause | Action |
| --- | --- | --- |
| `stanza-create` errors `056: unable to find primary cluster` | Patroni hasn't elected a leader yet (race during bootstrap). | The role passes `--no-online` to dodge this; if you see it anyway, check Patroni health first. |
| `archive-push` errors `connection failed: connection refused` on port 8432 | Firewall blocks the peer source; or the pgbackrest TLS daemon isn't running. | `systemctl status pgbackrest`, `firewall-cmd --list-rich-rules`, `nc -z <backup_server> 8432`. |
| `archive-push` errors `unable to load certificate` | Per-host cert missing or wrong CA. | `ls -l /etc/pki/pigsty/$(hostname).crt`, `openssl verify -CAfile /etc/pki/pigsty/ca.crt /etc/pki/pigsty/$(hostname).crt`. |
| `pg_stat_archiver.last_failed_wal` populated | Recent archive-push failed; look at `last_failed_time` then `/var/log/pgbackrest/*.log` around that time. | Fix the underlying issue, run `pgbackrest check` to confirm green. |
| `pgbackrest check` errors `error 068: HINT: archive-push command not enabled` | `archive_command` doesn't reference pgbackrest. | PATCH Patroni dynamic config: `archive_command: 'pgbackrest --stanza=${CLUSTER} archive-push %p'`. The role does this in `_archive.yml`. |
| `backup` errors `unable to find primary cluster` | Same as stanza-create — Patroni had no leader at runtime. | This time the online path is required; resolve Patroni first, then retry. |

---

## Cross-cutting tips

### See everything systemd manages for this cluster

```bash
systemctl list-units --no-pager --no-legend \
  --type=service 'etcd*' 'patroni*' 'pgbouncer*' 'haproxy*' 'vip-manager*' 'pgbackrest*'
```

### Tail every relevant log on one screen

```bash
journalctl -fu etcd -u patroni -u pgbouncer -u haproxy -u vip-manager -u pgbackrest
```

### Capture a full bug snapshot

```bash
mkdir -p /tmp/pigsty-lite-snapshot && cd /tmp/pigsty-lite-snapshot
patronictl -c /etc/patroni/patroni.yml list > patronictl.txt 2>&1
curl -sk --cacert /etc/pki/pigsty/ca.crt https://localhost:8008/cluster > patroni-cluster.json
curl -sk --cacert /etc/pki/pigsty/ca.crt https://localhost:8008/config > patroni-config.json
etcdctl --endpoints=https://$(hostname):2379 \
  --cacert=/etc/pki/pigsty/ca.crt --cert=/etc/pki/pigsty/$(hostname).crt --key=/etc/pki/pigsty/$(hostname).key \
  get --prefix / -w json > etcd-dump.json
curl -s -u "pigsty:haproxy-dev-stats-change-me" "http://127.0.0.1:7000/;csv" > haproxy-stats.csv
for u in etcd patroni pgbouncer haproxy vip-manager pgbackrest; do
  journalctl -u $u -n 500 --no-pager > journal-$u.txt 2>&1
done
ss -tlnp > listeners.txt
firewall-cmd --list-all > firewall.txt
tar czf /tmp/pigsty-lite-snapshot.tgz -C /tmp pigsty-lite-snapshot
```
