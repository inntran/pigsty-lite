# Testing commands

These are the commands used during P3 verification.

## Unit tests

```bash
PYTHONPATH=. UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/configure -v
```

## Targeted P3 lint

```bash
yamllint roles/provision playbooks/_provision.yml playbooks/site.yml tests/molecule/provision .github/workflows/molecule.yml responses/single.rsp.yml.example responses/ha.rsp.yml.example
```

```bash
ANSIBLE_LOCAL_TEMP=/tmp/ansible-local ansible-lint roles/provision playbooks/_provision.yml tests/molecule/provision
```

```bash
markdownlint-cli2 roles/provision/README.md docs/operations/firstrun.md docs/operations/day2-provisioning.md README.md
```

## Python / shell / XML lint

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run ruff check .
UV_CACHE_DIR=/tmp/uv-cache uv run ruff format --check .
```

```bash
files=$(find bin -path '*/__pycache__' -prune -o -type f -not -name '*.py' -not -name '_*.py' -print 2>/dev/null)
if [ -n "$files" ]; then shellcheck $files; fi
```

```bash
if compgen -G "files/firewalld/services/*.xml" > /dev/null; then xmllint --noout files/firewalld/services/*.xml; fi
```

## Full lint

```bash
ANSIBLE_LOCAL_TEMP=/tmp/ansible-local UV_CACHE_DIR=/tmp/uv-cache PYTHONPATH=. make lint
```

At the time P3 was implemented, full `make lint` was blocked by existing
unrelated ansible-lint findings in haproxy, pgbouncer, and vip_manager
Molecule files. Full markdown lint also included the untracked
`docs/comparison.md`.

## Molecule

The provision scenarios use Podman. With ansible-core 2.20 and the current
Molecule Podman plugin, the compatibility flag below is required because the
plugin ships a string-valued conditional.

```bash
cd tests/molecule/provision
ANSIBLE_ALLOW_BROKEN_CONDITIONALS=true molecule test -s default
```

```bash
cd tests/molecule/provision
ANSIBLE_ALLOW_BROKEN_CONDITIONALS=true molecule test -s ha
```

The `default` scenario passed end-to-end during P3 verification, including
idempotence. The `ha` scenario was blocked before reaching the provision role by
the shared etcd prepare stack in this local Podman environment.
