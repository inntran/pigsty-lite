# Configurable Ansible Remote User Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a configurable Ansible remote login user (default `dba`) that operators set during `configure`, and clearly document the passwordless-SSH + NOPASSWD-sudo contract.

**Architecture:** A new optional `access` section in the response file carries `ansible_user`. The schema validates it (absent → `dba`); the response-vars generator emits `ansible_user` into `group_vars/response.yml`; the interactive wizard adds a 4th prompt and prints a connection-contract block. No connectivity check, no `ansible.cfg` change.

**Tech Stack:** Python 3 stdlib + PyYAML, pytest, Ansible (YAML config only).

Spec: `docs/superpowers/specs/2026-05-20-remote-user-design.md`

---

## File Structure

- `bin/_response_schema.py` — add `_validate_access`, wire into `validate()`.
- `bin/_generate_response_vars.py` — emit `ansible_user` key.
- `configure` — 4th wizard prompt + connection-contract block.
- `responses/ha.rsp.yml.example`, `responses/spof.rsp.yml.example` — add `access` block.
- `docs/operations/firstrun.md` — expand the SSH/sudo prerequisite.
- `tests/configure/test_schema.py` — schema tests for `access`.
- `tests/configure/test_generate_response_vars.py` — generator test for `ansible_user`.
- `tests/configure/test_configure_cli.py` — wizard prompt test.

Run tests with `.venv/bin/pytest` (the Makefile's `PYTEST` default).

---

## Task 1: Schema validation for the `access` section

**Files:**
- Modify: `bin/_response_schema.py`
- Test: `tests/configure/test_schema.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/configure/test_schema.py`:

```python
def test_access_absent_is_valid():
    data = _minimal_spof_response()
    data.pop("access", None)
    validate(data)


def test_access_custom_user_is_valid():
    data = _minimal_spof_response()
    data["access"] = {"ansible_user": "ansible-svc"}
    validate(data)


def test_access_must_be_mapping():
    data = _minimal_spof_response()
    data["access"] = "dba"
    with pytest.raises(SchemaError, match="access: must be a mapping"):
        validate(data)


def test_access_ansible_user_must_be_string():
    data = _minimal_spof_response()
    data["access"] = {"ansible_user": 42}
    with pytest.raises(SchemaError, match="access.ansible_user"):
        validate(data)


def test_access_ansible_user_must_not_be_empty():
    data = _minimal_spof_response()
    data["access"] = {"ansible_user": ""}
    with pytest.raises(SchemaError, match="access.ansible_user"):
        validate(data)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/configure/test_schema.py -k access -v`
Expected: FAIL — `test_access_must_be_mapping` etc. fail because no validation rejects a string/bad `access` (the others pass trivially since `access` is currently ignored).

- [ ] **Step 3: Add the `_validate_access` validator**

In `bin/_response_schema.py`, add this function immediately after `_validate_network` (which ends near line 93):

```python
def _validate_access(access: dict | None) -> str:
    if access is None:
        return "dba"
    if not isinstance(access, dict):
        raise SchemaError("access: must be a mapping")
    ansible_user = access.get("ansible_user", "dba")
    if not isinstance(ansible_user, str) or not ansible_user.strip():
        raise SchemaError("access.ansible_user: expected a non-empty string")
    return ansible_user
```

- [ ] **Step 4: Wire it into `validate()`**

In `bin/_response_schema.py`, in `validate()`, immediately after the line
`ip_version = _validate_network(data.get("network"))` add:

```python
    _validate_access(data.get("access"))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/configure/test_schema.py -k access -v`
Expected: PASS — all 5 tests.

- [ ] **Step 6: Run the full schema suite for regressions**

Run: `.venv/bin/pytest tests/configure/test_schema.py -v`
Expected: PASS — all tests.

- [ ] **Step 7: Commit**

```bash
git add bin/_response_schema.py tests/configure/test_schema.py
git commit -m "feat(configure): validate optional access.ansible_user section

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 2: Emit `ansible_user` into `group_vars/response.yml`

**Files:**
- Modify: `bin/_generate_response_vars.py`
- Test: `tests/configure/test_generate_response_vars.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/configure/test_generate_response_vars.py`:

```python
def test_ansible_user_defaults_to_dba_when_access_absent():
    data = _load("ha.rsp.yml")
    data.pop("access", None)
    out = yaml.safe_load(generate(data))
    assert out["ansible_user"] == "dba"


def test_ansible_user_reflects_access_section():
    data = _load("ha.rsp.yml")
    data["access"] = {"ansible_user": "ansible-svc"}
    out = yaml.safe_load(generate(data))
    assert out["ansible_user"] == "ansible-svc"
```

Note: the existing tests call `generate(_load(...))` directly; `_load` returns a
dict, so mutating it before `generate` is fine.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/configure/test_generate_response_vars.py -k ansible_user -v`
Expected: FAIL — `KeyError: 'ansible_user'`.

- [ ] **Step 3: Emit the key in the generator**

In `bin/_generate_response_vars.py`, inside `generate()`, in the `out` dict
literal, add `ansible_user` immediately after the `"cluster_domain"` entry:

```python
        "cluster_domain": response["cluster"]["domain"],
        "ansible_user": response.get("access", {}).get("ansible_user", "dba"),
        "network_ip_version": ip_version,
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/configure/test_generate_response_vars.py -k ansible_user -v`
Expected: PASS — both tests.

- [ ] **Step 5: Run the full generator suite for regressions**

Run: `.venv/bin/pytest tests/configure/test_generate_response_vars.py -v`
Expected: PASS — all tests.

- [ ] **Step 6: Commit**

```bash
git add bin/_generate_response_vars.py tests/configure/test_generate_response_vars.py
git commit -m "feat(configure): emit ansible_user into group_vars/response.yml

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 3: Wizard prompt + connection-contract block

**Files:**
- Modify: `configure`
- Test: `tests/configure/test_configure_cli.py`

The interactive wizard (`cmd_interactive`) currently prompts for profile,
cluster name, and cluster domain via `input()`, then writes only the response
file. We add a 4th prompt and a printed contract block.

- [ ] **Step 1: Write the failing test**

Append to `tests/configure/test_configure_cli.py`:

```python
def test_interactive_prompts_for_remote_user(monkeypatch, tmp_path):
    """The 4th prompt sets access.ansible_user in the written response file."""
    module = _load_configure_module()
    (tmp_path / "responses").mkdir()
    (tmp_path / "responses" / "spof.rsp.yml.example").write_text(
        (ROOT / "responses" / "spof.rsp.yml.example").read_text()
    )

    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "RESPONSE_FILE_PATH", tmp_path / "responses" / "site.rsp.yml")
    monkeypatch.setattr(module, "INVENTORY_PATH", tmp_path / "inventory" / "site.yml")
    monkeypatch.setattr(module, "RESPONSE_VARS_PATH", tmp_path / "group_vars" / "response.yml")
    monkeypatch.setattr(sys, "stdin", _TtyStdin())
    answers = iter(["pg-dev", "example.internal", "ansible-svc"])
    monkeypatch.setattr(builtins, "input", lambda _prompt: next(answers))

    rc = module.cmd_interactive(argparse.Namespace(profile="spof", no_vault=True))

    assert rc == 0
    data = yaml.safe_load((tmp_path / "responses" / "site.rsp.yml").read_text())
    assert data["access"]["ansible_user"] == "ansible-svc"


def test_interactive_remote_user_defaults_to_dba(monkeypatch, tmp_path):
    """Empty input on the remote-user prompt keeps the dba default."""
    module = _load_configure_module()
    (tmp_path / "responses").mkdir()
    (tmp_path / "responses" / "spof.rsp.yml.example").write_text(
        (ROOT / "responses" / "spof.rsp.yml.example").read_text()
    )

    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "RESPONSE_FILE_PATH", tmp_path / "responses" / "site.rsp.yml")
    monkeypatch.setattr(module, "INVENTORY_PATH", tmp_path / "inventory" / "site.yml")
    monkeypatch.setattr(module, "RESPONSE_VARS_PATH", tmp_path / "group_vars" / "response.yml")
    monkeypatch.setattr(sys, "stdin", _TtyStdin())
    answers = iter(["pg-dev", "example.internal", ""])
    monkeypatch.setattr(builtins, "input", lambda _prompt: next(answers))

    rc = module.cmd_interactive(argparse.Namespace(profile="spof", no_vault=True))

    assert rc == 0
    data = yaml.safe_load((tmp_path / "responses" / "site.rsp.yml").read_text())
    assert data["access"]["ansible_user"] == "dba"
```

The existing tests `test_interactive_response_file_starts_with_document_marker`
and `test_interactive_does_not_generate_derived_files` use a 2-item `answers`
iterator (`["pg-dev", "example.internal"]`). Adding a 4th `input()` call will
make them raise `StopIteration`. Both must be updated in Step 3.

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `.venv/bin/pytest tests/configure/test_configure_cli.py -k remote_user -v`
Expected: FAIL — `KeyError: 'access'` (the wizard does not write the section yet).

- [ ] **Step 3: Add the 4th prompt and set the `access` section**

In `configure`, in `cmd_interactive`, find this block:

```python
    profile = args.profile or input("Profile (spof|ha) [ha]: ").strip() or "ha"
    cluster_name = input("Cluster name [pg-prod]: ").strip() or "pg-prod"
    cluster_domain = input("Cluster domain [example.internal]: ").strip() or "example.internal"
```

Add a 4th prompt after it:

```python
    remote_user = input("Remote user [dba]: ").strip() or "dba"
```

Then find:

```python
    data["profile"] = profile
    data["cluster"]["name"] = cluster_name
    data["cluster"]["domain"] = cluster_domain
```

Add immediately after:

```python
    data["access"] = {"ansible_user": remote_user}
```

- [ ] **Step 4: Update the existing 2-answer wizard tests**

In `tests/configure/test_configure_cli.py`, in BOTH
`test_interactive_response_file_starts_with_document_marker` and
`test_interactive_does_not_generate_derived_files`, change:

```python
    answers = iter(["pg-dev", "example.internal"])
```

to:

```python
    answers = iter(["pg-dev", "example.internal", "dba"])
```

- [ ] **Step 5: Add the connection-contract block to the closing output**

In `configure`, in `cmd_interactive`, find the closing message:

```python
    print()
    print(
        "Edit responses/site.rsp.yml to fill IPv4/IPv6 IPs, then: make plan\n"
        "(make plan/deploy regenerate inventory/site.yml and"
        " group_vars/response.yml from the response file automatically)"
    )
    return 0
```

Replace it with:

```python
    print()
    print(
        "Edit responses/site.rsp.yml to fill IPv4/IPv6 IPs, then: make plan\n"
        "(make plan/deploy regenerate inventory/site.yml and"
        " group_vars/response.yml from the response file automatically)"
    )
    print()
    line = f"  The remote user '{remote_user}' must already have, on"
    print("+" + "-" * 62 + "+")
    print("|  BEFORE you run `make plan` / `make deploy`:" + " " * 18 + "|")
    print("|" + " " * 62 + "|")
    print("|" + line.ljust(62) + "|")
    print("|  EVERY target host:" + " " * 42 + "|")
    print("|    1. Passwordless SSH login (SSH key authentication)" + " " * 8 + "|")
    print("|    2. NOPASSWD sudo" + " " * 42 + "|")
    print("|" + " " * 62 + "|")
    print("|  pigsty-lite does not set this up. make plan will fail" + " " * 8 + "|")
    print("|  with a connection or sudo error if it is not in place." + " " * 6 + "|")
    print("+" + "-" * 62 + "+")
    return 0
```

- [ ] **Step 6: Run the wizard tests to verify they pass**

Run: `.venv/bin/pytest tests/configure/test_configure_cli.py -v`
Expected: PASS — all tests, including the two new `remote_user` tests and the
two updated existing tests.

- [ ] **Step 7: Commit**

```bash
git add configure tests/configure/test_configure_cli.py
git commit -m "feat(configure): prompt for remote user, print SSH/sudo contract

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 4: Add the `access` block to the example response files

**Files:**
- Modify: `responses/ha.rsp.yml.example`
- Modify: `responses/spof.rsp.yml.example`

There is no automated test for the example files' content; verification is by
running `configure --validate` against each (Step 3).

- [ ] **Step 1: Add the `access` block to `responses/ha.rsp.yml.example`**

In `responses/ha.rsp.yml.example`, find the `cluster:` block:

```yaml
cluster:
  name: pg-prod
  domain: example.internal
```

Insert immediately after it (before `nodes:`):

```yaml
# Ansible logs in as this user on every target host. It must have
# passwordless SSH (key auth) and NOPASSWD sudo. pigsty-lite does not
# configure this - set it up yourself. See docs/operations/firstrun.md.
access:
  ansible_user: dba
```

- [ ] **Step 2: Add the `access` block to `responses/spof.rsp.yml.example`**

In `responses/spof.rsp.yml.example`, find the `cluster:` block:

```yaml
cluster:
  name: pg-dev
  domain: example.internal
```

Insert immediately after it (before `nodes:`):

```yaml
# Ansible logs in as this user on every target host. It must have
# passwordless SSH (key auth) and NOPASSWD sudo. pigsty-lite does not
# configure this - set it up yourself. See docs/operations/firstrun.md.
access:
  ansible_user: dba
```

- [ ] **Step 3: Verify both examples still validate**

Run:
```bash
./configure --validate responses/ha.rsp.yml.example
./configure --validate responses/spof.rsp.yml.example
```
Expected: `OK` printed for each, exit 0.

- [ ] **Step 4: Verify the generator emits the configured user**

Run:
```bash
.venv/bin/python -c "import yaml; from bin._generate_response_vars import generate; print(yaml.safe_load(generate(yaml.safe_load(open('responses/ha.rsp.yml.example'))))['ansible_user'])"
```
Expected: `dba`

- [ ] **Step 5: Commit**

```bash
git add responses/ha.rsp.yml.example responses/spof.rsp.yml.example
git commit -m "docs(configure): add access.ansible_user to example response files

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 5: Document the SSH/sudo prerequisite in firstrun.md

**Files:**
- Modify: `docs/operations/firstrun.md`

The Prerequisites section currently has the bullet:
`- SSH access from the control node with `become` privileges`

- [ ] **Step 1: Expand the prerequisite bullet**

In `docs/operations/firstrun.md`, find:

```markdown
- SSH access from the control node with `become` privileges
```

Replace that single line with:

```markdown
- A login user on every target host for Ansible to connect as. This is
  `access.ansible_user` in the response file (default `dba`). It must have:
  - **Passwordless SSH login** from the control node (SSH key authentication).
  - **NOPASSWD sudo**, e.g. a file `/etc/sudoers.d/dba` containing:

    ```
    dba ALL=(ALL) NOPASSWD: ALL
    ```

  pigsty-lite does not create this user or configure SSH/sudo. If it is not in
  place, `make plan` fails with a connection or `become` error.
```

- [ ] **Step 2: Verify the doc lints**

Run: `make lint` (or, if that runs the full linter suite and is slow,
`.venv/bin/python -m mdformat --check docs/operations/firstrun.md` is not
configured here — use `make lint` and confirm no markdownlint error on
`firstrun.md`).
Expected: no markdownlint errors for `docs/operations/firstrun.md`.

- [ ] **Step 3: Commit**

```bash
git add docs/operations/firstrun.md
git commit -m "docs: document SSH key + NOPASSWD sudo prerequisite

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 6: Full verification

**Files:** none (verification only).

- [ ] **Step 1: Run the full configure test suite**

Run: `make test-configure`
Expected: PASS — all tests (the prior baseline was 110; this plan adds 9 tests
across Tasks 1-3, so expect 119 passed).

- [ ] **Step 2: End-to-end wizard smoke test is covered by the CLI tests**

No manual TTY run is required — `test_configure_cli.py` exercises
`cmd_interactive` directly. Confirm Step 1 included those tests in its count.

- [ ] **Step 3: Verify silent mode end to end**

Run:
```bash
./configure -s -f responses/ha.rsp.yml.example --no-vault
grep ansible_user group_vars/response.yml
```
Expected: `Wrote inventory/site.yml` / `Wrote group_vars/response.yml`, and
`grep` prints `ansible_user: dba`.

- [ ] **Step 4: Restore generated files**

The previous step overwrites `inventory/site.yml` and
`group_vars/response.yml` from the example. Regenerate them from the real
response file:

```bash
make regen
```
Expected: `Wrote inventory/site.yml` / `Wrote group_vars/response.yml`.

---

## Self-Review Notes

- **Spec coverage:** §1 response section → Task 4; §2 schema → Task 1; §3
  generator → Task 2; §4 `ansible.cfg` (no change) → covered by design, no task
  needed; §5 wizard prompt + block → Task 3; §6 example files → Task 4; §7
  firstrun.md → Task 5; Testing section → Tasks 1-3 + Task 6.
- **Naming:** the section is `access` with key `ansible_user` throughout — no
  collision with the existing `connection_layer` section.
- **No connectivity check:** intentional, per spec non-goals.
