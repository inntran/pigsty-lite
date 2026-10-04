# repos

Manages dnf repositories: PGDG (default), vendor (always), EPEL (always
installed; disabled for normal dependency resolution by default), CRB (best-effort
on RHEL), and pigsty (opt-in).

## Inputs

- `repos_pgdg_enabled` (bool, default true)
- `repos_epel_enabled` (bool, default false) - selects EPEL's terminal state.
  `epel-release` is installed unconditionally (roles such as patroni,
  pgbackrest and monitoring need EPEL packages and opt in via `enablerepo`).
  CRB is best-effort on RHEL: an already-enabled repo id matching
  `codeready`/`crb` (e.g. an internal mirror) is accepted, otherwise
  `subscription-manager` is tried and a failure only warns. The tested package
  set resolved on EL10 without CRB. When `false`, EPEL is disabled for normal
  dependency resolution; when `true`, EPEL is left enabled.
- `repos_epel_repo_id` (string, default `epel`)
- `repos_pigsty_enabled` (bool, default false)
- `repos_pigsty_packages` (list, default `[]`) - only install pigsty packages
  when this is non-empty. The actual `dnf install` happens in dependent roles.

## What PGDG actually ships

[`docs/reference/pgdg-packages.md`](../../docs/reference/pgdg-packages.md) is a
snapshot of every package in the PGDG repos this role configures. Consult it
before reaching for EPEL, the pigsty repo, or an upstream tarball -- PGDG
carries more than it looks like (`patroni`, `pgbackrest`, `pgbouncer`,
`pgexporter`).

Regenerate with `./bin/snapshot_pgdg_packages.py`.

## Tags

None.
