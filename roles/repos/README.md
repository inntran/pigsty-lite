# repos

Manages dnf repositories: PGDG (default), vendor (always), EPEL installed but
disabled for normal dependency resolution, and pigsty (opt-in).

## Inputs

- `repos_pgdg_enabled` (bool, default true)
- `repos_epel_enabled` (bool, default true) - installs `epel-release` and then
  leaves the `epel` repo disabled; package tasks that need EPEL opt in with
  `enablerepo`.
- `repos_epel_repo_file` (path, default `/etc/yum.repos.d/epel.repo`)
- `repos_epel_repo_id` (string, default `epel`)
- `repos_pigsty_enabled` (bool, default false)
- `repos_pigsty_packages` (list, default `[]`) - only install pigsty packages
  when this is non-empty. The actual `dnf install` happens in dependent roles.

## Tags

None.
