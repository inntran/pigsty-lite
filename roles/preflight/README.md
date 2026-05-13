# preflight

Validates a host before pigsty-lite changes anything. Fails fast on missing
prerequisites; warns (does not fail) on storage-layout concerns.

## Inputs

- `preflight_required_os_family` (default: `RedHat`)
- `preflight_required_os_major` (default: `10`)
- `preflight_required_packages` (default: see `defaults/main.yml`)
- `preflight_required_selinux_mode` (default: `Enforcing`)

## Outputs

- `preflight_passed` fact set to `true` on success.

## Tags

None. The role's tasks run unconditionally when the role is included.
