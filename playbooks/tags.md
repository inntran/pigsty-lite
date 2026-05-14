# Tag reference

## Module tags

- `preflight`
- `ca`
- `node`
- `repos`
- `certs`
- `etcd`
- `postgres`
- `patroni`
- `pgbouncer`
- `haproxy`
- `vip_manager`
- `provision`

## Action tags (used inside roles in later sub-plans)

- `install`
- `config`
- `restart`
- `provision`
- `firewall`
- `service`
- `assert`
- `selinux`

## Examples

- `--tags preflight` - only run the preflight role.
- `--tags ca` - only (re)generate the CA on localhost.
- `--tags etcd` - install/configure the etcd cluster.
- `--tags postgres` - install PG, prepare fs, mask vendor unit.
- `--tags patroni` - configure and start Patroni; safe re-run.
- `--tags patroni,config` - render patroni.yml without restart; handlers still flush if files changed.
- `--tags pgbouncer` - reconfigure pgBouncer (reload, not restart).
- `--tags haproxy` - reconfigure HAProxy (reload). Use `haproxy,restart` to bounce the service.
- `--tags vip_manager` - re-render vip-manager config and restart (only when enabled).
- `--tags provision` - re-apply HBA / users / dbs / extensions on the leader.
- `--tags provision,hba` - re-render pg_hba.conf only.
- `--tags provision,users` - reconcile roles only.
