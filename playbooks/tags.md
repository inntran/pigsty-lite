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
