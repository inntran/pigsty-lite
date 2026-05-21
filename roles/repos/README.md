# repos

Manages dnf repositories: PGDG (default), vendor (always), EPEL and CRB
(always installed; disabled for normal dependency resolution by default),
and pigsty (opt-in).

## Inputs

- `repos_pgdg_enabled` (bool, default true)
- `repos_epel_enabled` (bool, default false) - selects EPEL's terminal state.
  `epel-release` and CRB are installed unconditionally (roles such as patroni,
  pgbackrest and monitoring need EPEL packages and opt in via `enablerepo`).
  When `false`, EPEL is disabled for normal dependency resolution; when `true`,
  EPEL is left enabled.
- `repos_epel_repo_id` (string, default `epel`)
- `repos_pigsty_enabled` (bool, default false)
- `repos_pigsty_packages` (list, default `[]`) - only install pigsty packages
  when this is non-empty. The actual `dnf install` happens in dependent roles.

## Tags

None.
