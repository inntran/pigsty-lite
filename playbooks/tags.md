# Tag reference

## Module tags

- `preflight`
- `ca`
- `node`
- `repos`
- `certs`
- `etcd`

## Action tags (used inside roles in later sub-plans)

- `install`
- `config`
- `restart`
- `provision`

## Examples

- `--tags preflight` - only run the preflight role.
- `--tags ca` - only (re)generate the CA on localhost.
- `--tags etcd` - install/configure the etcd cluster.
