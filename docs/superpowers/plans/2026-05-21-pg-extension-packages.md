# PostgreSQL Extension Packages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `postgres_extension_packages` so response files can install PostgreSQL extension RPMs on every PG node independently from `postgres_extensions` creation.

**Architecture:** The response file gets a new file-only `postgres.extension_packages` list that the schema validates and the generator emits as `postgres_extension_packages`. The `postgres` role installs those RPMs after core PostgreSQL packages on all PG nodes. The existing `provision` role remains unchanged and continues to create SQL extensions from `postgres_extensions`.

**Tech Stack:** Python 3 stdlib + PyYAML, pytest, Ansible role YAML, Molecule with podman.

Spec: `docs/superpowers/specs/2026-05-21-pg-extension-packages-design.md`

---

## File Structure

- `bin/_response_schema.py` — add `_validate_extension_packages()` and call it from `_validate_postgres()`.
- `bin/_generate_response_vars.py` — emit `postgres_extension_packages` adjacent to `postgres_extensions`.
- `tests/configure/test_schema.py` — add schema tests for `postgres.extension_packages`.
- `tests/configure/test_generate_response_vars.py` — add generator tests for `postgres_extension_packages`.
- `tests/configure/test_postgres_role_static.py` — add static checks for the role default and install task.
- `roles/postgres/defaults/main.yml` — define `postgres_extension_packages: []`.
- `roles/postgres/tasks/main.yml` — install extension RPMs when the list is non-empty.
- `tests/molecule/postgres/molecule/default/converge.yml` — declare `postgres_extension_packages: ["pgvector_{{ postgres_version }}"]`.
- `tests/molecule/postgres/molecule/default/verify.yml` — assert the `pgvector_18` RPM is installed.
- `responses/ha.rsp.yml.example`, `responses/spof.rsp.yml.example` — add commented `extension_packages` guidance directly above `extensions`.
- `roles/postgres/README.md` — document the variable and its decoupling from `postgres_extensions`.

Run Python tests with `.venv/bin/pytest`. Run Molecule with `uv run --with molecule --with molecule-plugins[podman] molecule test -s default` from `tests/molecule/postgres` if the local Molecule environment is not already installed.

---

## Task 1: Validate `postgres.extension_packages`

**Files:**
- Modify: `bin/_response_schema.py`
- Test: `tests/configure/test_schema.py`

- [ ] **Step 1: Write the failing schema tests**

Append these tests after `test_postgres_extensions_dict_requires_name()` in `tests/configure/test_schema.py`:

```python
def test_postgres_extension_packages_accepts_list_of_strings():
    data = _load("spof.rsp.yml")
    data["postgres"]["extension_packages"] = ["pgvector_{{ postgres_version }}"]
    validate(data)


def test_postgres_extension_packages_must_be_list():
    data = _load("spof.rsp.yml")
    data["postgres"]["extension_packages"] = "pgvector_18"
    with pytest.raises(SchemaError, match=r"postgres\.extension_packages: must be a list"):
        validate(data)


def test_postgres_extension_packages_rejects_non_string_entries():
    data = _load("spof.rsp.yml")
    data["postgres"]["extension_packages"] = ["pgvector_18", 42]
    with pytest.raises(
        SchemaError, match=r"postgres\.extension_packages\[1\]: expected string"
    ):
        validate(data)
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `.venv/bin/pytest tests/configure/test_schema.py -k extension_packages -v`

Expected: FAIL for the two rejection tests because `postgres.extension_packages` is not validated yet.

- [ ] **Step 3: Add the validator**

In `bin/_response_schema.py`, add this function immediately after `_validate_extensions()`:

```python
def _validate_extension_packages(postgres: dict) -> None:
    packages = postgres.get("extension_packages", [])
    if not isinstance(packages, list):
        raise SchemaError("postgres.extension_packages: must be a list")
    for index, package in enumerate(packages):
        if not isinstance(package, str):
            raise SchemaError(f"postgres.extension_packages[{index}]: expected string")
```

- [ ] **Step 4: Wire the validator into `_validate_postgres()`**

In `bin/_response_schema.py`, in `_validate_postgres()`, call it directly before `_validate_extensions(postgres)`:

```python
    _validate_extension_packages(postgres)
    _validate_extensions(postgres)
```

- [ ] **Step 5: Run the focused schema tests**

Run: `.venv/bin/pytest tests/configure/test_schema.py -k extension_packages -v`

Expected: PASS for all three tests.

- [ ] **Step 6: Run the full schema test file**

Run: `.venv/bin/pytest tests/configure/test_schema.py -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add bin/_response_schema.py tests/configure/test_schema.py
git commit -m "feat(configure): validate postgres extension packages"
```

---

## Task 2: Emit `postgres_extension_packages`

**Files:**
- Modify: `bin/_generate_response_vars.py`
- Test: `tests/configure/test_generate_response_vars.py`

- [ ] **Step 1: Write the failing generator tests**

In `tests/configure/test_generate_response_vars.py`, update `test_postgres_keys_namespaced()` to assert the default emitted value:

```python
def test_postgres_keys_namespaced():
    out = yaml.safe_load(generate(_load("ha.rsp.yml")))
    assert out["postgres_version"] == 18
    assert out["postgres_port"] == 5432
    assert out["postgres_tune_profile"] == "oltp"
    assert out["postgres_shared_buffer_ratio"] == 0.25
    assert out["postgres_extension_packages"] == []
    assert out["postgres_extensions"] == ["pg_stat_statements", "pgvector"]
    assert out["postgres_databases"] == [{"name": "app", "owner": "app"}]
```

Append this new test near the existing postgres generator tests:

```python
def test_postgres_extension_packages_pass_through():
    data = _load("ha.rsp.yml")
    data["postgres"]["extension_packages"] = ["pgvector_{{ postgres_version }}"]
    out = yaml.safe_load(generate(data))
    assert out["postgres_extension_packages"] == ["pgvector_{{ postgres_version }}"]
```

- [ ] **Step 2: Run the focused generator tests to verify they fail**

Run: `.venv/bin/pytest tests/configure/test_generate_response_vars.py -k "postgres_keys_namespaced or extension_packages" -v`

Expected: FAIL with `KeyError: 'postgres_extension_packages'`.

- [ ] **Step 3: Emit the generated variable**

In `bin/_generate_response_vars.py`, inside the `out` dict in `generate()`, insert `postgres_extension_packages` immediately above `postgres_extensions`:

```python
        "postgres_shared_buffer_ratio": postgres.get("shared_buffer_ratio", 0.25),
        "postgres_extension_packages": postgres.get("extension_packages", []),
        "postgres_extensions": postgres.get("extensions", []),
```

- [ ] **Step 4: Run the focused generator tests**

Run: `.venv/bin/pytest tests/configure/test_generate_response_vars.py -k "postgres_keys_namespaced or extension_packages" -v`

Expected: PASS.

- [ ] **Step 5: Run the full generator test file**

Run: `.venv/bin/pytest tests/configure/test_generate_response_vars.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add bin/_generate_response_vars.py tests/configure/test_generate_response_vars.py
git commit -m "feat(configure): emit postgres extension packages"
```

---

## Task 3: Install extension RPMs in the `postgres` role

**Files:**
- Create: `tests/configure/test_postgres_role_static.py`
- Modify: `roles/postgres/defaults/main.yml`
- Modify: `roles/postgres/tasks/main.yml`

- [ ] **Step 1: Write the failing static role tests**

Create `tests/configure/test_postgres_role_static.py`:

```python
"""Static checks for postgres role behavior."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _load_yaml(path: str):
    with (ROOT / path).open() as fh:
        return yaml.safe_load(fh)


def test_postgres_extension_packages_default_empty():
    defaults = _load_yaml("roles/postgres/defaults/main.yml")

    assert defaults["postgres_extension_packages"] == []


def test_postgres_extension_packages_install_task_after_core_packages():
    tasks = _load_yaml("roles/postgres/tasks/main.yml")
    task_names = [task.get("name") for task in tasks]

    core_index = task_names.index("Install PostgreSQL server, contrib, and libs packages")
    extension_index = task_names.index("Install PostgreSQL extension packages")

    assert core_index < extension_index

    extension_task = tasks[extension_index]
    assert extension_task["ansible.builtin.dnf"] == {
        "name": "{{ postgres_extension_packages }}",
        "state": "present",
    }
    assert extension_task["when"] == "postgres_extension_packages | length > 0"
    assert extension_task["tags"] == ["postgres", "install"]
```

- [ ] **Step 2: Run the static role tests to verify they fail**

Run: `.venv/bin/pytest tests/configure/test_postgres_role_static.py -v`

Expected: FAIL because the default and install task do not exist yet.

- [ ] **Step 3: Add the role default**

In `roles/postgres/defaults/main.yml`, add this block immediately after `postgres_support_packages`:

```yaml
# Optional RPMs that provide PostgreSQL extension files. SQL extension creation
# is separate and driven by postgres_extensions in the provision role.
postgres_extension_packages: []
```

- [ ] **Step 4: Add the install task**

In `roles/postgres/tasks/main.yml`, add this task directly after `Install PostgreSQL server, contrib, and libs packages`:

```yaml
- name: Install PostgreSQL extension packages
  ansible.builtin.dnf:
    name: "{{ postgres_extension_packages }}"
    state: present
  when: postgres_extension_packages | length > 0
  tags: [postgres, install]
```

- [ ] **Step 5: Run the static role tests**

Run: `.venv/bin/pytest tests/configure/test_postgres_role_static.py -v`

Expected: PASS.

- [ ] **Step 6: Run ansible-lint on the changed role**

Run: `ansible-lint roles/postgres`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add roles/postgres/defaults/main.yml roles/postgres/tasks/main.yml tests/configure/test_postgres_role_static.py
git commit -m "feat(postgres): install extension rpm packages"
```

---

## Task 4: Cover package installation in Molecule

**Files:**
- Modify: `tests/molecule/postgres/molecule/default/converge.yml`
- Modify: `tests/molecule/postgres/molecule/default/verify.yml`

- [ ] **Step 1: Declare pgvector in the Molecule converge vars**

In `tests/molecule/postgres/molecule/default/converge.yml`, add `postgres_extension_packages` under the existing `vars:` block:

```yaml
  vars:
    network_any_address: 0.0.0.0
    network_loopback_address: 127.0.0.1
    postgres_extension_packages:
      - "pgvector_{{ postgres_version }}"
```

- [ ] **Step 2: Add package verification**

In `tests/molecule/postgres/molecule/default/verify.yml`, add this task block after `Assert postgres binary installed`:

```yaml
    - name: Gather installed package facts
      ansible.builtin.package_facts:
        manager: auto

    - name: Assert pgvector extension RPM installed
      ansible.builtin.assert:
        that:
          - "'pgvector_18' in ansible_facts.packages"
        fail_msg: "pgvector_18 package was not installed"
```

- [ ] **Step 3: Run the postgres Molecule scenario**

Run from `tests/molecule/postgres`: `uv run --with molecule --with molecule-plugins[podman] molecule test -s default`

Expected: PASS. During converge, the postgres role installs `pgvector_18`; during verify, `package_facts` finds `pgvector_18` in `ansible_facts.packages`.

- [ ] **Step 4: Commit**

```bash
git add tests/molecule/postgres/molecule/default/converge.yml tests/molecule/postgres/molecule/default/verify.yml
git commit -m "test(postgres): verify extension rpm installation"
```

---

## Task 5: Update response examples and postgres role docs

**Files:**
- Modify: `responses/ha.rsp.yml.example`
- Modify: `responses/spof.rsp.yml.example`
- Modify: `roles/postgres/README.md`

- [ ] **Step 1: Update the HA response example**

In `responses/ha.rsp.yml.example`, replace the existing P3 comment and `extensions` line:

```yaml
  # P3 day-2 workflow: edit these declarative lists, then run make deploy.
  extensions: [pg_stat_statements, pgvector]
```

with:

```yaml
  # RPM packages providing extension files - installed on every PG node.
  # Makes extensions available (CREATE EXTENSION is separate, see below).
  # Values pass through Jinja, so "pgvector_{{ postgres_version }}" auto-syncs
  # with the PG major version. Browse available packages for your platform at:
  #   https://download.postgresql.org/pub/repos/yum/18/redhat/rhel-10-x86_64/
  extension_packages:
    - "pgvector_{{ postgres_version }}"
  # Extensions to CREATE EXTENSION, and in which database - run on the leader.
  extensions:
    - {name: vector, db: app}
    - pg_stat_statements
```

- [ ] **Step 2: Update the SPOF response example**

In `responses/spof.rsp.yml.example`, replace the existing P3 comment and `extensions` line:

```yaml
  # P3 day-2 workflow: edit these declarative lists, then run make deploy.
  extensions: [pg_stat_statements]
```

with:

```yaml
  # RPM packages providing extension files - installed on every PG node.
  # Makes extensions available (CREATE EXTENSION is separate, see below).
  # Values pass through Jinja, so "pgvector_{{ postgres_version }}" auto-syncs
  # with the PG major version. Browse available packages for your platform at:
  #   https://download.postgresql.org/pub/repos/yum/18/redhat/rhel-10-x86_64/
  extension_packages: []
  # Extensions to CREATE EXTENSION, and in which database - run on the leader.
  extensions:
    - pg_stat_statements
```

- [ ] **Step 3: Update role documentation**

In `roles/postgres/README.md`, replace the current `## Variables` section with:

```markdown
## Variables

See `defaults/main.yml`. The most important downstream contract is that
`postgres_data_dir` matches the path Patroni writes into its own
`postgresql.data_dir`. Both roles consume `group_vars/postgres.yml` for this;
do not override per-host unless you really mean it.

`postgres_extension_packages` is a list of RPM package names to install on
every PostgreSQL node after the server/contrib/libs packages. Use it for
extension file packages such as `pgvector_{{ postgres_version }}`. This only
makes extension files available; SQL extension creation is separate and remains
driven by `postgres_extensions` in the provision role.
```

- [ ] **Step 4: Run response schema tests against examples and fixtures**

Run: `.venv/bin/pytest tests/configure/test_schema.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add responses/ha.rsp.yml.example responses/spof.rsp.yml.example roles/postgres/README.md
git commit -m "docs(postgres): document extension package workflow"
```

---

## Task 6: Final verification

**Files:**
- No new file changes.

- [ ] **Step 1: Run all configure tests**

Run: `.venv/bin/pytest tests/configure -v`

Expected: PASS.

- [ ] **Step 2: Run lint for changed Ansible content**

Run: `ansible-lint roles/postgres tests/molecule/postgres/molecule/default`

Expected: PASS.

- [ ] **Step 3: Run the postgres Molecule scenario if not already run after Task 4**

Run from `tests/molecule/postgres`: `uv run --with molecule --with molecule-plugins[podman] molecule test -s default`

Expected: PASS.

- [ ] **Step 4: Inspect generated response vars manually**

Run:

```bash
uv run --with pyyaml python - <<'PY'
import yaml
from pathlib import Path
from bin._generate_response_vars import generate

response = yaml.safe_load(Path("responses/ha.rsp.yml.example").read_text())
out = yaml.safe_load(generate(response))
print(out["postgres_extension_packages"])
print(out["postgres_extensions"])
PY
```

Expected output:

```text
['pgvector_{{ postgres_version }}']
[{'name': 'vector', 'db': 'app'}, 'pg_stat_statements']
```

- [ ] **Step 5: Confirm the provision role was not changed**

Run: `git diff --name-only HEAD~5..HEAD`

Expected: the output does not include files under `roles/provision/`.

- [ ] **Step 6: Final commit if any verification-only fixes were needed**

If no fixes were needed, skip this step. If verification required small follow-up edits, inspect the exact file list first:

```bash
git status --short
```

Then add only the files shown by `git status --short` that belong to this feature. For the expected verification fixes, the command will be one of these concrete forms:

```bash
git add bin/_response_schema.py tests/configure/test_schema.py
git add bin/_generate_response_vars.py tests/configure/test_generate_response_vars.py
git add roles/postgres/defaults/main.yml roles/postgres/tasks/main.yml tests/configure/test_postgres_role_static.py
git add tests/molecule/postgres/molecule/default/converge.yml tests/molecule/postgres/molecule/default/verify.yml
git add responses/ha.rsp.yml.example responses/spof.rsp.yml.example roles/postgres/README.md
git commit -m "fix(postgres): polish extension package workflow"
```

---

## Self-Review Notes

- Spec coverage: Tasks 1 and 2 cover schema and response generation; Task 3 covers postgres role defaults and install behavior; Task 4 covers Molecule package verification; Task 5 covers response examples and role docs; Task 6 verifies no provision-role changes.
- The two variables remain decoupled: `postgres_extension_packages` only feeds `dnf`; `postgres_extensions` remains unchanged for `CREATE EXTENSION`.
- No catalog or name mapping is introduced. Operators write literal RPM package names and literal SQL extension names.
