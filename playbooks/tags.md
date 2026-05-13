# Tag reference

## Module tags (in P0)

- `preflight`
- `ca`
- `node`
- `repos`
- `certs`

## Action tags (used inside roles in later sub-plans)

- `install`
- `config`
- `restart`
- `provision`

## Examples

- `--tags preflight` - only run the preflight role.
- `--tags ca` - only (re)generate the CA on localhost.
