# repos

Manages dnf repositories: PGDG (default), vendor (always), EPEL (opt-in),
pigsty (opt-in).

## Inputs

- `repos_pgdg_enabled` (bool, default true)
- `repos_epel_enabled` (bool, default false)
- `repos_pigsty_enabled` (bool, default false)
- `repos_pigsty_packages` (list, default `[]`) - only install pigsty packages
  when this is non-empty. The actual `dnf install` happens in dependent roles.

## Tags

None.
