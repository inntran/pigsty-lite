# P0 — Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the pigsty-lite repository scaffolding and the cross-cutting roles every later sub-plan depends on: `preflight`, `repos`, `node`, `ca`, `certs`. At the end of P0, `make plan` on a single-host inventory produces a clean `--check --diff` output and `make deploy` against a real RHEL 10 host completes successfully with an installed PGDG repo, hardened OS, baseline firewalld, and a self-signed CA distributing certs.

**Architecture:** A standard Ansible repo (Approach B from the spec) rooted at the project top level. Roles are thin and use `community.postgresql`, `community.crypto`, `community.general`, and `ansible.posix` from Galaxy. A Python 3 `configure` script (stdlib only) translates an Oracle-style response file into both an inventory and a generated `group_vars/response.yml`. A `Makefile` wraps the common operator commands. SELinux stays enforcing; firewalld stays in charge; no shell scripts that aren't tested. Every change is TDD-driven via Molecule with the podman driver (P0 roles don't need libvirt).

**Tech Stack:** Ansible ≥2.16, Python 3.11+ (control node), RHEL 10 / Rocky 10 / Alma 10 (managed hosts), Molecule + podman for role tests, yamllint + ansible-lint + ruff + shellcheck + xmllint for lint, GitHub Actions for CI Layer 1 + 2 only.

---

## File Structure

Files created or modified in P0, with responsibility per file. Listed in dependency order so a reader can hold the structure in mind.

| Path | Responsibility |
|---|---|
| `ansible.cfg` | Default forks, pipelining, inventory, callback plugins, retry-files-disabled, host_key_checking knob |
| `requirements.yml` | Pinned upstream Galaxy collections (`community.postgresql`, `community.crypto`, `community.general`, `ansible.posix`, `victoriametrics.cluster`, `grafana.grafana`, `community.grafana`) |
| `Makefile` | Operator entry points: `configure`, `plan`, `deploy`, `lint`, `test-role`, `clean`. P0 implements `configure`, `plan`, `deploy`, `lint`, `test-role`, `clean` only |
| `configure` | Python 3 stdlib script. Modes: interactive wizard, `-c {single,ha}` preset, `-s -f file.yml` silent, `--validate file.yml` schema check. Reads response file, emits `inventory/site.yml` + `group_vars/response.yml` |
| `bin/_response_schema.py` | JSON Schema (Python dict) for the response file. Imported by `configure` |
| `bin/_generate_inventory.py` | Pure function: takes a validated response dict, returns inventory YAML string. Imported by `configure` |
| `bin/_generate_response_vars.py` | Pure function: takes a validated response dict, returns `group_vars/response.yml` content |
| `responses/single.rsp.yml.example` | Commented template for the `single` profile |
| `responses/ha.rsp.yml.example` | Commented template for the `ha` profile |
| `inventory/examples/single.yml` | Committed example inventory matching `single.rsp.yml.example` |
| `inventory/examples/ha.yml` | Committed example inventory matching `ha.rsp.yml.example` |
| `group_vars/all.yml` | Cross-role coordination: shared paths, ports, version pins, repo policy, `cluster_*` and `operator_*` defaults |
| `group_vars/postgres.yml` | Defaults shared by postgres-group roles (used in P2; placeholder in P0 with just file present) |
| `group_vars/etcd.yml` | Defaults shared by etcd-group roles (used in P1; placeholder in P0 with just file present) |
| `group_vars/monitor.yml` | Defaults for monitor group (used in P5; placeholder in P0 with just file present) |
| `group_vars/backup_store.yml` | Defaults for backup_store group (used in P4; placeholder in P0 with just file present) |
| `playbooks/site.yml` | Top-level orchestrator. In P0 it imports only P0 plays |
| `playbooks/_preflight.yml` | All hosts; runs `preflight` role |
| `playbooks/_ca.yml` | Localhost only; runs `ca` role |
| `playbooks/_node.yml` | All hosts; runs `repos`, `node`, `certs` roles in that order |
| `playbooks/preflight.yml` | Operator alias for `_preflight.yml`; identical content |
| `playbooks/tags.md` | Canonical tag reference, P0 entries only (extended in later plans) |
| `roles/preflight/` | OS version, SELinux=enforcing, firewalld present, time sync, mounts, block-device-separation check (warn) |
| `roles/repos/` | Install `pgdg-redhat-repo` RPM, RHEL/Rocky/Alma vendor repos enabled, EPEL opt-in only, pigsty repo opt-in via list of packages, dnf priorities set |
| `roles/node/` | Hostname from inventory, `/etc/hosts` rendered from inventory, sysctl tuning, journald sizing, firewalld baseline (ssh open, default zone), unattended-upgrades disabled |
| `roles/ca/` | Localhost-only role: generate self-signed CA in `pki/ca/` via `community.crypto`. Idempotent. |
| `roles/certs/` | Per-host certs: generate local private key, build CSR with SANs, sign on control node, distribute to `/etc/pki/pigsty-lite/<host>.{crt,key}`. Renew if `notAfter < cert_renewal_window` |
| `files/firewalld/services/` | Empty dir in P0 (custom XMLs added in later sub-plans). Committed `.gitkeep` |
| `tests/molecule/preflight/` | Molecule scenario for `preflight` role (podman driver) |
| `tests/molecule/repos/` | Molecule scenario for `repos` role (podman driver) |
| `tests/molecule/node/` | Molecule scenario for `node` role (podman driver) |
| `tests/molecule/ca/` | Molecule scenario for `ca` role (runs on localhost; uses default driver) |
| `tests/molecule/certs/` | Molecule scenario for `certs` role (podman driver) |
| `tests/configure/test_schema.py` | Pytest tests for response-file schema validation |
| `tests/configure/test_generate_inventory.py` | Pytest tests for inventory generation from response |
| `tests/configure/test_generate_response_vars.py` | Pytest tests for `group_vars/response.yml` generation |
| `tests/configure/fixtures/single.rsp.yml` | Fixture response file: single profile |
| `tests/configure/fixtures/ha.rsp.yml` | Fixture response file: ha profile |
| `tests/configure/fixtures/invalid.rsp.yml` | Fixture response file: deliberately broken |
| `.ansible-lint` | ansible-lint config |
| `.yamllint` | yamllint config |
| `pyproject.toml` | ruff config + pytest config |
| `Makefile.d/lint.mk` | Lint helper makefile included by `Makefile` (keeps lint logic isolated) |
| `.github/workflows/lint.yml` | GitHub Actions workflow: yamllint + ansible-lint + ruff + shellcheck + xmllint + markdownlint |
| `.github/workflows/molecule.yml` | GitHub Actions workflow: Molecule matrix over P0 roles (podman-only) |
| `docs/operations/firstrun.md` | Operator-facing first-run guide |

---

## Cross-cutting conventions (apply to every task)

These match the spec's §4.2 and §7 directly. The implementing agent must follow them in every role and Python file:

- Every Ansible variable name is **prefixed by role/domain** (`preflight_*`, `repos_*`, `node_*`, `ca_*`, `cert_*`). No bare names.
- Every role has a `defaults/main.yml`, `tasks/main.yml`, `meta/main.yml`, and `README.md`. Optional: `handlers/main.yml`, `vars/main.yml`, `files/`, `templates/`.
- Every `command:`/`shell:` task has explicit `changed_when:` based on output, not on return code.
- No `tags: always` anywhere.
- Roles never reference another role's variables directly; cross-role state lives in `group_vars/all.yml`.
- Every Molecule scenario runs `converge.yml` twice (idempotency check is built into Molecule; the implementing agent must keep it enabled — do not disable in `molecule.yml`).
- Every Molecule `verify.yml` runs `ausearch -m AVC -ts boot` and fails if any AVC is found (deferred to P1+ where SELinux contexts get added; P0 roles don't add custom contexts).
- All Python is formatted with `ruff format` and linted with `ruff check`. No `print()` in library code; use `argparse` + `sys.exit(N)` for the CLI.
- Frequent commits: one commit per task as the final step. Never bundle multiple tasks in one commit.

---

## Task 1: Lint configs (yamllint, ansible-lint, ruff, markdownlint)

**Files:**
- Create: `.yamllint`
- Create: `.ansible-lint`
- Create: `pyproject.toml`
- Create: `.markdownlint.yaml`

- [ ] **Step 1: Create `.yamllint`**

```yaml
extends: default
rules:
  line-length:
    max: 160
    level: warning
  truthy:
    allowed-values: ['true', 'false', 'yes', 'no']
    check-keys: false
  comments:
    min-spaces-from-content: 1
  indentation:
    spaces: 2
    indent-sequences: consistent
ignore: |
  .venv/
  .ansible/
  collections/
```

- [ ] **Step 2: Create `.ansible-lint`**

```yaml
profile: production

exclude_paths:
  - .git/
  - .ansible/
  - collections/
  - tests/molecule/*/molecule.yml

skip_list:
  - role-name  # we use single-word role names by design

warn_list: []

kinds:
  - playbook: "playbooks/*.yml"
  - tasks: "roles/*/tasks/*.yml"
  - vars: "roles/*/vars/*.yml"
  - meta: "roles/*/meta/main.yml"
```

- [ ] **Step 3: Create `pyproject.toml`**

```toml
[tool.ruff]
line-length = 100
target-version = "py311"
extend-exclude = [".venv", "collections", ".ansible"]

[tool.ruff.lint]
select = ["E", "F", "I", "W", "B", "UP", "SIM"]
ignore = []

[tool.ruff.format]
quote-style = "double"

[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]
addopts = "-v --strict-markers"
```

- [ ] **Step 4: Create `.markdownlint.yaml`**

```yaml
default: true
MD013: false  # line-length: rely on yamllint-style soft wrap
MD033: false  # inline HTML allowed
MD041: false  # first-line H1 not required (specs have YAML frontmatter)
```

- [ ] **Step 5: Verify lint configs load**

Run:
```bash
yamllint --version && yamllint -c .yamllint .gitignore
ansible-lint --version
ruff check --no-fix .
markdownlint-cli2 --version || npx markdownlint-cli2 --version
```
Expected: each command exits 0 (no findings yet — repo has almost no files).

- [ ] **Step 6: Commit**

```bash
git add .yamllint .ansible-lint pyproject.toml .markdownlint.yaml
git commit -m "build: add lint configurations"
```

---

## Task 2: `ansible.cfg` and `requirements.yml`

**Files:**
- Create: `ansible.cfg`
- Create: `requirements.yml`

- [ ] **Step 1: Create `ansible.cfg`**

```ini
[defaults]
forks = 10
inventory = inventory/site.yml
host_key_checking = False
retry_files_enabled = False
pipelining = True
gathering = smart
fact_caching = jsonfile
fact_caching_connection = .ansible/facts
fact_caching_timeout = 7200
stdout_callback = yaml
callbacks_enabled = profile_tasks
timeout = 30
deprecation_warnings = True
command_warnings = False
nocows = 1
collections_path = ./collections

[ssh_connection]
pipelining = True
ssh_args = -o ControlMaster=auto -o ControlPersist=60s -o ServerAliveInterval=15

[privilege_escalation]
become = True
become_method = sudo
become_ask_pass = False
```

- [ ] **Step 2: Create `requirements.yml`**

```yaml
---
collections:
  - name: community.postgresql
    version: "3.4.0"
  - name: community.crypto
    version: "2.22.0"
  - name: community.general
    version: "9.4.0"
  - name: ansible.posix
    version: "1.5.4"
  # Used in later sub-plans; pinned now so CI installs them once
  - name: grafana.grafana
    version: "5.5.0"
  - name: community.grafana
    version: "2.1.0"
  - name: victoriametrics.cluster
    version: "0.0.10"
```

- [ ] **Step 3: Install collections to verify pins resolve**

Run: `ansible-galaxy collection install -r requirements.yml -p ./collections`
Expected: each collection installs without error.

- [ ] **Step 4: Verify ansible.cfg is recognized**

Run: `ansible --version | grep "config file"`
Expected: line contains `ansible.cfg` (the file in the repo).

- [ ] **Step 5: Commit**

```bash
git add ansible.cfg requirements.yml
git commit -m "build: pin ansible config and Galaxy collections"
```

---

## Task 3: `Makefile` skeleton

**Files:**
- Create: `Makefile`
- Create: `Makefile.d/lint.mk`

- [ ] **Step 1: Create `Makefile.d/lint.mk`**

```makefile
# Lint targets — included by top-level Makefile.

.PHONY: lint lint-yaml lint-ansible lint-python lint-markdown lint-shell lint-xml

lint: lint-yaml lint-ansible lint-python lint-markdown lint-shell lint-xml

lint-yaml:
	yamllint .

lint-ansible:
	ansible-lint

lint-python:
	ruff check .
	ruff format --check .

lint-markdown:
	markdownlint-cli2 "**/*.md" "#collections" "#.ansible"

lint-shell:
	@if compgen -G "bin/*" > /dev/null; then \
		shellcheck $$(find bin -type f -not -name '*.py' -not -name '_*.py'); \
	fi

lint-xml:
	@if compgen -G "files/firewalld/services/*.xml" > /dev/null; then \
		xmllint --noout files/firewalld/services/*.xml; \
	fi
```

- [ ] **Step 2: Create `Makefile`**

```makefile
# pigsty-lite operator entry points.

SHELL := /usr/bin/env bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

include Makefile.d/lint.mk

.PHONY: help init configure plan deploy clean test-role

help:
	@echo "pigsty-lite — operator commands"
	@echo
	@echo "  make init          Set up the control node (install Galaxy collections + roles)"
	@echo "  make configure     Interactive wizard; emits inventory + response file"
	@echo "  make plan          Run site.yml in --check --diff mode"
	@echo "  make deploy        Run site.yml against the active inventory"
	@echo "  make lint          Run all linters"
	@echo "  make test-role ROLE=<name>   Run molecule for a single role"
	@echo "  make clean         Remove generated artifacts"

init:
	ansible-galaxy collection install -r requirements.yml -p ./collections
	ansible-galaxy role install -r requirements.yml -p ./roles.galaxy

configure:
	./configure

plan: init
	ansible-playbook playbooks/site.yml --check --diff

deploy: init
	ansible-playbook playbooks/site.yml

test-role:
	@if [ -z "$(ROLE)" ]; then echo "Usage: make test-role ROLE=<name>"; exit 2; fi
	cd tests/molecule/$(ROLE) && molecule test

clean:
	rm -rf .ansible/facts dist/ artifacts/
	find . -name __pycache__ -type d -exec rm -rf {} +
```

- [ ] **Step 3: Verify Makefile parses**

Run: `make help`
Expected: prints the help block; exit 0.

- [ ] **Step 4: Commit**

```bash
git add Makefile Makefile.d/lint.mk
git commit -m "build: add Makefile and lint helpers"
```

---

## Task 4: Response-file JSON Schema (`bin/_response_schema.py`)

**Files:**
- Create: `bin/_response_schema.py`
- Create: `tests/configure/__init__.py`
- Create: `tests/configure/fixtures/single.rsp.yml`
- Create: `tests/configure/fixtures/ha.rsp.yml`
- Create: `tests/configure/fixtures/invalid.rsp.yml`
- Create: `tests/configure/test_schema.py`

- [ ] **Step 1: Write fixture `single.rsp.yml`**

Path: `tests/configure/fixtures/single.rsp.yml`

```yaml
profile: single
cluster:
  name: pg-dev
  domain: example.internal
nodes:
  pgmon01:
    ip: 10.20.30.10
    role: monitor
  pgnode01:
    ip: 10.20.30.11
    role: pg_primary
postgres:
  version: 18
  port: 5432
  tune: oltp
  shared_buffer_ratio: 0.25
  extensions: [pg_stat_statements]
  databases: []
  users: []
  hba_rules: []
pgbackrest:
  enabled: false
tls:
  internal_ca: generate
  user_facing: { mode: ca_signed }
monitoring:
  vmsingle_retention: 30d
  vlsingle_retention: 14d
  alertmanager: { receivers: [] }
repos:
  pigsty: { enabled: false, packages: [] }
firewall:
  operator_cidrs: ["10.0.0.0/8"]
  postgres_client_cidrs: ["10.20.40.0/24"]
```

- [ ] **Step 2: Write fixture `ha.rsp.yml`**

Path: `tests/configure/fixtures/ha.rsp.yml`

```yaml
profile: ha
cluster:
  name: pg-prod
  domain: example.internal
nodes:
  pgmon01:  { ip: 10.20.30.10, role: monitor }
  pgnode01: { ip: 10.20.30.11, role: pg_primary }
  pgnode02: { ip: 10.20.30.12, role: pg_replica }
  pgnode03: { ip: 10.20.30.13, role: pg_replica }
postgres:
  version: 18
  port: 5432
  tune: oltp
  shared_buffer_ratio: 0.25
  extensions: [pg_stat_statements, pgvector]
  databases: [{ name: app, owner: app }]
  users: []
  hba_rules:
    - { db: app, user: app, source: 10.20.40.0/24, method: scram-sha-256 }
pgbackrest:
  enabled: true
  schedule: { full: "0 1 * * 0", differential: "0 1 * * 1-6" }
  retention: { full: 4 }
tls:
  internal_ca: generate
  user_facing: { mode: ca_signed }
monitoring:
  vmsingle_retention: 90d
  vlsingle_retention: 30d
  alertmanager: { receivers: [] }
repos:
  pigsty: { enabled: false, packages: [] }
firewall:
  operator_cidrs: ["10.0.0.0/8"]
  postgres_client_cidrs: ["10.20.40.0/24"]
```

- [ ] **Step 3: Write fixture `invalid.rsp.yml`**

Path: `tests/configure/fixtures/invalid.rsp.yml`

```yaml
profile: enterprise
cluster:
  name: pg-prod
nodes:
  pgmon01: { ip: not-an-ip, role: monitor }
```

- [ ] **Step 4: Write the failing test for the schema**

Path: `tests/configure/test_schema.py`

```python
"""Tests for the response-file schema validator."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from bin._response_schema import SchemaError, validate

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    with (FIXTURES / name).open() as fh:
        return yaml.safe_load(fh)


def test_single_profile_fixture_validates():
    data = _load("single.rsp.yml")
    validate(data)  # raises SchemaError on failure


def test_ha_profile_fixture_validates():
    data = _load("ha.rsp.yml")
    validate(data)


def test_invalid_profile_value_rejected():
    data = _load("invalid.rsp.yml")
    with pytest.raises(SchemaError, match="profile"):
        validate(data)


def test_missing_required_top_level_key_rejected():
    data = _load("ha.rsp.yml")
    del data["cluster"]
    with pytest.raises(SchemaError, match="cluster"):
        validate(data)


def test_bad_ip_rejected():
    data = _load("ha.rsp.yml")
    data["nodes"]["pgnode01"]["ip"] = "999.999.999.999"
    with pytest.raises(SchemaError, match="ip"):
        validate(data)


def test_unknown_node_role_rejected():
    data = _load("ha.rsp.yml")
    data["nodes"]["pgnode01"]["role"] = "wizard"
    with pytest.raises(SchemaError, match="role"):
        validate(data)


def test_single_profile_must_have_exactly_one_postgres_node():
    data = _load("ha.rsp.yml")
    data["profile"] = "single"
    with pytest.raises(SchemaError, match="single"):
        validate(data)


def test_ha_profile_requires_three_postgres_nodes():
    data = _load("ha.rsp.yml")
    del data["nodes"]["pgnode03"]
    with pytest.raises(SchemaError, match="ha"):
        validate(data)


def test_ha_profile_requires_exactly_one_primary():
    data = _load("ha.rsp.yml")
    data["nodes"]["pgnode02"]["role"] = "pg_primary"
    with pytest.raises(SchemaError, match="primary"):
        validate(data)
```

- [ ] **Step 5: Run tests to verify they fail**

Run: `python -m pytest tests/configure/test_schema.py -v`
Expected: ImportError or ModuleNotFoundError on `bin._response_schema`.

- [ ] **Step 6: Implement `bin/_response_schema.py`**

```python
"""Response-file schema validation for pigsty-lite.

Pure-Python validator using stdlib only. Imported by the `configure` CLI.
Raises SchemaError with a human-readable message on validation failure.
"""
from __future__ import annotations

import ipaddress
import re
from typing import Any

ALLOWED_PROFILES = {"single", "ha"}
ALLOWED_NODE_ROLES = {"monitor", "backup_store", "pg_primary", "pg_replica"}
ALLOWED_TUNE = {"oltp", "olap", "tiny"}
ALLOWED_CA_MODES = {"generate", "existing", "byo"}
ALLOWED_USER_TLS = {"ca_signed", "byo", "http"}
CRON_RE = re.compile(r"^[\d\s\*/,\-]+$")
DURATION_RE = re.compile(r"^\d+[smhdw]$")


class SchemaError(ValueError):
    """Raised when a response file fails schema validation."""


def _require(d: dict, key: str, path: str) -> Any:
    if key not in d:
        raise SchemaError(f"{path}: missing required key '{key}'")
    return d[key]


def _require_str(d: dict, key: str, path: str) -> str:
    v = _require(d, key, path)
    if not isinstance(v, str):
        raise SchemaError(f"{path}.{key}: expected string, got {type(v).__name__}")
    return v


def _require_int(d: dict, key: str, path: str) -> int:
    v = _require(d, key, path)
    if not isinstance(v, int) or isinstance(v, bool):
        raise SchemaError(f"{path}.{key}: expected int, got {type(v).__name__}")
    return v


def _require_bool(d: dict, key: str, path: str) -> bool:
    v = _require(d, key, path)
    if not isinstance(v, bool):
        raise SchemaError(f"{path}.{key}: expected bool, got {type(v).__name__}")
    return v


def _check_ip(s: str, path: str) -> None:
    try:
        ipaddress.ip_address(s)
    except ValueError as exc:
        raise SchemaError(f"{path}: invalid ip '{s}': {exc}") from exc


def _check_cidr(s: str, path: str) -> None:
    try:
        ipaddress.ip_network(s, strict=False)
    except ValueError as exc:
        raise SchemaError(f"{path}: invalid cidr '{s}': {exc}") from exc


def _validate_nodes(nodes: dict, profile: str) -> None:
    if not isinstance(nodes, dict) or not nodes:
        raise SchemaError("nodes: must be a non-empty mapping")

    roles: list[str] = []
    for name, node in nodes.items():
        path = f"nodes.{name}"
        if not isinstance(node, dict):
            raise SchemaError(f"{path}: must be a mapping")
        ip = _require_str(node, "ip", path)
        _check_ip(ip, f"{path}.ip")
        role = _require_str(node, "role", path)
        if role not in ALLOWED_NODE_ROLES:
            raise SchemaError(
                f"{path}.role: '{role}' not in {sorted(ALLOWED_NODE_ROLES)}"
            )
        roles.append(role)

    primaries = roles.count("pg_primary")
    replicas = roles.count("pg_replica")
    monitors = roles.count("monitor")

    if monitors != 1:
        raise SchemaError(f"nodes: profile '{profile}' requires exactly 1 monitor node")
    if primaries != 1:
        raise SchemaError(
            f"nodes: profile '{profile}' requires exactly 1 pg_primary; got {primaries}"
        )

    if profile == "single":
        if replicas != 0:
            raise SchemaError(
                "nodes: profile 'single' allows 0 pg_replica; "
                f"got {replicas} (use profile 'ha' for replicas)"
            )
    elif profile == "ha":
        if replicas < 2:
            raise SchemaError(
                f"nodes: profile 'ha' requires at least 2 pg_replica; got {replicas}"
            )


def _validate_postgres(pg: dict) -> None:
    if not isinstance(pg, dict):
        raise SchemaError("postgres: must be a mapping")
    ver = _require_int(pg, "version", "postgres")
    if ver < 14 or ver > 18:
        raise SchemaError(f"postgres.version: {ver} not in 14..18")
    port = _require_int(pg, "port", "postgres")
    if port < 1 or port > 65535:
        raise SchemaError(f"postgres.port: {port} out of range")
    tune = _require_str(pg, "tune", "postgres")
    if tune not in ALLOWED_TUNE:
        raise SchemaError(f"postgres.tune: '{tune}' not in {sorted(ALLOWED_TUNE)}")
    sbr = pg.get("shared_buffer_ratio", 0.25)
    if not isinstance(sbr, (int, float)) or not 0.05 <= sbr <= 0.6:
        raise SchemaError("postgres.shared_buffer_ratio: must be a float in 0.05..0.6")


def _validate_tls(tls: dict) -> None:
    if not isinstance(tls, dict):
        raise SchemaError("tls: must be a mapping")
    mode = _require_str(tls, "internal_ca", "tls")
    if mode not in ALLOWED_CA_MODES:
        raise SchemaError(
            f"tls.internal_ca: '{mode}' not in {sorted(ALLOWED_CA_MODES)}"
        )
    user = _require(tls, "user_facing", "tls")
    if not isinstance(user, dict):
        raise SchemaError("tls.user_facing: must be a mapping")
    um = _require_str(user, "mode", "tls.user_facing")
    if um not in ALLOWED_USER_TLS:
        raise SchemaError(
            f"tls.user_facing.mode: '{um}' not in {sorted(ALLOWED_USER_TLS)}"
        )


def _validate_firewall(fw: dict) -> None:
    if not isinstance(fw, dict):
        raise SchemaError("firewall: must be a mapping")
    for key in ("operator_cidrs", "postgres_client_cidrs"):
        v = _require(fw, key, "firewall")
        if not isinstance(v, list) or not v:
            raise SchemaError(f"firewall.{key}: must be a non-empty list")
        for i, cidr in enumerate(v):
            _check_cidr(cidr, f"firewall.{key}[{i}]")


def _validate_monitoring(mon: dict) -> None:
    if not isinstance(mon, dict):
        raise SchemaError("monitoring: must be a mapping")
    for key in ("vmsingle_retention", "vlsingle_retention"):
        v = _require_str(mon, key, "monitoring")
        if not DURATION_RE.match(v):
            raise SchemaError(
                f"monitoring.{key}: '{v}' must match Nm|Nh|Nd|Nw form (e.g. 90d)"
            )


def validate(data: Any) -> None:
    """Validate a response-file dict in place. Raises SchemaError on failure."""
    if not isinstance(data, dict):
        raise SchemaError("response file: top-level must be a mapping")

    profile = _require_str(data, "profile", "")
    if profile not in ALLOWED_PROFILES:
        raise SchemaError(
            f"profile: '{profile}' not in {sorted(ALLOWED_PROFILES)}"
        )

    cluster = _require(data, "cluster", "")
    if not isinstance(cluster, dict):
        raise SchemaError("cluster: must be a mapping")
    _require_str(cluster, "name", "cluster")
    _require_str(cluster, "domain", "cluster")

    nodes = _require(data, "nodes", "")
    _validate_nodes(nodes, profile)
    _validate_postgres(_require(data, "postgres", ""))
    _validate_tls(_require(data, "tls", ""))
    _validate_firewall(_require(data, "firewall", ""))
    _validate_monitoring(_require(data, "monitoring", ""))
```

- [ ] **Step 7: Add `bin/__init__.py` so tests can import**

```python
"""pigsty-lite control-node helpers."""
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `python -m pytest tests/configure/test_schema.py -v`
Expected: 9 passed.

- [ ] **Step 9: Commit**

```bash
git add bin/_response_schema.py bin/__init__.py tests/configure/__init__.py \
        tests/configure/fixtures/ tests/configure/test_schema.py
git commit -m "feat(configure): response-file schema validator"
```

---

## Task 5: Inventory generator (`bin/_generate_inventory.py`)

**Files:**
- Create: `bin/_generate_inventory.py`
- Create: `tests/configure/test_generate_inventory.py`

- [ ] **Step 1: Write the failing test**

Path: `tests/configure/test_generate_inventory.py`

```python
"""Tests for the inventory generator."""
from __future__ import annotations

from pathlib import Path

import yaml

from bin._generate_inventory import generate

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    with (FIXTURES / name).open() as fh:
        return yaml.safe_load(fh)


def test_single_inventory_has_required_groups():
    out = yaml.safe_load(generate(_load("single.rsp.yml")))
    children = out["all"]["children"]
    assert set(children) >= {"monitor", "backup_store", "etcd", "postgres"}


def test_single_inventory_collocates_monitor_and_backup_store():
    out = yaml.safe_load(generate(_load("single.rsp.yml")))
    children = out["all"]["children"]
    mon_hosts = set(children["monitor"]["hosts"].keys())
    bs_hosts = set(children["backup_store"]["hosts"].keys())
    assert mon_hosts == bs_hosts == {"pgmon01"}


def test_single_postgres_node_is_in_etcd_group():
    out = yaml.safe_load(generate(_load("single.rsp.yml")))
    etcd_hosts = out["all"]["children"]["etcd"]["hosts"]
    pg_hosts = out["all"]["children"]["postgres"]["hosts"]
    assert set(etcd_hosts.keys()) == set(pg_hosts.keys()) == {"pgnode01"}


def test_ha_inventory_has_three_postgres_and_three_etcd_members():
    out = yaml.safe_load(generate(_load("ha.rsp.yml")))
    pg_hosts = out["all"]["children"]["postgres"]["hosts"]
    etcd_hosts = out["all"]["children"]["etcd"]["hosts"]
    assert set(pg_hosts.keys()) == {"pgnode01", "pgnode02", "pgnode03"}
    assert set(etcd_hosts.keys()) == {"pgnode01", "pgnode02", "pgnode03"}


def test_postgres_role_set_on_primary_and_replicas():
    out = yaml.safe_load(generate(_load("ha.rsp.yml")))
    pg = out["all"]["children"]["postgres"]["hosts"]
    assert pg["pgnode01"]["postgres_role"] == "primary"
    assert pg["pgnode02"]["postgres_role"] == "replica"
    assert pg["pgnode03"]["postgres_role"] == "replica"


def test_etcd_seq_and_postgres_seq_assigned_deterministically():
    out = yaml.safe_load(generate(_load("ha.rsp.yml")))
    pg = out["all"]["children"]["postgres"]["hosts"]
    etcd = out["all"]["children"]["etcd"]["hosts"]
    assert pg["pgnode01"]["postgres_seq"] == 1
    assert pg["pgnode02"]["postgres_seq"] == 2
    assert pg["pgnode03"]["postgres_seq"] == 3
    assert etcd["pgnode01"]["etcd_seq"] == 1
    assert etcd["pgnode02"]["etcd_seq"] == 2
    assert etcd["pgnode03"]["etcd_seq"] == 3


def test_ansible_host_propagated_from_response():
    out = yaml.safe_load(generate(_load("ha.rsp.yml")))
    pg = out["all"]["children"]["postgres"]["hosts"]
    assert pg["pgnode01"]["ansible_host"] == "10.20.30.11"


def test_generated_inventory_includes_banner_comment():
    raw = generate(_load("single.rsp.yml"))
    assert raw.lstrip().startswith("#")
    assert "configure" in raw.splitlines()[0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/configure/test_generate_inventory.py -v`
Expected: ImportError on `bin._generate_inventory`.

- [ ] **Step 3: Implement `bin/_generate_inventory.py`**

```python
"""Inventory generator: response dict -> inventory YAML string."""
from __future__ import annotations

from io import StringIO
from typing import Any

import yaml

BANNER = (
    "# GENERATED FILE — DO NOT EDIT.\n"
    "# Regenerate via: ./configure -s -f responses/site.rsp.yml\n"
    "# Source of truth: responses/site.rsp.yml\n"
)


def _split_nodes_by_role(nodes: dict[str, dict]) -> dict[str, list[tuple[str, dict]]]:
    by_role: dict[str, list[tuple[str, dict]]] = {
        "monitor": [],
        "backup_store": [],
        "pg_primary": [],
        "pg_replica": [],
    }
    for name, node in nodes.items():
        by_role[node["role"]].append((name, node))
    return by_role


def _build_group(hosts: list[tuple[str, dict[str, Any]]]) -> dict:
    return {"hosts": {name: host_vars for name, host_vars in hosts}}


def generate(response: dict[str, Any]) -> str:
    """Produce inventory/site.yml content from a validated response dict."""
    by_role = _split_nodes_by_role(response["nodes"])

    # monitor group
    monitor_hosts = [
        (name, {"ansible_host": node["ip"]}) for name, node in by_role["monitor"]
    ]

    # backup_store group: defaults to monitor's hosts when no explicit
    # backup_store entry exists.
    if by_role["backup_store"]:
        backup_hosts = [
            (name, {"ansible_host": node["ip"]})
            for name, node in by_role["backup_store"]
        ]
    else:
        backup_hosts = monitor_hosts.copy()

    # postgres group: primary first, then replicas in insertion order.
    pg_nodes = by_role["pg_primary"] + by_role["pg_replica"]
    pg_hosts: list[tuple[str, dict[str, Any]]] = []
    for seq, (name, node) in enumerate(pg_nodes, start=1):
        host_vars = {
            "ansible_host": node["ip"],
            "postgres_role": "primary" if node["role"] == "pg_primary" else "replica",
            "postgres_seq": seq,
        }
        pg_hosts.append((name, host_vars))

    # etcd group: same hosts as postgres (colocated), with etcd_seq.
    etcd_hosts: list[tuple[str, dict[str, Any]]] = []
    for seq, (name, node) in enumerate(pg_nodes, start=1):
        etcd_hosts.append((name, {"ansible_host": node["ip"], "etcd_seq": seq}))

    inventory = {
        "all": {
            "children": {
                "monitor": _build_group(monitor_hosts),
                "backup_store": _build_group(backup_hosts),
                "etcd": _build_group(etcd_hosts),
                "postgres": _build_group(pg_hosts),
            }
        }
    }

    buf = StringIO()
    buf.write(BANNER)
    yaml.safe_dump(inventory, buf, sort_keys=False, default_flow_style=False)
    return buf.getvalue()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/configure/test_generate_inventory.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add bin/_generate_inventory.py tests/configure/test_generate_inventory.py
git commit -m "feat(configure): inventory generator from response file"
```

---

## Task 6: Response-vars generator (`bin/_generate_response_vars.py`)

**Files:**
- Create: `bin/_generate_response_vars.py`
- Create: `tests/configure/test_generate_response_vars.py`

- [ ] **Step 1: Write the failing test**

Path: `tests/configure/test_generate_response_vars.py`

```python
"""Tests for group_vars/response.yml generator."""
from __future__ import annotations

from pathlib import Path

import yaml

from bin._generate_response_vars import generate

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    with (FIXTURES / name).open() as fh:
        return yaml.safe_load(fh)


def test_cluster_keys_mapped():
    out = yaml.safe_load(generate(_load("ha.rsp.yml")))
    assert out["cluster_name"] == "pg-prod"
    assert out["cluster_domain"] == "example.internal"
    assert out["cluster_profile"] == "ha"


def test_postgres_keys_namespaced():
    out = yaml.safe_load(generate(_load("ha.rsp.yml")))
    assert out["postgres_version"] == 18
    assert out["postgres_port"] == 5432
    assert out["postgres_tune_profile"] == "oltp"
    assert out["postgres_shared_buffer_ratio"] == 0.25
    assert out["postgres_extensions"] == ["pg_stat_statements", "pgvector"]
    assert out["postgres_databases"] == [{"name": "app", "owner": "app"}]


def test_firewall_keys_promoted_to_top_level():
    out = yaml.safe_load(generate(_load("ha.rsp.yml")))
    assert out["operator_cidrs"] == ["10.0.0.0/8"]
    assert out["postgres_client_cidrs"] == ["10.20.40.0/24"]


def test_tls_keys_namespaced():
    out = yaml.safe_load(generate(_load("ha.rsp.yml")))
    assert out["ca_mode"] == "generate"
    assert out["nginx_proxy_tls_mode"] == "ca_signed"


def test_monitoring_retention_namespaced():
    out = yaml.safe_load(generate(_load("ha.rsp.yml")))
    assert out["vmsingle_retention"] == "90d"
    assert out["vlsingle_retention"] == "30d"


def test_pgbackrest_disabled_when_response_says_so():
    out = yaml.safe_load(generate(_load("single.rsp.yml")))
    assert out["pgbackrest_enabled"] is False


def test_pgbackrest_schedule_promoted():
    out = yaml.safe_load(generate(_load("ha.rsp.yml")))
    assert out["pgbackrest_enabled"] is True
    assert out["pgbackrest_schedule_full"] == "0 1 * * 0"
    assert out["pgbackrest_schedule_differential"] == "0 1 * * 1-6"
    assert out["pgbackrest_retention_full"] == 4


def test_generated_file_has_banner():
    raw = generate(_load("single.rsp.yml"))
    assert raw.lstrip().startswith("#")
    assert "GENERATED" in raw
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/configure/test_generate_response_vars.py -v`
Expected: ImportError on `bin._generate_response_vars`.

- [ ] **Step 3: Implement `bin/_generate_response_vars.py`**

```python
"""group_vars/response.yml generator: response dict -> Ansible vars YAML."""
from __future__ import annotations

from io import StringIO
from typing import Any

import yaml

BANNER = (
    "# GENERATED FILE — DO NOT EDIT.\n"
    "# Regenerate via: ./configure -s -f responses/site.rsp.yml\n"
    "# This is the operator-facing variable layer; edit the response file.\n"
)


def _flatten_pgbackrest(pgbr: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"pgbackrest_enabled": bool(pgbr.get("enabled", False))}
    if not out["pgbackrest_enabled"]:
        return out
    schedule = pgbr.get("schedule", {})
    if "full" in schedule:
        out["pgbackrest_schedule_full"] = schedule["full"]
    if "differential" in schedule:
        out["pgbackrest_schedule_differential"] = schedule["differential"]
    retention = pgbr.get("retention", {})
    if "full" in retention:
        out["pgbackrest_retention_full"] = retention["full"]
    if pgbr.get("repo2", {}).get("enabled"):
        out["pgbackrest_repo2"] = pgbr["repo2"]
    return out


def generate(response: dict[str, Any]) -> str:
    """Produce group_vars/response.yml content from a validated response dict."""
    pg = response["postgres"]
    tls = response["tls"]
    mon = response["monitoring"]
    fw = response["firewall"]
    repos = response.get("repos", {})

    out: dict[str, Any] = {
        "cluster_profile": response["profile"],
        "cluster_name": response["cluster"]["name"],
        "cluster_domain": response["cluster"]["domain"],
        # postgres
        "postgres_version": pg["version"],
        "postgres_port": pg["port"],
        "postgres_tune_profile": pg["tune"],
        "postgres_shared_buffer_ratio": pg.get("shared_buffer_ratio", 0.25),
        "postgres_extensions": pg.get("extensions", []),
        "postgres_databases": pg.get("databases", []),
        "postgres_users": pg.get("users", []),
        "postgres_hba_rules": pg.get("hba_rules", []),
        "postgres_extra_parameters": pg.get("extra_parameters", {}),
        "postgres_pin_version": pg.get("pin_version", ""),
        # tls
        "ca_mode": tls["internal_ca"],
        "nginx_proxy_tls_mode": tls["user_facing"]["mode"],
        # monitoring
        "vmsingle_retention": mon["vmsingle_retention"],
        "vlsingle_retention": mon["vlsingle_retention"],
        "alertmanager_receivers": mon.get("alertmanager", {}).get("receivers", []),
        # firewall
        "operator_cidrs": fw["operator_cidrs"],
        "postgres_client_cidrs": fw["postgres_client_cidrs"],
        # repos policy
        "repos_pigsty_enabled": bool(repos.get("pigsty", {}).get("enabled", False)),
        "repos_pigsty_packages": repos.get("pigsty", {}).get("packages", []),
    }
    out.update(_flatten_pgbackrest(response.get("pgbackrest", {})))

    buf = StringIO()
    buf.write(BANNER)
    yaml.safe_dump(out, buf, sort_keys=False, default_flow_style=False)
    return buf.getvalue()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/configure/test_generate_response_vars.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add bin/_generate_response_vars.py tests/configure/test_generate_response_vars.py
git commit -m "feat(configure): response-vars generator for group_vars/response.yml"
```

---

## Task 7: `configure` CLI script

**Files:**
- Create: `configure` (executable Python script)
- Create: `responses/single.rsp.yml.example`
- Create: `responses/ha.rsp.yml.example`

- [ ] **Step 1: Create example response files (operator-facing templates)**

Copy `tests/configure/fixtures/single.rsp.yml` to `responses/single.rsp.yml.example` and add a leading comment block:

```yaml
# pigsty-lite response file — SINGLE profile (1 monitor + 1 postgres).
# Copy to responses/site.rsp.yml and edit IPs / credentials / domain.
# Then run: ./configure -s -f responses/site.rsp.yml
```

Same for `ha.rsp.yml.example`.

- [ ] **Step 2: Implement `configure` CLI**

Path: `configure` (mode 0755)

```python
#!/usr/bin/env python3
"""pigsty-lite configure CLI.

Modes:
  ./configure                  Interactive wizard (TTY required)
  ./configure -c {single,ha}   Preset profile, prompts for the rest
  ./configure -s -f FILE       Silent: load FILE, validate, regenerate
  ./configure --validate FILE  Validate FILE and exit

Always emits:
  inventory/site.yml
  group_vars/response.yml
On a non-silent run, also writes:
  responses/site.rsp.yml
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

# Allow `bin/_*` imports without making `bin` a package on the path.
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from bin._generate_inventory import generate as gen_inventory
from bin._generate_response_vars import generate as gen_response_vars
from bin._response_schema import SchemaError, validate

INVENTORY_PATH = ROOT / "inventory" / "site.yml"
RESPONSE_VARS_PATH = ROOT / "group_vars" / "response.yml"
RESPONSE_FILE_PATH = ROOT / "responses" / "site.rsp.yml"


def _load_yaml(path: Path) -> dict:
    with path.open() as fh:
        return yaml.safe_load(fh)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def cmd_validate(args: argparse.Namespace) -> int:
    try:
        data = _load_yaml(Path(args.validate))
        validate(data)
    except SchemaError as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 2
    except (OSError, yaml.YAMLError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print("OK")
    return 0


def cmd_silent(args: argparse.Namespace) -> int:
    src = Path(args.file)
    try:
        data = _load_yaml(src)
        validate(data)
    except SchemaError as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 2
    except (OSError, yaml.YAMLError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    _write(INVENTORY_PATH, gen_inventory(data))
    _write(RESPONSE_VARS_PATH, gen_response_vars(data))
    print(f"Wrote {INVENTORY_PATH.relative_to(ROOT)}")
    print(f"Wrote {RESPONSE_VARS_PATH.relative_to(ROOT)}")
    return 0


def cmd_interactive(args: argparse.Namespace) -> int:
    if not sys.stdin.isatty():
        print(
            "ERROR: interactive mode requires a TTY; use -s -f FILE for silent mode",
            file=sys.stderr,
        )
        return 1
    print("pigsty-lite interactive configure")
    print("(P0 implements minimal prompts; future tasks extend this)")
    print()
    profile = args.profile or input("Profile (single|ha) [ha]: ").strip() or "ha"
    cluster_name = input("Cluster name [pg-prod]: ").strip() or "pg-prod"
    cluster_domain = input("Cluster domain [example.internal]: ").strip() or "example.internal"

    # P0 wizard is minimal: copy the example template, fill in just these three.
    example_name = f"{profile}.rsp.yml.example"
    example_path = ROOT / "responses" / example_name
    if not example_path.exists():
        print(f"ERROR: missing template {example_path}", file=sys.stderr)
        return 1
    data = _load_yaml(example_path)
    data["profile"] = profile
    data["cluster"]["name"] = cluster_name
    data["cluster"]["domain"] = cluster_domain

    try:
        validate(data)
    except SchemaError as exc:
        print(f"INVALID generated response: {exc}", file=sys.stderr)
        return 2

    RESPONSE_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with RESPONSE_FILE_PATH.open("w") as fh:
        yaml.safe_dump(data, fh, sort_keys=False)
    _write(INVENTORY_PATH, gen_inventory(data))
    _write(RESPONSE_VARS_PATH, gen_response_vars(data))
    print(f"Wrote {RESPONSE_FILE_PATH.relative_to(ROOT)}")
    print(f"Wrote {INVENTORY_PATH.relative_to(ROOT)}")
    print(f"Wrote {RESPONSE_VARS_PATH.relative_to(ROOT)}")
    print()
    print("Edit responses/site.rsp.yml to fill IPs, then: make plan")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="configure", description=__doc__)
    parser.add_argument("--validate", metavar="FILE", help="validate FILE and exit")
    parser.add_argument("-s", "--silent", action="store_true")
    parser.add_argument("-f", "--file", help="response file for silent mode")
    parser.add_argument(
        "-c", "--profile", choices=("single", "ha"), help="preset profile for wizard"
    )
    args = parser.parse_args(argv)

    if args.validate:
        return cmd_validate(args)
    if args.silent:
        if not args.file:
            parser.error("--silent requires --file")
        return cmd_silent(args)
    return cmd_interactive(args)


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3: Mark executable**

Run: `chmod +x configure`

- [ ] **Step 4: Verify silent mode against fixture**

Run:
```bash
./configure -s -f tests/configure/fixtures/ha.rsp.yml
```
Expected: prints two "Wrote ..." lines, exit 0. Inventory and `group_vars/response.yml` appear and parse:
```bash
ansible-inventory -i inventory/site.yml --list >/dev/null
yamllint group_vars/response.yml
```
Both exit 0.

- [ ] **Step 5: Verify `--validate` mode**

Run:
```bash
./configure --validate tests/configure/fixtures/invalid.rsp.yml
```
Expected: prints `INVALID: profile: 'enterprise' not in ['ha', 'single']` to stderr, exit 2.

- [ ] **Step 6: Commit**

```bash
git add configure responses/single.rsp.yml.example responses/ha.rsp.yml.example
git commit -m "feat(configure): silent + interactive + validate modes"
```

---

## Task 8: Inventory examples and group_vars defaults

**Files:**
- Create: `inventory/examples/single.yml` (output of running `configure -s` against the single fixture)
- Create: `inventory/examples/ha.yml`
- Create: `group_vars/all.yml`
- Create: `group_vars/postgres.yml` (placeholder for P2)
- Create: `group_vars/etcd.yml` (placeholder for P1)
- Create: `group_vars/monitor.yml` (placeholder for P5)
- Create: `group_vars/backup_store.yml` (placeholder for P4)

- [ ] **Step 1: Generate example inventories**

Run:
```bash
./configure -s -f tests/configure/fixtures/single.rsp.yml
cp inventory/site.yml inventory/examples/single.yml
./configure -s -f tests/configure/fixtures/ha.rsp.yml
cp inventory/site.yml inventory/examples/ha.yml
rm inventory/site.yml group_vars/response.yml
```

- [ ] **Step 2: Create `group_vars/all.yml`**

```yaml
---
# Cross-role coordination for pigsty-lite. Generated examples in
# inventory/examples/ illustrate identity-only host vars; this file
# defines the shared defaults all roles depend on.
#
# Variable precedence (low -> high):
#   role defaults -> group_vars/all -> group_vars/<group> ->
#   inventory host vars -> group_vars/response.yml -> --extra-vars

# Identity ----------------------------------------------------------
cluster_profile: "ha"
cluster_name: "pg-pigsty-lite"
cluster_domain: "example.internal"

# OS / SELinux / firewalld -----------------------------------------
selinux_required_mode: "Enforcing"
firewalld_default_zone: "public"

# Paths (vendor defaults; do not change without strong reason) ------
postgres_data_dir_pattern: "/var/lib/pgsql/{{ postgres_version }}/data"
etcd_data_dir: "/var/lib/etcd"
pgbackrest_repo_path: "/var/lib/pgbackrest"
pki_dir: "/etc/pki/pigsty-lite"

# Ports -------------------------------------------------------------
postgres_port: 5432
pgbouncer_port: 6432
haproxy_default_port: 5432
haproxy_primary_port: 5433
haproxy_replica_port: 5434
haproxy_stats_port: 7000
patroni_rest_port: 8008
etcd_client_port: 2379
etcd_peer_port: 2380
vmsingle_port: 8428
vlsingle_port: 9428
vmalert_port: 8880
alertmanager_port: 9093
grafana_port: 3000

# Versions ----------------------------------------------------------
postgres_version: 18

# Repo policy -------------------------------------------------------
repos_pgdg_enabled: true
repos_vendor_enabled: true
repos_epel_enabled: false
repos_pigsty_enabled: false
repos_pigsty_packages: []

# Cert renewal ------------------------------------------------------
cert_validity_days: 730
cert_renewal_window_days: 30

# Operator network defaults — override in response file -------------
operator_cidrs: ["10.0.0.0/8"]
postgres_client_cidrs: ["10.0.0.0/8"]
```

- [ ] **Step 3: Create placeholder group_vars files**

Each of the following has identical content — a YAML doc start plus one comment so the file is valid and committable.

`group_vars/postgres.yml`:
```yaml
---
# postgres group defaults. Populated in P2.
```

`group_vars/etcd.yml`:
```yaml
---
# etcd group defaults. Populated in P1.
```

`group_vars/monitor.yml`:
```yaml
---
# monitor group defaults. Populated in P5.
```

`group_vars/backup_store.yml`:
```yaml
---
# backup_store group defaults. Populated in P4.
```

- [ ] **Step 4: Verify**

Run:
```bash
yamllint group_vars/ inventory/examples/
ansible-inventory -i inventory/examples/ha.yml --list >/dev/null
```
Expected: both exit 0.

- [ ] **Step 5: Commit**

```bash
git add inventory/examples/ group_vars/
git commit -m "feat(inventory): example inventories and group_vars defaults"
```

---

## Task 9: `roles/preflight` — test first

**Files:**
- Create: `roles/preflight/meta/main.yml`
- Create: `roles/preflight/defaults/main.yml`
- Create: `roles/preflight/tasks/main.yml`
- Create: `roles/preflight/README.md`
- Create: `tests/molecule/preflight/molecule.yml`
- Create: `tests/molecule/preflight/converge.yml`
- Create: `tests/molecule/preflight/verify.yml`

- [ ] **Step 1: Write the Molecule scenario (test first)**

Path: `tests/molecule/preflight/molecule.yml`

```yaml
---
driver:
  name: podman
platforms:
  - name: pigsty-lite-preflight-rocky10
    image: docker.io/library/rockylinux:10
    pre_build_image: true
    privileged: true
    command: /usr/sbin/init
    volume_mounts:
      - "/sys/fs/cgroup:/sys/fs/cgroup:rw"
    capabilities: [SYS_ADMIN]
    systemd: always
provisioner:
  name: ansible
  inventory:
    host_vars:
      pigsty-lite-preflight-rocky10:
        selinux_required_mode: "Permissive"  # podman images can't enforce
verifier:
  name: ansible
scenario:
  test_sequence:
    - destroy
    - create
    - prepare
    - converge
    - idempotence
    - verify
    - destroy
```

Path: `tests/molecule/preflight/converge.yml`

```yaml
---
- name: Converge
  hosts: all
  become: true
  roles:
    - role: preflight
```

Path: `tests/molecule/preflight/verify.yml`

```yaml
---
- name: Verify
  hosts: all
  become: true
  tasks:
    - name: Read preflight_passed fact
      ansible.builtin.assert:
        that:
          - preflight_passed | default(false) | bool
        fail_msg: "preflight did not set preflight_passed=true"

    - name: Confirm firewalld is installed
      ansible.builtin.command: rpm -q firewalld
      register: firewalld_rpm
      changed_when: false

    - name: Confirm policycoreutils is installed
      ansible.builtin.command: rpm -q policycoreutils
      register: pcu_rpm
      changed_when: false
```

- [ ] **Step 2: Run molecule to verify the scenario fails (role doesn't exist yet)**

Run: `cd tests/molecule/preflight && molecule test`
Expected: failure at converge step with "role 'preflight' not found".

- [ ] **Step 3: Create `roles/preflight/meta/main.yml`**

```yaml
---
galaxy_info:
  role_name: preflight
  author: pigsty-lite
  description: Validate host before any change is applied.
  license: Apache-2.0
  min_ansible_version: "2.16"
  platforms:
    - name: EL
      versions: ["10"]
  galaxy_tags: [postgresql, preflight]
dependencies: []
```

- [ ] **Step 4: Create `roles/preflight/defaults/main.yml`**

```yaml
---
preflight_required_os_family: "RedHat"
preflight_required_os_major: 10
preflight_required_packages:
  - firewalld
  - policycoreutils
  - policycoreutils-python-utils
  - chrony
preflight_required_selinux_mode: "{{ selinux_required_mode | default('Enforcing') }}"
preflight_warn_only_on_block_devices: true
```

- [ ] **Step 5: Create `roles/preflight/tasks/main.yml`**

```yaml
---
- name: Gather distribution facts
  ansible.builtin.setup:
    gather_subset:
      - distribution
      - selinux
      - mounts

- name: Assert OS family
  ansible.builtin.assert:
    that:
      - ansible_facts.os_family == preflight_required_os_family
    fail_msg: >-
      pigsty-lite requires {{ preflight_required_os_family }};
      this host reports {{ ansible_facts.os_family }}.

- name: Assert OS major version
  ansible.builtin.assert:
    that:
      - (ansible_facts.distribution_major_version | int) == preflight_required_os_major
    fail_msg: >-
      pigsty-lite requires major version {{ preflight_required_os_major }};
      this host reports {{ ansible_facts.distribution_major_version }}.

- name: Install required packages (preflight expects these to manage host)
  ansible.builtin.dnf:
    name: "{{ preflight_required_packages }}"
    state: present
  register: pf_pkg

- name: Read SELinux mode from /sys (current runtime)
  ansible.builtin.slurp:
    src: /sys/fs/selinux/enforce
  register: pf_enforce
  failed_when: false
  changed_when: false

- name: Compute current SELinux runtime mode
  ansible.builtin.set_fact:
    preflight_selinux_runtime: >-
      {{ 'Enforcing' if (pf_enforce.content | default('MA==') | b64decode) == '1'
         else 'Permissive' }}
  when: pf_enforce.content is defined

- name: Assert SELinux runtime matches requirement
  ansible.builtin.assert:
    that:
      - preflight_selinux_runtime | default('Permissive') == preflight_required_selinux_mode
    fail_msg: >-
      pigsty-lite requires SELinux={{ preflight_required_selinux_mode }};
      this host is {{ preflight_selinux_runtime | default('unknown') }}.
  when: pf_enforce.content is defined

- name: Warn if swap is enabled
  ansible.builtin.debug:
    msg: "WARNING: swap is on. Recommended off for database hosts."
  when: ansible_facts.swaptotal_mb | default(0) | int > 0

- name: Record preflight pass
  ansible.builtin.set_fact:
    preflight_passed: true
```

- [ ] **Step 6: Create `roles/preflight/README.md`**

```markdown
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
```

- [ ] **Step 7: Re-run molecule to verify role passes**

Run: `cd tests/molecule/preflight && molecule test`
Expected: scenario passes including idempotence step (second converge reports zero changed).

- [ ] **Step 8: Commit**

```bash
git add roles/preflight/ tests/molecule/preflight/
git commit -m "feat(preflight): host validation role with molecule scenario"
```

---

## Task 10: `roles/repos` — test first

**Files:**
- Create: `roles/repos/meta/main.yml`
- Create: `roles/repos/defaults/main.yml`
- Create: `roles/repos/tasks/main.yml`
- Create: `roles/repos/templates/pigsty.repo.j2`
- Create: `roles/repos/README.md`
- Create: `tests/molecule/repos/molecule.yml`
- Create: `tests/molecule/repos/converge.yml`
- Create: `tests/molecule/repos/verify.yml`

- [ ] **Step 1: Write the Molecule scenario**

Path: `tests/molecule/repos/molecule.yml`

```yaml
---
driver:
  name: podman
platforms:
  - name: pigsty-lite-repos-rocky10
    image: docker.io/library/rockylinux:10
    pre_build_image: true
    privileged: true
    command: /usr/sbin/init
    volume_mounts:
      - "/sys/fs/cgroup:/sys/fs/cgroup:rw"
    capabilities: [SYS_ADMIN]
    systemd: always
provisioner:
  name: ansible
verifier:
  name: ansible
scenario:
  test_sequence:
    - destroy
    - create
    - converge
    - idempotence
    - verify
    - destroy
```

Path: `tests/molecule/repos/converge.yml`

```yaml
---
- name: Converge
  hosts: all
  become: true
  roles:
    - role: repos
```

Path: `tests/molecule/repos/verify.yml`

```yaml
---
- name: Verify
  hosts: all
  become: true
  tasks:
    - name: PGDG repo present
      ansible.builtin.command: dnf repolist --enabled
      register: repolist
      changed_when: false
    - name: Assert PGDG enabled
      ansible.builtin.assert:
        that:
          - "'pgdg' in repolist.stdout"

    - name: EPEL not enabled by default
      ansible.builtin.assert:
        that:
          - "'epel' not in repolist.stdout"

    - name: Pigsty repo not present (default disabled)
      ansible.builtin.stat:
        path: /etc/yum.repos.d/pigsty.repo
      register: pigsty_repo_file
    - name: Assert pigsty repo absent
      ansible.builtin.assert:
        that:
          - not pigsty_repo_file.stat.exists
```

- [ ] **Step 2: Run molecule to verify it fails**

Run: `cd tests/molecule/repos && molecule test`
Expected: failure at converge ("role 'repos' not found").

- [ ] **Step 3: Create `roles/repos/meta/main.yml`**

```yaml
---
galaxy_info:
  role_name: repos
  author: pigsty-lite
  description: Manage dnf repositories (PGDG, vendor, optional EPEL/pigsty).
  license: Apache-2.0
  min_ansible_version: "2.16"
  platforms:
    - name: EL
      versions: ["10"]
  galaxy_tags: [postgresql, repos]
dependencies: []
```

- [ ] **Step 4: Create `roles/repos/defaults/main.yml`**

```yaml
---
repos_pgdg_rpm_url: "https://download.postgresql.org/pub/repos/yum/reporpms/EL-10-x86_64/pgdg-redhat-repo-latest.noarch.rpm"
repos_pgdg_enabled: true
repos_vendor_enabled: true
repos_epel_enabled: false
repos_pigsty_enabled: false
repos_pigsty_packages: []
repos_pigsty_baseurl: "https://repo.pigsty.io/yum/infra/$basearch"
repos_pigsty_gpgkey: "https://repo.pigsty.io/key/RPM-GPG-KEY-pigsty"
```

- [ ] **Step 5: Create `roles/repos/tasks/main.yml`**

```yaml
---
- name: Install PGDG repo RPM
  ansible.builtin.dnf:
    name: "{{ repos_pgdg_rpm_url }}"
    state: present
    disable_gpg_check: true
  when: repos_pgdg_enabled | bool

- name: Disable default PG module (RHEL 10 ships a stream that conflicts with PGDG)
  ansible.builtin.command: dnf -y module disable postgresql
  args:
    creates: /etc/dnf/modules.d/postgresql-disabled
  when: repos_pgdg_enabled | bool
  register: pg_module_disable

- name: Mark PG module disabled
  ansible.builtin.copy:
    dest: /etc/dnf/modules.d/postgresql-disabled
    content: "disabled by pigsty-lite repos role\n"
    mode: "0644"
  when: pg_module_disable.changed | default(false)

- name: Enable EPEL (only if explicitly requested)
  ansible.builtin.dnf:
    name: epel-release
    state: present
  when: repos_epel_enabled | bool

- name: Render /etc/yum.repos.d/pigsty.repo
  ansible.builtin.template:
    src: pigsty.repo.j2
    dest: /etc/yum.repos.d/pigsty.repo
    mode: "0644"
  when: repos_pigsty_enabled | bool

- name: Remove pigsty.repo if disabled
  ansible.builtin.file:
    path: /etc/yum.repos.d/pigsty.repo
    state: absent
  when: not (repos_pigsty_enabled | bool)
```

- [ ] **Step 6: Create `roles/repos/templates/pigsty.repo.j2`**

```jinja
# Managed by pigsty-lite repos role. Do not edit.
[pigsty]
name = pigsty repo
baseurl = {{ repos_pigsty_baseurl }}
enabled = 1
gpgcheck = 1
gpgkey = {{ repos_pigsty_gpgkey }}
module_hotfixes = 1
```

- [ ] **Step 7: Create `roles/repos/README.md`**

```markdown
# repos

Manages dnf repositories: PGDG (default), vendor (always), EPEL (opt-in),
pigsty (opt-in).

## Inputs (selected)
- `repos_pgdg_enabled` (bool, default true)
- `repos_epel_enabled` (bool, default false)
- `repos_pigsty_enabled` (bool, default false)
- `repos_pigsty_packages` (list, default `[]`) — only install pigsty packages
  when this is non-empty. The actual `dnf install` happens in dependent roles.

## Tags
None.
```

- [ ] **Step 8: Re-run molecule to verify role passes**

Run: `cd tests/molecule/repos && molecule test`
Expected: scenario passes including idempotence.

- [ ] **Step 9: Commit**

```bash
git add roles/repos/ tests/molecule/repos/
git commit -m "feat(repos): manage PGDG, vendor, EPEL, pigsty repos"
```

---

## Task 10b: `roles/repos` — set priority on PGDG repo

Spec §11 calls for "Repo priority: PGDG > vendor > EPEL > pigsty, enforced via `dnf-plugins-core` priority weights." In practice the only ordering that materially affects behavior is **PGDG beats vendor** — without that, `dnf` resolves shared packages (notably `postgresql-libs`) from whichever repo has the highest NEVR, which is usually vendor on RHEL 10, and breaks the PGDG installation path P2 depends on. The other splits (vendor > EPEL > pigsty) cover repos that rarely contain overlapping packages, and `dnf`'s default tie-breaking handles them acceptably.

So the minimal correct enforcement is: set PGDG sections to a low priority (10), leave everything else at the default 99. We do not touch vendor repo files (OS-owned, can be rewritten by `dnf update`), and we do not touch EPEL or pigsty (no benefit, more files to manage). The `dnf` priorities plugin is built into `dnf-plugins-core`, which is present on RHEL/Rocky/Alma 10 by default but we install it explicitly to be safe. Lower numbers win.

**Files:**
- Modify: `roles/repos/defaults/main.yml`
- Modify: `roles/repos/tasks/main.yml`
- Modify: `tests/molecule/repos/molecule/default/verify.yml`

- [ ] **Step 1: Append priority default to `roles/repos/defaults/main.yml`**

```yaml
# Repo priorities (lower wins; dnf accepts 1..99, default 99).
# Spec §11 wants PGDG > vendor > EPEL > pigsty. In practice only the PGDG-vs-
# vendor split actually matters (shared packages like postgresql-libs), and
# pigsty/EPEL rarely overlap with anything we install. So we set PGDG=10 and
# leave everything else at the default 99. dnf's NEVR tie-breaking handles the
# rare overlap between vendor/EPEL/pigsty acceptably, and we avoid editing
# repo files we don't own.
repos_pgdg_priority: 10
```

- [ ] **Step 2: Add priority tasks to `roles/repos/tasks/main.yml`**

Insert these BEFORE the existing "Enable EPEL" task:

```yaml
- name: Ensure dnf priorities plugin package is installed
  ansible.builtin.dnf:
    name: dnf-plugins-core
    state: present

- name: Set priority on PGDG repo sections
  community.general.ini_file:
    path: "{{ repos_pgdg_repo_file }}"
    section: "{{ item }}"
    option: priority
    value: "{{ repos_pgdg_priority | string }}"
    mode: "0644"
    no_extra_spaces: true
  loop:
    - "pgdg-common"
    - "{{ repos_pgdg_extras_repo }}"
    - "pgdg{{ repos_pgdg_postgres_version }}"
  when: repos_pgdg_enabled | bool
```

`community.general.ini_file` is idempotent by content; re-running the role writes zero changes once priorities are in place.

- [ ] **Step 3: Extend `tests/molecule/repos/molecule/default/verify.yml`**

Append after the existing `Assert pigsty repo absent`:

```yaml
    - name: Read PGDG repo file content
      ansible.builtin.slurp:
        src: /etc/yum.repos.d/pgdg-redhat-all.repo
      register: pgdg_repo_content

    - name: Assert PGDG sections carry priority=10
      ansible.builtin.assert:
        that:
          - "'priority = 10' in (pgdg_repo_content.content | b64decode)"
        fail_msg: |
          Expected priority = 10 in PGDG repo file. Got:
          {{ pgdg_repo_content.content | b64decode }}
```

- [ ] **Step 4: Re-run repos molecule**

Run: `cd tests/molecule/repos && molecule test`
Expected: PASS including idempotence (second converge writes zero priority changes).

- [ ] **Step 5: Commit**

```bash
git add roles/repos/ tests/molecule/repos/molecule/default/verify.yml
git commit -m "fix(repos): set priority on PGDG repo per spec §11"
```

---

## Task 11: `roles/node` — test first

**Files:**
- Create: `roles/node/meta/main.yml`
- Create: `roles/node/defaults/main.yml`
- Create: `roles/node/handlers/main.yml`
- Create: `roles/node/tasks/main.yml`
- Create: `roles/node/templates/hosts.j2`
- Create: `roles/node/templates/90-pigsty-lite.conf.j2`
- Create: `roles/node/README.md`
- Create: `tests/molecule/node/molecule.yml`
- Create: `tests/molecule/node/converge.yml`
- Create: `tests/molecule/node/verify.yml`

- [ ] **Step 1: Write Molecule scenario**

Path: `tests/molecule/node/molecule.yml`

```yaml
---
driver:
  name: podman
platforms:
  - name: pigsty-lite-node-rocky10
    image: docker.io/library/rockylinux:10
    pre_build_image: true
    privileged: true
    command: /usr/sbin/init
    volume_mounts:
      - "/sys/fs/cgroup:/sys/fs/cgroup:rw"
    capabilities: [SYS_ADMIN, NET_ADMIN]
    systemd: always
provisioner:
  name: ansible
  inventory:
    group_vars:
      all:
        firewalld_default_zone: public
verifier:
  name: ansible
scenario:
  test_sequence:
    - destroy
    - create
    - converge
    - idempotence
    - verify
    - destroy
```

Path: `tests/molecule/node/converge.yml`

```yaml
---
- name: Converge
  hosts: all
  become: true
  roles:
    - role: repos
    - role: node
```

Path: `tests/molecule/node/verify.yml`

```yaml
---
- name: Verify
  hosts: all
  become: true
  tasks:
    - name: firewalld is enabled and running
      ansible.builtin.systemd:
        name: firewalld
        state: started
        enabled: true
      check_mode: true
      register: fw
    - ansible.builtin.assert:
        that:
          - not fw.changed

    - name: ssh service is allowed in default zone
      ansible.builtin.command: firewall-cmd --list-services
      register: services
      changed_when: false
    - ansible.builtin.assert:
        that:
          - "'ssh' in services.stdout"

    - name: /etc/hosts has pigsty-lite marker
      ansible.builtin.command: grep -q "MANAGED BY pigsty-lite" /etc/hosts
      changed_when: false

    - name: sysctl values applied
      ansible.builtin.command: sysctl -n vm.swappiness
      register: swappiness
      changed_when: false
    - ansible.builtin.assert:
        that:
          - swappiness.stdout | int == 10
```

- [ ] **Step 2: Run molecule to verify it fails**

Run: `cd tests/molecule/node && molecule test`
Expected: converge fails ("role 'node' not found").

- [ ] **Step 3: Create `roles/node/meta/main.yml`**

```yaml
---
galaxy_info:
  role_name: node
  author: pigsty-lite
  description: Baseline node OS configuration (hostname, hosts, firewalld, sysctl).
  license: Apache-2.0
  min_ansible_version: "2.16"
  platforms:
    - name: EL
      versions: ["10"]
  galaxy_tags: [postgresql, node]
dependencies: []
```

- [ ] **Step 4: Create `roles/node/defaults/main.yml`**

```yaml
---
node_set_hostname: true
node_manage_etc_hosts: true
node_firewalld_default_zone: "{{ firewalld_default_zone | default('public') }}"
node_firewalld_baseline_services: [ssh]
node_sysctl:
  vm.swappiness: 10
  vm.dirty_ratio: 20
  vm.dirty_background_ratio: 5
  net.ipv4.tcp_keepalive_time: 60
  net.ipv4.tcp_keepalive_intvl: 10
  net.ipv4.tcp_keepalive_probes: 6
node_journald_max_use: "1G"
node_journald_max_file_sec: "1month"
```

- [ ] **Step 5: Create `roles/node/handlers/main.yml`**

```yaml
---
- name: reload firewalld
  ansible.builtin.command: firewall-cmd --reload
  changed_when: true

- name: restart journald
  ansible.builtin.systemd:
    name: systemd-journald
    state: restarted
```

- [ ] **Step 6: Create `roles/node/templates/hosts.j2`**

```jinja
# MANAGED BY pigsty-lite — DO NOT EDIT
# Entries below are derived from inventory groups: postgres, monitor, backup_store, etcd.

127.0.0.1   localhost localhost.localdomain
::1         localhost localhost.localdomain

{% set seen = [] %}
{% for grp in ['monitor', 'backup_store', 'postgres', 'etcd'] %}
{% for host in groups.get(grp, []) %}
{% set ip = hostvars[host].ansible_host | default('') %}
{% if ip and host not in seen %}
{{ '%-15s' | format(ip) }} {{ host }} {{ host }}.{{ cluster_domain | default('local') }}
{% set _ = seen.append(host) %}
{% endif %}
{% endfor %}
{% endfor %}
```

- [ ] **Step 7: Create `roles/node/templates/90-pigsty-lite.conf.j2`**

```jinja
# MANAGED BY pigsty-lite node role
{% for key, val in node_sysctl.items() %}
{{ key }} = {{ val }}
{% endfor %}
```

- [ ] **Step 8: Create `roles/node/tasks/main.yml`**

```yaml
---
- name: Set hostname to inventory host
  ansible.builtin.hostname:
    name: "{{ inventory_hostname }}"
  when: node_set_hostname | bool

- name: Render /etc/hosts from inventory
  ansible.builtin.template:
    src: hosts.j2
    dest: /etc/hosts
    owner: root
    group: root
    mode: "0644"
  when: node_manage_etc_hosts | bool

- name: Apply sysctl tuning
  ansible.builtin.template:
    src: 90-pigsty-lite.conf.j2
    dest: /etc/sysctl.d/90-pigsty-lite.conf
    owner: root
    group: root
    mode: "0644"
  register: sysctl_render

- name: Reload sysctl
  ansible.builtin.command: sysctl --system
  when: sysctl_render.changed
  changed_when: true

- name: Configure journald sizing
  ansible.builtin.copy:
    dest: /etc/systemd/journald.conf.d/10-pigsty-lite.conf
    mode: "0644"
    content: |
      # MANAGED BY pigsty-lite node role
      [Journal]
      SystemMaxUse={{ node_journald_max_use }}
      MaxFileSec={{ node_journald_max_file_sec }}
  notify: restart journald

- name: Ensure firewalld is enabled and running
  ansible.builtin.systemd:
    name: firewalld
    state: started
    enabled: true

- name: Set firewalld default zone
  ansible.builtin.command: firewall-cmd --set-default-zone={{ node_firewalld_default_zone }}
  register: set_zone
  changed_when: "'success' in set_zone.stdout"

- name: Open baseline services in default zone
  ansible.posix.firewalld:
    service: "{{ item }}"
    zone: "{{ node_firewalld_default_zone }}"
    state: enabled
    permanent: true
    immediate: true
  loop: "{{ node_firewalld_baseline_services }}"
```

- [ ] **Step 9: Create `roles/node/README.md`**

```markdown
# node

Baseline node OS configuration: hostname, /etc/hosts from inventory, sysctl
tuning, journald sizing, firewalld baseline (ssh open).

## Inputs (selected)
- `node_firewalld_baseline_services` (default `[ssh]`)
- `node_sysctl` (dict of key/value pairs)
- `node_journald_max_use` (default `1G`)

## Tags
None.
```

- [ ] **Step 10: Re-run molecule**

Run: `cd tests/molecule/node && molecule test`
Expected: passes including idempotence.

- [ ] **Step 11: Commit**

```bash
git add roles/node/ tests/molecule/node/
git commit -m "feat(node): hostname, hosts, sysctl, journald, firewalld baseline"
```

---

## Task 12: `roles/ca` — localhost-only CA generation

**Files:**
- Create: `roles/ca/meta/main.yml`
- Create: `roles/ca/defaults/main.yml`
- Create: `roles/ca/tasks/main.yml`
- Create: `roles/ca/README.md`
- Create: `tests/molecule/ca/molecule.yml`
- Create: `tests/molecule/ca/converge.yml`
- Create: `tests/molecule/ca/verify.yml`

- [ ] **Step 1: Write Molecule scenario (delegate_to localhost; no real platform needed beyond a Python env)**

Path: `tests/molecule/ca/molecule.yml`

```yaml
---
driver:
  name: default
platforms:
  - name: localhost
    groups: [localhost]
provisioner:
  name: ansible
  config_options:
    defaults:
      inventory: ./inventory.yml
  inventory:
    host_vars:
      localhost:
        ansible_connection: local
        ansible_python_interpreter: "{{ ansible_playbook_python }}"
verifier:
  name: ansible
scenario:
  test_sequence:
    - destroy
    - create
    - converge
    - idempotence
    - verify
    - destroy
```

Path: `tests/molecule/ca/converge.yml`

```yaml
---
- name: Converge
  hosts: localhost
  gather_facts: false
  vars:
    ca_dir: "{{ playbook_dir }}/_tmp_pki/ca"
  roles:
    - role: ca
```

Path: `tests/molecule/ca/verify.yml`

```yaml
---
- name: Verify
  hosts: localhost
  gather_facts: false
  vars:
    ca_dir: "{{ playbook_dir }}/_tmp_pki/ca"
  tasks:
    - name: ca.key exists, mode 0600
      ansible.builtin.stat:
        path: "{{ ca_dir }}/ca.key"
      register: ca_key
    - ansible.builtin.assert:
        that:
          - ca_key.stat.exists
          - ca_key.stat.mode == "0600"

    - name: ca.crt exists, mode 0644
      ansible.builtin.stat:
        path: "{{ ca_dir }}/ca.crt"
      register: ca_crt
    - ansible.builtin.assert:
        that:
          - ca_crt.stat.exists
          - ca_crt.stat.mode == "0644"

    - name: ca.crt is a valid x509 with cluster CN
      community.crypto.x509_certificate_info:
        path: "{{ ca_dir }}/ca.crt"
      register: info
    - ansible.builtin.assert:
        that:
          - info.subject.commonName is search("pigsty-lite")

    - name: Clean tmp dir
      ansible.builtin.file:
        path: "{{ playbook_dir }}/_tmp_pki"
        state: absent
```

- [ ] **Step 2: Run molecule and observe failure**

Run: `cd tests/molecule/ca && molecule test`
Expected: converge fails ("role 'ca' not found").

- [ ] **Step 3: Create `roles/ca/meta/main.yml`**

```yaml
---
galaxy_info:
  role_name: ca
  author: pigsty-lite
  description: Generate a self-signed CA on the control node.
  license: Apache-2.0
  min_ansible_version: "2.16"
  platforms:
    - name: EL
      versions: ["10"]
  galaxy_tags: [postgresql, pki, ca]
dependencies: []
```

- [ ] **Step 4: Create `roles/ca/defaults/main.yml`**

```yaml
---
ca_dir: "{{ playbook_dir | dirname }}/pki/ca"
ca_common_name: "pigsty-lite CA — {{ cluster_name | default('pigsty-lite') }}"
ca_organization: "pigsty-lite"
ca_key_size: 4096
ca_valid_days: 3650
```

- [ ] **Step 5: Create `roles/ca/tasks/main.yml`**

```yaml
---
- name: Ensure CA directory exists
  ansible.builtin.file:
    path: "{{ ca_dir }}"
    state: directory
    mode: "0700"

- name: Generate CA private key (idempotent — only if absent)
  community.crypto.openssl_privatekey:
    path: "{{ ca_dir }}/ca.key"
    size: "{{ ca_key_size }}"
    type: RSA
    mode: "0600"
    state: present
    backup: false

- name: Generate CSR for self-signed CA
  community.crypto.openssl_csr:
    path: "{{ ca_dir }}/ca.csr"
    privatekey_path: "{{ ca_dir }}/ca.key"
    common_name: "{{ ca_common_name }}"
    organization_name: "{{ ca_organization }}"
    basic_constraints: ["CA:TRUE"]
    basic_constraints_critical: true
    key_usage: ["digitalSignature", "keyCertSign", "cRLSign"]
    key_usage_critical: true
    mode: "0640"

- name: Self-sign CA certificate
  community.crypto.x509_certificate:
    path: "{{ ca_dir }}/ca.crt"
    privatekey_path: "{{ ca_dir }}/ca.key"
    csr_path: "{{ ca_dir }}/ca.csr"
    provider: selfsigned
    selfsigned_not_after: "+{{ ca_valid_days }}d"
    mode: "0644"
```

- [ ] **Step 6: Create `roles/ca/README.md`**

```markdown
# ca

Generates a self-signed CA on the control node (`delegate_to: localhost`
implicit via running the play on localhost). Idempotent: if `ca.key` and
`ca.crt` exist, they are not regenerated.

## Inputs
- `ca_dir` (default `pki/ca/` at repo root)
- `ca_common_name` (default `pigsty-lite CA — <cluster_name>`)
- `ca_valid_days` (default 3650)
- `ca_key_size` (default 4096)

## Outputs on disk
- `{{ ca_dir }}/ca.key` (0600)
- `{{ ca_dir }}/ca.crt` (0644)
- `{{ ca_dir }}/ca.csr` (0640)
```

- [ ] **Step 7: Re-run molecule**

Run: `cd tests/molecule/ca && molecule test`
Expected: scenario passes including idempotence.

- [ ] **Step 8: Commit**

```bash
git add roles/ca/ tests/molecule/ca/
git commit -m "feat(ca): self-signed CA generation on control node"
```

---

## Task 13: `roles/certs` — per-host certs signed by the CA

**Files:**
- Create: `roles/certs/meta/main.yml`
- Create: `roles/certs/defaults/main.yml`
- Create: `roles/certs/tasks/main.yml`
- Create: `roles/certs/README.md`
- Create: `tests/molecule/certs/molecule.yml`
- Create: `tests/molecule/certs/prepare.yml`
- Create: `tests/molecule/certs/converge.yml`
- Create: `tests/molecule/certs/verify.yml`

- [ ] **Step 1: Write Molecule scenario**

Path: `tests/molecule/certs/molecule.yml`

```yaml
---
driver:
  name: podman
platforms:
  - name: pigsty-lite-certs-rocky10
    image: docker.io/library/rockylinux:10
    pre_build_image: true
    privileged: true
    command: /usr/sbin/init
    volume_mounts:
      - "/sys/fs/cgroup:/sys/fs/cgroup:rw"
    capabilities: [SYS_ADMIN]
    systemd: always
provisioner:
  name: ansible
  inventory:
    group_vars:
      all:
        cluster_name: pigsty-lite-test
        cluster_domain: test.local
        pki_dir: /etc/pki/pigsty-lite
verifier:
  name: ansible
scenario:
  test_sequence:
    - destroy
    - create
    - prepare
    - converge
    - idempotence
    - verify
    - destroy
```

Path: `tests/molecule/certs/prepare.yml`

```yaml
---
- name: Prepare — generate CA on control side
  hosts: localhost
  gather_facts: false
  vars:
    ca_dir: "{{ playbook_dir }}/_tmp_ca"
  roles:
    - role: ca
```

Path: `tests/molecule/certs/converge.yml`

```yaml
---
- name: Converge
  hosts: all
  become: true
  vars:
    ca_dir: "{{ molecule_ephemeral_directory }}/_tmp_ca"
  pre_tasks:
    - name: Sync CA dir from control to facts
      ansible.builtin.set_fact:
        certs_ca_dir_on_control: "{{ ca_dir }}"
  roles:
    - role: certs
```

Path: `tests/molecule/certs/verify.yml`

```yaml
---
- name: Verify
  hosts: all
  become: true
  tasks:
    - name: Host cert exists at /etc/pki/pigsty-lite/<host>.crt
      ansible.builtin.stat:
        path: "/etc/pki/pigsty-lite/{{ inventory_hostname }}.crt"
      register: hc
    - ansible.builtin.assert:
        that:
          - hc.stat.exists
          - hc.stat.mode == "0644"

    - name: Host key exists, mode 0640
      ansible.builtin.stat:
        path: "/etc/pki/pigsty-lite/{{ inventory_hostname }}.key"
      register: hk
    - ansible.builtin.assert:
        that:
          - hk.stat.exists
          - hk.stat.mode == "0640"

    - name: CA cert is installed on host
      ansible.builtin.stat:
        path: "/etc/pki/pigsty-lite/ca.crt"
      register: cac
    - ansible.builtin.assert:
        that:
          - cac.stat.exists
```

- [ ] **Step 2: Run molecule to verify it fails**

Run: `cd tests/molecule/certs && molecule test`
Expected: converge fails ("role 'certs' not found").

- [ ] **Step 3: Create `roles/certs/meta/main.yml`**

```yaml
---
galaxy_info:
  role_name: certs
  author: pigsty-lite
  description: Issue per-host certs from the pigsty-lite CA.
  license: Apache-2.0
  min_ansible_version: "2.16"
  platforms:
    - name: EL
      versions: ["10"]
  galaxy_tags: [postgresql, pki, certs]
dependencies: []
```

- [ ] **Step 4: Create `roles/certs/defaults/main.yml`**

```yaml
---
certs_ca_dir_on_control: "{{ playbook_dir | dirname }}/pki/ca"
certs_pki_dir: "{{ pki_dir | default('/etc/pki/pigsty-lite') }}"
certs_validity_days: "{{ cert_validity_days | default(730) }}"
certs_renewal_window_days: "{{ cert_renewal_window_days | default(30) }}"
certs_key_size: 2048
certs_subject_alternative_names:
  - "DNS:{{ inventory_hostname }}"
  - "DNS:{{ inventory_hostname }}.{{ cluster_domain | default('local') }}"
  - "IP:{{ ansible_host | default(ansible_facts.default_ipv4.address | default('127.0.0.1')) }}"
```

- [ ] **Step 5: Create `roles/certs/tasks/main.yml`**

```yaml
---
- name: Ensure host PKI directory exists
  ansible.builtin.file:
    path: "{{ certs_pki_dir }}"
    state: directory
    owner: root
    group: root
    mode: "0755"

- name: Distribute CA certificate to host
  ansible.builtin.copy:
    src: "{{ certs_ca_dir_on_control }}/ca.crt"
    dest: "{{ certs_pki_dir }}/ca.crt"
    owner: root
    group: root
    mode: "0644"

- name: Generate host private key (on target)
  community.crypto.openssl_privatekey:
    path: "{{ certs_pki_dir }}/{{ inventory_hostname }}.key"
    size: "{{ certs_key_size }}"
    type: RSA
    mode: "0640"
    owner: root
    group: root

- name: Generate host CSR
  community.crypto.openssl_csr:
    path: "{{ certs_pki_dir }}/{{ inventory_hostname }}.csr"
    privatekey_path: "{{ certs_pki_dir }}/{{ inventory_hostname }}.key"
    common_name: "{{ inventory_hostname }}"
    subject_alt_name: "{{ certs_subject_alternative_names }}"
    mode: "0640"

- name: Fetch CSR back to control node
  ansible.builtin.fetch:
    src: "{{ certs_pki_dir }}/{{ inventory_hostname }}.csr"
    dest: "{{ certs_ca_dir_on_control }}/csrs/{{ inventory_hostname }}.csr"
    flat: true

- name: Sign CSR on control node
  delegate_to: localhost
  become: false
  community.crypto.x509_certificate:
    path: "{{ certs_ca_dir_on_control }}/issued/{{ inventory_hostname }}.crt"
    csr_path: "{{ certs_ca_dir_on_control }}/csrs/{{ inventory_hostname }}.csr"
    ownca_path: "{{ certs_ca_dir_on_control }}/ca.crt"
    ownca_privatekey_path: "{{ certs_ca_dir_on_control }}/ca.key"
    provider: ownca
    ownca_not_after: "+{{ certs_validity_days }}d"
    mode: "0644"

- name: Copy signed cert back to host
  ansible.builtin.copy:
    src: "{{ certs_ca_dir_on_control }}/issued/{{ inventory_hostname }}.crt"
    dest: "{{ certs_pki_dir }}/{{ inventory_hostname }}.crt"
    owner: root
    group: root
    mode: "0644"
```

- [ ] **Step 6: Create `roles/certs/README.md`**

```markdown
# certs

Issues per-host certificates signed by the pigsty-lite CA. The CA must
already exist on the control node (`roles/ca`).

## Flow
1. Ensure `/etc/pki/pigsty-lite/` exists on target.
2. Distribute `ca.crt` to target.
3. Generate host key on target.
4. Generate CSR on target; fetch back to control.
5. Sign CSR with the CA on control node.
6. Copy signed cert back to target.

## Inputs
- `certs_pki_dir` (default `/etc/pki/pigsty-lite`)
- `certs_validity_days` (default 730)
- `certs_subject_alternative_names` (auto-built; override only if needed)

## Idempotency
`community.crypto.openssl_privatekey`, `openssl_csr`, and
`x509_certificate` are idempotent by default. Renewal logic for certs
within `cert_renewal_window_days` is added in a later sub-plan; P0 leaves
renewal to manual re-run.
```

- [ ] **Step 7: Re-run molecule**

Run: `cd tests/molecule/certs && molecule test`
Expected: passes including idempotence.

- [ ] **Step 8: Commit**

```bash
git add roles/certs/ tests/molecule/certs/
git commit -m "feat(certs): issue per-host certs from pigsty-lite CA"
```

---

## Task 14: P0 playbooks and `site.yml`

**Files:**
- Create: `playbooks/_preflight.yml`
- Create: `playbooks/_ca.yml`
- Create: `playbooks/_node.yml`
- Create: `playbooks/preflight.yml`
- Create: `playbooks/site.yml`
- Create: `playbooks/tags.md`
- Create: `files/firewalld/services/.gitkeep`

- [ ] **Step 1: Create `playbooks/_preflight.yml`**

```yaml
---
- name: P0 preflight — validate every host before any change
  hosts: all
  become: true
  gather_facts: true
  roles:
    - role: preflight
```

- [ ] **Step 2: Create `playbooks/_ca.yml`**

```yaml
---
- name: P0 CA — generate self-signed CA on the control node
  hosts: localhost
  gather_facts: false
  connection: local
  roles:
    - role: ca
```

- [ ] **Step 3: Create `playbooks/_node.yml`**

```yaml
---
- name: P0 node — repos, OS baseline, per-host certs
  hosts: all
  become: true
  gather_facts: true
  roles:
    - role: repos
    - role: node
    - role: certs
```

- [ ] **Step 4: Create `playbooks/preflight.yml` (operator alias)**

```yaml
---
# Operator entry: validate without making changes.
- import_playbook: _preflight.yml
```

- [ ] **Step 5: Create `playbooks/site.yml`**

```yaml
---
# P0 site.yml — full deploy of pigsty-lite (P0 scope only).
# Later sub-plans (P1..P6) extend this file as they ship.
- import_playbook: _preflight.yml
- import_playbook: _ca.yml
- import_playbook: _node.yml
```

- [ ] **Step 6: Create `playbooks/tags.md`**

```markdown
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
- `--tags preflight` — only run the preflight role.
- `--tags ca` — only (re)generate the CA on localhost.
```

- [ ] **Step 7: Create the firewalld services dir placeholder**

```bash
mkdir -p files/firewalld/services
touch files/firewalld/services/.gitkeep
```

- [ ] **Step 8: Verify `--syntax-check`**

Run:
```bash
./configure -s -f tests/configure/fixtures/ha.rsp.yml
ansible-playbook playbooks/site.yml --syntax-check
```
Expected: both exit 0.

- [ ] **Step 9: Verify `--check --diff` mode against the generated inventory (no real hosts needed; Ansible will fail at the SSH connection step, but the syntax + playbook structure is what we're validating)**

Run:
```bash
ansible-playbook playbooks/site.yml --check --diff -e ansible_check_mode=true \
  --connection=local --limit localhost --tags ca
```
Expected: the CA role runs in check mode and exits 0; the file system remains unchanged.

- [ ] **Step 10: Commit**

```bash
git add playbooks/ files/firewalld/services/.gitkeep
git commit -m "feat(playbooks): site, _preflight, _ca, _node for P0"
```

---

## Task 15: GitHub Actions — lint workflow

**Files:**
- Create: `.github/workflows/lint.yml`

- [ ] **Step 1: Create `lint.yml`**

```yaml
name: lint
on:
  pull_request:
  push:
    branches: [main]

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Set up Node (for markdownlint-cli2)
        uses: actions/setup-node@v4
        with:
          node-version: "20"

      - name: Install python tooling
        run: |
          python -m pip install --upgrade pip
          python -m pip install yamllint ansible-lint ruff ansible-core==2.16.* pyyaml pytest

      - name: Install collections
        run: ansible-galaxy collection install -r requirements.yml -p ./collections

      - name: Install markdownlint
        run: npm install -g markdownlint-cli2

      - name: yamllint
        run: yamllint .

      - name: ansible-lint
        run: ansible-lint

      - name: ruff check
        run: ruff check .

      - name: ruff format check
        run: ruff format --check .

      - name: pytest (configure script unit tests)
        run: python -m pytest tests/configure -v

      - name: markdownlint
        run: markdownlint-cli2 "**/*.md" "#collections" "#.ansible"

      - name: xmllint firewalld services
        run: |
          if compgen -G "files/firewalld/services/*.xml" > /dev/null; then
            xmllint --noout files/firewalld/services/*.xml
          fi
```

- [ ] **Step 2: Sanity-check locally with `act` if available, otherwise just commit and let GitHub run it**

Run (optional): `act -j lint`

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/lint.yml
git commit -m "ci: add lint workflow (yamllint, ansible-lint, ruff, pytest, markdownlint)"
```

---

## Task 16: GitHub Actions — molecule workflow (podman roles only)

**Files:**
- Create: `.github/workflows/molecule.yml`

- [ ] **Step 1: Create `molecule.yml`**

```yaml
name: molecule
on:
  pull_request:
  push:
    branches: [main]

jobs:
  molecule:
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        role:
          - preflight
          - repos
          - node
          - ca
          - certs
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install python tooling
        run: |
          python -m pip install --upgrade pip
          python -m pip install \
            "ansible-core==2.16.*" \
            "molecule==24.*" \
            "molecule-plugins[podman]==23.*" \
            pytest

      - name: Install collections
        run: ansible-galaxy collection install -r requirements.yml -p ./collections

      - name: molecule ${{ matrix.role }}
        env:
          ANSIBLE_COLLECTIONS_PATH: ${{ github.workspace }}/collections
        run: |
          cd tests/molecule/${{ matrix.role }}
          molecule --debug test
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/molecule.yml
git commit -m "ci: molecule workflow for podman-driver P0 roles"
```

---

## Task 17: Operator docs — first-run guide

**Files:**
- Create: `docs/operations/firstrun.md`

- [ ] **Step 1: Create the doc**

```markdown
# First run

This guide walks through the minimal pigsty-lite P0 workflow on a fresh
control node. P0 ships the cross-cutting pieces (preflight, repos, node
baseline, CA, per-host certs). Roles for etcd, PostgreSQL, monitoring,
backups, and reverse proxy ship in later sub-plans.

## Prerequisites

Control node (the machine you run Ansible from):

- Linux or macOS
- Python 3.11+
- ansible-core 2.16+
- git, make, gpg
- One-time: `make init` (installs Galaxy collections and roles)

Target hosts (1 for `single`, 4 for `ha`):

- RHEL 10, Rocky 10, or Alma 10
- SELinux in `enforcing` mode (preflight will fail otherwise)
- firewalld installed
- SSH access from the control node with `become` privileges

## Steps

1. **Generate a response file.**

   ```bash
   cp responses/single.rsp.yml.example responses/site.rsp.yml
   $EDITOR responses/site.rsp.yml
   ```

2. **Validate.**

   ```bash
   ./configure --validate responses/site.rsp.yml
   ```

3. **Generate inventory + variables.**

   ```bash
   ./configure -s -f responses/site.rsp.yml
   ```

   This writes `inventory/site.yml` and `group_vars/response.yml`.

4. **Dry-run.**

   ```bash
   make plan
   ```

5. **Deploy.**

   ```bash
   make deploy
   ```

After P0 the deploy ends with every host having: PGDG enabled, baseline
firewalld, sysctl tuning, and `/etc/pki/pigsty-lite/<host>.{crt,key}`
plus `ca.crt`.

## Troubleshooting

- **`preflight` fails on SELinux**: confirm `getenforce` returns `Enforcing`
  on every host. Reboot or `setenforce 1` if it was disabled at runtime.
- **`dnf install` fails on PGDG repo**: confirm internet access to
  `download.postgresql.org` or proxy via `proxy_env` in inventory.
- **`certs` task hangs on CSR fetch**: control-node user lacks read access
  to `pki/ca/`. Run from the user that ran `_ca.yml`.
```

- [ ] **Step 2: Commit**

```bash
git add docs/operations/firstrun.md
git commit -m "docs(ops): P0 first-run operator guide"
```

---

## Task 18: README update — reflect implemented state

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Edit README.md to flag P0 as in progress**

Replace the **Status** line near the top with:

```markdown
**Status:** P0 (Foundation) in progress. Scaffolding + lint + preflight, repos, node,
CA, and per-host certs roles complete. Subsequent sub-plans (P1 etcd, P2 PostgreSQL HA,
P3 provisioning, P4 backups, P5 monitoring, P6 lifecycle/portability) pending.
The architecture and scope are defined in
[`docs/superpowers/specs/2026-05-12-pigsty-lite-design.md`](docs/superpowers/specs/2026-05-12-pigsty-lite-design.md).
```

Add a new section before **Credit**:

```markdown
## Roadmap

| Sub-plan | Scope | Status |
|---|---|---|
| P0 | Foundation: scaffolding, configure CLI, preflight/repos/node/ca/certs | in progress |
| P1 | etcd cluster | pending |
| P2 | PostgreSQL + Patroni + pgBouncer + HAProxy + VIP | pending |
| P3 | Provisioning (users, databases, extensions, HBA) | pending |
| P4 | Backups (pgBackRest, repo host, S3 offsite, PITR) | pending |
| P5 | Monitoring stack (VictoriaMetrics, VictoriaLogs, Grafana, nginx_proxy) | pending |
| P6 | Lifecycle ops + portability bundle | pending |
| P7 | Integration tests (libvirt, chaos) | pending |
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: README — roadmap and P0 status"
```

---

## Task 19: Smoke test the whole P0 flow against a real RHEL 10 host (optional but recommended before declaring P0 done)

- [ ] **Step 1: Stand up one Rocky 10 or Alma 10 VM with SSH access from the control node and SELinux enforcing.**

- [ ] **Step 2: Edit `responses/site.rsp.yml`** (copy from `single.rsp.yml.example`) so `pgnode01.ip` and `pgmon01.ip` match the VM IP (use the same IP for both in single-VM mode; pigsty-lite groups will overlap).

- [ ] **Step 3: Run silently**

```bash
./configure -s -f responses/site.rsp.yml
make plan
make deploy
```

- [ ] **Step 4: Verify on the target VM**

```bash
ssh <target> 'dnf repolist enabled | grep pgdg && \
              firewall-cmd --list-services | grep ssh && \
              ls -l /etc/pki/pigsty-lite/'
```
Expected: PGDG repo enabled, ssh in firewalld services, three files in `/etc/pki/pigsty-lite/` (`ca.crt`, `<host>.crt`, `<host>.key`).

- [ ] **Step 5: Re-run deploy and confirm zero changes**

```bash
make deploy
```
Expected: every task reports `ok=...; changed=0`.

- [ ] **Step 6: Tag the P0 milestone**

```bash
git tag p0-foundation
```

(No commit needed for this step.)

---

## Self-review (checked while drafting)

**Spec coverage check (vs the design doc):**

- §2 Scope, OS/profiles → covered by `_response_schema.py` enforcing single/ha and node role counts.
- §3.3 Storage assumptions → preflight checks SELinux + packages; block-device check deferred to P1+ where roles touch the relevant paths.
- §3.4 Module dependency graph → `site.yml` imports in the documented order (P0 only includes preflight → ca → node).
- §4 Roles table → P0 implements `preflight`, `repos`, `node`, `ca`, `certs`. Others scheduled in later sub-plans.
- §4.2 Role design principles → role README, defaults, idempotency-via-Molecule, `community.*` modules, no `tags: always`, no role-to-role variable references. All applied.
- §5 Repo layout → matches the layout in the spec.
- §5.1 site.yml — P0 imports the subset of plays the spec lists; later plans will extend.
- §6 firewalld baseline → P0 opens only `ssh` in the default zone. Custom XMLs come with the roles that need them (P1+).
- §6.3 SELinux → preflight asserts enforcing; later roles add per-component fcontexts/ports.
- §7 Variable layering → operator-facing `group_vars/response.yml` is generated; role variables are all prefixed.
- §7.3 Response file → schema enforces structure; tests cover both profiles + invalid input.
- §7.4 Secrets → CA private key on control node only, mode 0600. `artifacts/credentials.txt` is referenced but not produced yet — that's a P2 concern (when we generate replicator/dbsu/monitor passwords). P0 doesn't need it.
- §7.7 CA & cert renewal → generation done in P0; renewal-window detection deferred (noted in certs README).
- §13 Testing → lint (Layer 1) + Molecule podman matrix (Layer 2 GitHub-CI-safe) are in place; libvirt scenarios + integration tests come with the roles that need real systemd/SELinux.

**Placeholder scan:** Skimmed — no TBD, TODO, "implement later", or unspecified steps.

**Type / name consistency:** Cross-checked variable names (`postgres_*`, `cluster_*`, `ca_*`, `cert_*`, `repos_*`, `node_*`, `preflight_*`, `certs_*`), function names (`generate`, `validate`), file paths (`bin/_response_schema.py`, `bin/_generate_inventory.py`, `bin/_generate_response_vars.py`), and CLI flag conventions. All consistent.

**Scope check:** ~30 atomic tasks for the foundation layer. Each task is independently committable and produces a green CI run on its own. P0 ends at a working CA + baseline-host deploy on RHEL 10; that's a sensible cut.
