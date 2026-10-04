# AIO Profile Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an `aio` profile that runs the whole SPOF stack on one `pg_primary` host, and make the response file's `backup:` block actually drive pgBackRest.

**Architecture:** The response file's `backup_*` vars are mapped onto `pgbackrest_*` in the role's defaults (repo convention: the consuming role carries the `default()` fallback). pgBackRest gains a `local` mode chosen from inventory group membership. The `aio` profile is a schema + inventory-generator change; playbooks treat it like `spof`.

**Tech Stack:** Python 3 stdlib + PyYAML + Jinja2 (pytest), Ansible, molecule (podman).

**Spec:** `docs/superpowers/specs/2026-10-03-aio-profile-design.md`

## Global Constraints

- Profiles allowed: `spof`, `ha`, `aio`.
- `aio`: exactly one node, `role: pg_primary`; VIP must be disabled.
- S3 credentials come from operator-supplied vault keys `vault_pgbackrest_s3_key` and `vault_pgbackrest_s3_key_secret`; only referenced when S3 is enabled.
- `backup.secondary_store.type` must be `s3` when enabled.
- `site.yml` imports `_pgbackrest.yml` only when `backup_enabled | default(true) | bool`.
- pgBackRest `local` mode: no TLS daemon, no 8432 firewall rule, no `tls-server-*` / `repo1-host*` lines; asserts exactly one `postgres` host.
- Unit tests: `.venv/bin/pytest tests/configure -q`. Lint: `.venv/bin/ruff check bin tests configure` and `.venv/bin/ruff format --check bin tests`.

## Review Focus

1. `backup.enabled: false` (or no `backup:` block) → pgBackRest play skipped in `site.yml`.
2. S3 disabled and vault keys absent → config renders with no repo2 lines and no undefined-variable error.
3. `pgbackrest_s3_enabled` arriving as the string `"False"` → treated as false (template uses `| bool`).
4. HA cluster where `backup_server` collapsed onto a pg node → `local` mode assertion fails with an actionable message.
5. `aio` with external monitoring → `monitor` group empty, host still in `backup_server`.

---

### Task 1: Wire `backup:` into the pgBackRest role, validate S3 type, gate the play

**Files:**
- Modify: `roles/pgbackrest/defaults/main.yml`
- Modify: `roles/pgbackrest/templates/pgbackrest.conf.j2` (`pgbackrest_s3_enabled` → `pgbackrest_s3_enabled | bool`)
- Modify: `roles/pgbackrest/tasks/_config.yml` (`no_log: "{{ pgbackrest_s3_enabled | bool }}"`)
- Modify: `bin/_response_schema.py` (`_validate_backup`)
- Modify: `playbooks/site.yml`
- Test: `tests/configure/test_pgbackrest_role_static.py` (new), `tests/configure/test_schema.py`

**Interfaces:**
- Consumes (from `bin/_generate_response_vars.py`, unchanged): `backup_enabled`, `backup_retention_full`, `backup_schedule_full_oncalendar`, `backup_schedule_differential_oncalendar`, `backup_secondary_store` (dict: `enabled`, `type`, `bucket`, `endpoint`, optional `region`, `path`).
- Produces: `pgbackrest_retention_full`, `pgbackrest_schedule_full`, `pgbackrest_schedule_diff`, `pgbackrest_s3_enabled`, `pgbackrest_s3_bucket`, `pgbackrest_s3_endpoint`, `pgbackrest_s3_region`, `pgbackrest_s3_path`, `pgbackrest_s3_key`, `pgbackrest_s3_key_secret`.

- [ ] **Step 1: Write failing tests**

`tests/configure/test_pgbackrest_role_static.py`:

```python
"""Static checks for the pgbackrest role and its playbook wiring."""

from __future__ import annotations

from pathlib import Path

import yaml
from jinja2 import Environment

ROOT = Path(__file__).resolve().parents[2]


def _defaults() -> dict:
    return yaml.safe_load((ROOT / "roles/pgbackrest/defaults/main.yml").read_text())


def _eval(expr: str, **ctx: object) -> str:
    return Environment().from_string(expr).render(**ctx)


def test_retention_and_schedule_follow_response_vars():
    d = _defaults()
    assert _eval(d["pgbackrest_retention_full"], backup_retention_full=7) == "7"
    assert _eval(d["pgbackrest_retention_full"]) == "4"
    assert (
        _eval(d["pgbackrest_schedule_full"], backup_schedule_full_oncalendar="Sat *-*-* 02:00:00")
        == "Sat *-*-* 02:00:00"
    )
    assert _eval(d["pgbackrest_schedule_full"]) == "Sun *-*-* 01:00:00"
    assert (
        _eval(
            d["pgbackrest_schedule_diff"],
            backup_schedule_differential_oncalendar="Mon *-*-* 03:00:00",
        )
        == "Mon *-*-* 03:00:00"
    )
    assert _eval(d["pgbackrest_schedule_diff"]) == "Mon..Sat *-*-* 01:00:00"


def test_s3_follows_secondary_store():
    d = _defaults()
    store = {
        "enabled": True,
        "type": "s3",
        "bucket": "b",
        "endpoint": "s3.example.com",
        "region": "eu-west-1",
        "path": "/aio",
    }
    assert _eval(d["pgbackrest_s3_enabled"], backup_secondary_store=store) == "True"
    assert _eval(d["pgbackrest_s3_enabled"]) == "False"
    assert _eval(d["pgbackrest_s3_bucket"], backup_secondary_store=store) == "b"
    assert _eval(d["pgbackrest_s3_endpoint"], backup_secondary_store=store) == "s3.example.com"
    assert _eval(d["pgbackrest_s3_region"], backup_secondary_store=store) == "eu-west-1"
    assert _eval(d["pgbackrest_s3_region"]) == "us-east-1"
    assert _eval(d["pgbackrest_s3_path"], backup_secondary_store=store) == "/aio"
    assert _eval(d["pgbackrest_s3_path"]) == "/pgbackrest"
    assert "vault_pgbackrest_s3_key" in d["pgbackrest_s3_key"]
    assert "vault_pgbackrest_s3_key_secret" in d["pgbackrest_s3_key_secret"]


def test_site_gates_pgbackrest_on_backup_enabled():
    site = yaml.safe_load((ROOT / "playbooks/site.yml").read_text())
    entry = next(e for e in site if e["import_playbook"] == "_pgbackrest.yml")
    assert entry["when"] == "backup_enabled | default(true) | bool"
```

Append to `tests/configure/test_schema.py`:

```python
def test_backup_secondary_store_rejects_non_s3_type():
    data = _minimal_spof_response()
    data["backup"] = {
        "enabled": True,
        "secondary_store": {
            "enabled": True,
            "type": "gcs",
            "bucket": "b",
            "endpoint": "e.example.com",
        },
    }
    with pytest.raises(SchemaError, match=r"backup\.secondary_store\.type"):
        validate(data)
```

- [ ] **Step 2: Run, expect FAIL** — `.venv/bin/pytest tests/configure/test_pgbackrest_role_static.py tests/configure/test_schema.py -q`

- [ ] **Step 3: Implement**

`roles/pgbackrest/defaults/main.yml` — replace the retention/schedule lines and the S3 block with:

```yaml
# Operator-facing values come from the response file's `backup:` block
# (flattened to backup_* by ./configure). The default() fallbacks keep the
# role usable standalone (molecule).
pgbackrest_retention_full: "{{ backup_retention_full | default(4) }}"
pgbackrest_schedule_full: "{{ backup_schedule_full_oncalendar | default('Sun *-*-* 01:00:00') }}"
pgbackrest_schedule_diff: "{{ backup_schedule_differential_oncalendar | default('Mon..Sat *-*-* 01:00:00') }}"
```

```yaml
# S3 secondary repo (optional, repo2) — from backup.secondary_store.
# Key and secret are operator-supplied vault entries; they are only
# dereferenced when the S3 block renders.
pgbackrest_s3_enabled: "{{ (backup_secondary_store | default({})).get('enabled', false) }}"
pgbackrest_s3_bucket: "{{ (backup_secondary_store | default({})).get('bucket', '') }}"
pgbackrest_s3_endpoint: "{{ (backup_secondary_store | default({})).get('endpoint', '') }}"
pgbackrest_s3_region: "{{ (backup_secondary_store | default({})).get('region', 'us-east-1') }}"
pgbackrest_s3_path: "{{ (backup_secondary_store | default({})).get('path', '/pgbackrest') }}"
pgbackrest_s3_key: "{{ vault_pgbackrest_s3_key }}"
pgbackrest_s3_key_secret: "{{ vault_pgbackrest_s3_key_secret }}"
pgbackrest_s3_retention_full: "{{ pgbackrest_retention_full }}"
```

Template: `{% if pgbackrest_s3_enabled %}` → `{% if pgbackrest_s3_enabled | bool %}`. `_config.yml`: `no_log: "{{ pgbackrest_s3_enabled | bool }}"`.

`bin/_response_schema.py` `_validate_backup`, inside `if enabled_secondary:` after the required-field loop:

```python
            if secondary_store["type"] != "s3":
                raise SchemaError(
                    "backup.secondary_store.type: only 's3' is supported; "
                    f"got '{secondary_store['type']}'"
                )
            for field in ("region", "path"):
                if field in secondary_store and not isinstance(secondary_store[field], str):
                    raise SchemaError(f"backup.secondary_store.{field}: must be a string")
```

`playbooks/site.yml` pgbackrest entry:

```yaml
- name: Import P4 pgbackrest playbook
  import_playbook: _pgbackrest.yml
  tags: [backup]
  when: backup_enabled | default(true) | bool
```

- [ ] **Step 4: Run, expect PASS** (same command, then full `.venv/bin/pytest tests/configure -q`).
- [ ] **Step 5: Commit** — `fix(pgbackrest): drive retention, schedule and S3 from the response file`.

### Task 2: pgBackRest `local` mode

**Files:**
- Modify: `roles/pgbackrest/templates/pgbackrest.conf.j2`
- Modify: `roles/pgbackrest/tasks/main.yml`, `roles/pgbackrest/tasks/_install.yml`, `roles/pgbackrest/handlers/main.yml`
- Modify: `playbooks/_pgbackrest.yml`
- Test: `tests/configure/test_pgbackrest_template.py` (new), `tests/configure/test_pgbackrest_role_static.py`

**Interfaces:** `pgbackrest_mode` ∈ `{server, client, local}`.

- [ ] **Step 1: Write failing tests**

`tests/configure/test_pgbackrest_template.py`:

```python
"""Render tests for roles/pgbackrest/templates/pgbackrest.conf.j2."""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment

ROOT = Path(__file__).resolve().parents[2]


def _render(mode: str, **overrides: object) -> str:
    template = Environment().from_string(
        (ROOT / "roles/pgbackrest/templates/pgbackrest.conf.j2").read_text()
    )
    ctx = {
        "ansible_managed": "test",
        "pgbackrest_mode": mode,
        "pgbackrest_log_path": "/var/log/pgbackrest",
        "pgbackrest_repo_path": "/var/lib/pgbackrest",
        "pgbackrest_retention_full": 4,
        "pgbackrest_s3_enabled": False,
        "pgbackrest_server_host": "pgmon01",
        "pgbackrest_tls_port": 8432,
        "pgbackrest_stanza": "pg-aio",
        "pgbackrest_pg_path": "/var/lib/pgsql/18/data",
        "pgbackrest_pg_port": 5432,
        "pigsty_pki_dir": "/etc/pki/pigsty",
        "inventory_hostname": "pgaio01",
        "groups": {"postgres": ["pgaio01"]},
        "hostvars": {"pgaio01": {}},
    }
    ctx.update(overrides)
    return template.render(**ctx)


def test_local_mode_has_repo_and_pg_path_without_tls():
    out = _render("local")
    assert "repo1-path=/var/lib/pgbackrest" in out
    assert "repo1-retention-full=4" in out
    assert "pg1-path=/var/lib/pgsql/18/data" in out
    assert "archive-async=y" in out
    assert "repo1-host" not in out
    assert "tls-server" not in out
    assert "pg1-host" not in out


def test_local_mode_s3_block_only_when_enabled():
    assert "repo2-type" not in _render("local")
    assert "repo2-type" not in _render("local", pgbackrest_s3_enabled="False")
    out = _render(
        "local",
        pgbackrest_s3_enabled=True,
        pgbackrest_s3_bucket="b",
        pgbackrest_s3_endpoint="s3.example.com",
        pgbackrest_s3_region="us-east-1",
        pgbackrest_s3_path="/pgbackrest",
        pgbackrest_s3_key="k",
        pgbackrest_s3_key_secret="s",
        pgbackrest_s3_retention_full=4,
    )
    assert "repo2-type=s3" in out
    assert "repo2-s3-bucket=b" in out


def test_client_and_server_modes_unchanged():
    client = _render("client")
    assert "repo1-host=pgmon01" in client
    assert "tls-server-auth=pgmon01=pg-aio" in client
    server = _render("server", inventory_hostname="pgmon01")
    assert "pg1-host=pgaio01" in server
    assert "tls-server-auth=pgaio01=pg-aio" in server
```

Note: plain Jinja has no `bool` filter; the test env must register one. Add to `_render` before `from_string`:
`env = Environment(); env.filters["bool"] = lambda v: str(v).strip().lower() in {"1", "true", "yes", "on", "y"}` and use `env.from_string(...)`.

Append to `tests/configure/test_pgbackrest_role_static.py`:

```python
def _tasks(path: str) -> list:
    return yaml.safe_load((ROOT / path).read_text())


def test_main_accepts_local_mode_and_skips_tls_pieces():
    tasks = {t["name"]: t for t in _tasks("roles/pgbackrest/tasks/main.yml")}
    assert "'local'" in tasks["Assert pgbackrest_mode is set to a valid value"][
        "ansible.builtin.assert"
    ]["that"][1]
    assert tasks["Deploy and start pgBackRest TLS server daemon"]["when"] == (
        "pgbackrest_mode in ['server', 'client']"
    )
    assert tasks["Open firewalld for pgBackRest TLS port"]["when"] == (
        "pgbackrest_mode in ['server', 'client']"
    )
    assert tasks["Create pgBackRest stanza"]["when"] == "pgbackrest_mode in ['server', 'local']"
    assert tasks["Install backup timers"]["when"] == "pgbackrest_mode in ['server', 'local']"
    local_assert = tasks["Assert local mode runs on a single-node cluster"]
    assert "groups['postgres'] | length == 1" in local_assert["ansible.builtin.assert"]["that"]


def test_restart_handler_skips_local_mode():
    handlers = {h["name"]: h for h in _tasks("roles/pgbackrest/handlers/main.yml")}
    assert handlers["Restart pgbackrest"]["when"] == "pgbackrest_mode != 'local'"


def test_playbook_picks_local_mode_for_colocated_backup_server():
    plays = _tasks("playbooks/_pgbackrest.yml")
    assert plays[0]["hosts"] == "postgres"
    assert "local" in plays[0]["vars"]["pgbackrest_mode"]
    assert "groups['backup_server']" in plays[0]["vars"]["pgbackrest_mode"]
    assert plays[1]["hosts"] == "backup_server:!postgres"
    assert plays[1]["vars"]["pgbackrest_mode"] == "server"
```

- [ ] **Step 2: Run, expect FAIL.**

- [ ] **Step 3: Implement**

Template body (replace from `{% if pgbackrest_mode == 'server' %}` after `start-fast=y` to end of file):

```jinja
{% if pgbackrest_mode in ['server', 'local'] %}
repo1-path={{ pgbackrest_repo_path }}
repo1-retention-full={{ pgbackrest_retention_full }}
{% if pgbackrest_s3_enabled | bool %}
repo2-type=s3
repo2-s3-bucket={{ pgbackrest_s3_bucket }}
repo2-s3-endpoint={{ pgbackrest_s3_endpoint }}
repo2-s3-region={{ pgbackrest_s3_region }}
repo2-path={{ pgbackrest_s3_path }}
repo2-s3-key={{ pgbackrest_s3_key }}
repo2-s3-key-secret={{ pgbackrest_s3_key_secret }}
repo2-retention-full={{ pgbackrest_s3_retention_full }}
{% endif %}
{% endif %}
{% if pgbackrest_mode == 'client' %}
repo1-host={{ pgbackrest_server_host }}
repo1-host-type=tls
repo1-host-ca-file={{ pigsty_pki_dir }}/ca.crt
repo1-host-cert-file={{ pigsty_pki_dir }}/{{ inventory_hostname }}.crt
repo1-host-key-file={{ pigsty_pki_dir }}/{{ inventory_hostname }}.key
repo1-host-port={{ pgbackrest_tls_port }}
{% endif %}
{% if pgbackrest_mode in ['client', 'local'] %}
archive-async=y
spool-path=/var/spool/pgbackrest
{% endif %}
{% if pgbackrest_mode != 'local' %}
tls-server-address=*
tls-server-port={{ pgbackrest_tls_port }}
tls-server-ca-file={{ pigsty_pki_dir }}/ca.crt
tls-server-cert-file={{ pigsty_pki_dir }}/{{ inventory_hostname }}.crt
tls-server-key-file={{ pigsty_pki_dir }}/{{ inventory_hostname }}.key
{% endif %}
{% if pgbackrest_mode == 'server' %}
{% for host in groups['postgres'] %}
tls-server-auth={{ host }}={{ pgbackrest_stanza }}
{% endfor %}
{% endif %}
{% if pgbackrest_mode == 'client' %}
tls-server-auth={{ pgbackrest_server_host }}={{ pgbackrest_stanza }}
{% endif %}

[{{ pgbackrest_stanza }}]
{% if pgbackrest_mode == 'server' %}
... (existing server loop, unchanged) ...
{% endif %}
{% if pgbackrest_mode in ['client', 'local'] %}
pg1-path={{ pgbackrest_pg_path }}
pg1-port={{ pgbackrest_pg_port }}
{% endif %}
```

`tasks/main.yml`:
- assert `that` second item → `pgbackrest_mode in ['server', 'client', 'local']`; fail_msg mentions `local`.
- After it, new task:

```yaml
- name: Assert local mode runs on a single-node cluster
  ansible.builtin.assert:
    that:
      - groups['postgres'] | length == 1
    fail_msg: >-
      pgBackRest local mode (repo on the PostgreSQL host) only supports a
      single-node cluster, but the postgres group has
      {{ groups['postgres'] | length }} hosts. Declare a backup_store or
      monitor node so the repository lives on its own host.
  when: pgbackrest_mode == 'local'
  tags: [pgbackrest, always]
```

- firewall import gets `when: pgbackrest_mode in ['server', 'client']`; stanza and timers `when: pgbackrest_mode in ['server', 'local']`. Update the trailing comments to say "server/local".

`_install.yml`: the three repo-dir tasks' `pgbackrest_mode == 'server'` → `pgbackrest_mode in ['server', 'local']`.

`handlers/main.yml` `Restart pgbackrest` gets `when: pgbackrest_mode != 'local'`.

`playbooks/_pgbackrest.yml`: header comment explains local mode; play 1:

```yaml
- name: PgBackRest — client (or local repo) install (postgres group)
  hosts: postgres
  become: true
  vars:
    # A postgres host that is also the backup_server (aio, or a cluster
    # with no monitor/backup_store) keeps the repo locally: one config with
    # both repo1-path and pg1-path, no TLS hop to itself.
    pgbackrest_mode: "{{ 'local' if inventory_hostname in groups['backup_server'] else 'client' }}"
```

Play 2: `hosts: backup_server:!postgres`.

- [ ] **Step 4: Run, expect PASS** (full `tests/configure`).
- [ ] **Step 5: Commit** — `feat(pgbackrest): add local mode for a repo on the PostgreSQL host`.

### Task 3: `aio` in schema and inventory

**Files:**
- Modify: `bin/_response_schema.py` (`ALLOWED_PROFILES`, `_validate_nodes`, `validate`)
- Modify: `bin/_generate_inventory.py` (`generate`)
- Create: `tests/configure/fixtures/aio.rsp.yml`
- Test: `tests/configure/test_schema.py`, `tests/configure/test_generate_inventory.py`

- [ ] **Step 1: Fixture + failing tests**

`tests/configure/fixtures/aio.rsp.yml` = `spof.rsp.yml` with `profile: aio`, `nodes:` only `pgaio01: {ip: 10.20.30.10, role: pg_primary}`, `backup: {enabled: true}`.

Schema tests (append):

```python
def test_aio_profile_fixture_validates():
    validate(_load("aio.rsp.yml"))


@pytest.mark.parametrize(
    "extra_role", ["monitor", "backup_store", "pg_replica", "pg_primary"]
)
def test_aio_rejects_a_second_node(extra_role):
    data = deepcopy(_load("aio.rsp.yml"))
    data["nodes"]["other"] = {"ip": "10.20.30.11", "role": extra_role}
    with pytest.raises(SchemaError, match=r"profile 'aio' requires exactly one node"):
        validate(data)


def test_aio_rejects_non_primary_single_node():
    data = deepcopy(_load("aio.rsp.yml"))
    data["nodes"] = {"pgaio01": {"ip": "10.20.30.10", "role": "monitor"}}
    with pytest.raises(SchemaError, match=r"profile 'aio' requires exactly one node"):
        validate(data)


def test_aio_rejects_vip():
    data = deepcopy(_load("aio.rsp.yml"))
    data["db_routing"] = {
        "vip_manager": {"enabled": True, "vip_cidr": "10.20.30.20/24", "interface": "eth0"}
    }
    with pytest.raises(SchemaError, match=r"vip_manager\.enabled.*aio"):
        validate(data)


def test_aio_allows_external_monitoring():
    data = deepcopy(_load("aio.rsp.yml"))
    data["monitoring"] = {
        "mode": "external_push",
        "external_push": {
            "metrics_url": "https://vm.example/api/v1/write",
            "logs_url": "https://vl.example/insert/jsonline",
        },
    }
    validate(data)
```

Inventory tests (append):

```python
def test_aio_self_hosted_puts_single_host_in_every_group():
    out = yaml.safe_load(generate(_load("aio.rsp.yml")))
    children = out["all"]["children"]
    for group in ("monitor", "backup_server", "etcd", "postgres"):
        assert set(children[group]["hosts"]) == {"pgaio01"}, group
    assert children["postgres"]["hosts"]["pgaio01"]["postgres_role"] == "primary"


def test_aio_external_monitoring_leaves_monitor_empty():
    data = _load("aio.rsp.yml")
    data["monitoring"] = {"mode": "external_push", "external_push": {
        "metrics_url": "https://vm.example/api/v1/write",
        "logs_url": "https://vl.example/insert/jsonline",
    }}
    children = yaml.safe_load(generate(data))["all"]["children"]
    assert children["monitor"]["hosts"] == {}
    assert set(children["backup_server"]["hosts"]) == {"pgaio01"}
```

- [ ] **Step 2: Run, expect FAIL.**

- [ ] **Step 3: Implement**

`ALLOWED_PROFILES = {"spof", "ha", "aio"}`.

`_validate_nodes`, right after the per-node loop:

```python
    if profile == "aio":
        if roles != ["pg_primary"]:
            raise SchemaError(
                "nodes: profile 'aio' requires exactly one node with role 'pg_primary'; "
                f"got roles {sorted(roles)} (use profile 'spof' or 'ha' for separate "
                "monitor, backup_store or replica nodes)"
            )
        return
```

`validate`, after `_validate_db_routing(...)`:

```python
    if profile == "aio":
        vip = (data.get("db_routing") or {}).get("vip_manager") or {}
        if vip.get("enabled"):
            raise SchemaError(
                "db_routing.vip_manager.enabled: profile 'aio' has one node, "
                "so a VIP has nowhere to move; set it to false"
            )
```

`_generate_inventory.generate`, after `monitor_hosts = ...`:

```python
    # aio: the single pg_primary is also the monitor when monitoring is
    # self-hosted. backup_server then follows monitor (or falls back to the
    # primary under external monitoring) via the logic below.
    monitoring_mode = response.get("monitoring", {}).get("mode", "self_hosted")
    if response.get("profile") == "aio" and monitoring_mode == "self_hosted":
        monitor_hosts = [
            (name, {"ansible_host": node["ip"]}) for name, node in by_role["pg_primary"]
        ]
```

- [ ] **Step 4: PASS.** - [ ] **Step 5: Commit** — `feat(configure): add the aio single-host profile`.

### Task 4: configure CLI, example response file, playbook gating

**Files:**
- Modify: `configure` (docstring line 6, wizard prompt, `-c` choices, routing prompts skipped for aio)
- Create: `responses/aio.rsp.yml.example`
- Modify: `playbooks/site.yml` (HAProxy gate), `roles/monitoring_agents/templates/vmagent-scrape.yml.j2` (HAProxy scrape gate + comment)
- Test: `tests/configure/test_configure_cli.py`, `tests/configure/test_pgbackrest_role_static.py` (site gate), example validates

- [ ] **Step 1: Failing tests**

`test_configure_cli.py`:

```python
def test_interactive_aio_skips_routing_prompts(monkeypatch, tmp_path):
    module = _load_configure_module()
    (tmp_path / "responses").mkdir()
    (tmp_path / "responses" / "aio.rsp.yml.example").write_text(
        (ROOT / "responses" / "aio.rsp.yml.example").read_text()
    )
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "RESPONSE_FILE_PATH", tmp_path / "responses" / "site.rsp.yml")
    monkeypatch.setattr(module, "INVENTORY_PATH", tmp_path / "inventory" / "site.yml")
    monkeypatch.setattr(
        module,
        "RESPONSE_VARS_PATH",
        tmp_path / "inventory" / "group_vars" / "all" / "response.yml",
    )
    monkeypatch.setattr(sys, "stdin", _TtyStdin())
    # cluster name, domain, remote user, monitoring mode -- no routing prompts
    answers = iter(["pg-aio", "example.internal", "dba", ""])
    monkeypatch.setattr(builtins, "input", lambda _prompt: next(answers))

    rc = module.cmd_interactive(argparse.Namespace(profile="aio", no_vault=True))

    assert rc == 0
    data = yaml.safe_load((tmp_path / "responses" / "site.rsp.yml").read_text())
    assert data["profile"] == "aio"
    assert data["db_routing"]["vip_manager"] == {"enabled": False}


def test_profile_flag_accepts_aio():
    module = _load_configure_module()
    src = (ROOT / "configure").read_text()
    assert 'choices=("spof", "ha", "aio")' in src
    assert module is not None
```

`test_schema.py`:

```python
def test_aio_example_validates():
    with (Path(__file__).resolve().parents[2] / "responses/aio.rsp.yml.example").open() as fh:
        validate(yaml.safe_load(fh))
```

`test_pgbackrest_role_static.py`:

```python
def test_haproxy_skipped_for_single_host_profiles():
    site = yaml.safe_load((ROOT / "playbooks/site.yml").read_text())
    entry = next(e for e in site if e["import_playbook"] == "_haproxy.yml")
    assert entry["when"] == "cluster_profile | default('ha') not in ['spof', 'aio']"
    scrape = (ROOT / "roles/monitoring_agents/templates/vmagent-scrape.yml.j2").read_text()
    assert "not in ['spof', 'aio']" in scrape
```

- [ ] **Step 2: FAIL.**

- [ ] **Step 3: Implement**

`configure`: docstring `./configure -c {spof,ha,aio}`; prompt `"Profile (spof|ha|aio) [ha]: "`; argparse `choices=("spof", "ha", "aio")`. In `cmd_interactive` wrap the routing prompts:

```python
    if profile == "aio":
        # One host: HAProxy is not deployed and a VIP has nowhere to move.
        rto_profile, backend_target, vip_enabled = "norm", "pgbouncer", False
        vip_cidr = vip_interface = ""
    else:
        print()
        print("Database routing (HAProxy + vip-manager):")
        ... existing prompts unchanged ...
```

`responses/aio.rsp.yml.example`: copy `spof.rsp.yml.example` and change: header comment (AIO profile — everything on one host, temporary/dev use, see docs/operations/firstrun.md#aio); `profile: aio`; `cluster.name: pg-aio`; `nodes:` only `pgaio01: {ip: 10.20.30.10, role: pg_primary}`; backup block:

```yaml
# AIO keeps the pgBackRest repository on this same host (/var/lib/pgbackrest),
# so a disk loss takes the backups with it. Uncomment secondary_store to also
# ship every backup and WAL segment to S3, then add the credentials with
#   ansible-vault edit inventory/group_vars/all/vault.yml
#     vault_pgbackrest_s3_key: ...
#     vault_pgbackrest_s3_key_secret: ...
backup:
  enabled: true
  tool: pgbackrest
  schedule: {full: "0 1 * * 0", differential: "0 1 * * 1-6"}
  retention: {full: 4}
  # secondary_store:
  #   enabled: true
  #   type: s3
  #   bucket: my-pg-backups
  #   endpoint: s3.us-east-1.amazonaws.com
  #   region: us-east-1
  #   path: /pg-aio
```

`db_routing` block: haproxy values with a comment that AIO does not deploy HAProxy; `vip_manager: {enabled: false}` with comment.

`site.yml`: `when: cluster_profile | default('ha') not in ['spof', 'aio']`. `vmagent-scrape.yml.j2` line 41: `{% if (cluster_profile | default('ha')) not in ['spof', 'aio'] %}`; update the comment on line 39 to "spof/aio".

- [ ] **Step 4: PASS** (full suite + ruff). - [ ] **Step 5: Commit** — `feat(aio): wire the aio profile into configure and the playbooks`.

### Task 5: Molecule `backup/local` scenario

**Files:**
- Create: `tests/molecule/backup/molecule/local/{molecule.yml,prepare.yml,converge.yml,verify.yml}`
- Modify: `tests/molecule/COVERAGE.md` (add row `backup / local`)

- [ ] **Step 1:** `molecule.yml`: copy `backup/default/molecule.yml`, keep ONE platform named `pigsty-lite-backup-local-server` (name must end with `-server`, enforced by `test_backup_server_hosts_use_server_suffix`), image `localhost/molecule-base-data:latest`, `groups: [etcd, postgres, backup_server]`, network `pigsty-lite-backup-local`; host_vars `postgres_role: primary`, `etcd_seq: 1` under the new name. Testing-goal header comment describes the aio/local topology.
- [ ] **Step 2:** `prepare.yml` and `converge.yml`: identical copies of the default scenario's.
- [ ] **Step 3:** `verify.yml`: the default scenario's plays retargeted (all plays run on the single host), plus:

```yaml
    - name: Read the rendered pgBackRest config
      ansible.builtin.slurp:
        src: /etc/pgbackrest/pgbackrest.conf
      register: pgbr_conf

    - name: Assert the config is local mode
      ansible.builtin.assert:
        that:
          - "'repo1-path=' in (pgbr_conf.content | b64decode)"
          - "'pg1-path=' in (pgbr_conf.content | b64decode)"
          - "'repo1-host' not in (pgbr_conf.content | b64decode)"
          - "'tls-server' not in (pgbr_conf.content | b64decode)"

    - name: Read pgbackrest.service state
      ansible.builtin.systemd_service:
        name: pgbackrest
      register: pgbr_svc

    - name: Assert no TLS server daemon runs in local mode
      ansible.builtin.assert:
        that:
          - pgbr_svc.status.ActiveState != 'active'
```

- [ ] **Step 4:** Run `make molecule ROLE=backup` if podman and base images are available; otherwise run `.venv/bin/pytest tests/configure -q` and `ansible-playbook --syntax-check` on the scenario files and record that molecule was not run.
- [ ] **Step 5: Commit** — `test(molecule): cover pgBackRest local mode on a single host`.

### Task 6: Docs

**Files:** `README.md`, `docs/operations/firstrun.md`, `roles/pgbackrest/README.md`, `docs/reference/ports.md`, `roles/monitoring_agents/README.md`, `playbooks/tags.md` (only if it mentions the profile gate).

- [ ] README "Two reference profiles" → "Three reference profiles", add `aio` (**AIO**, everything on one host — temporary dev/demo; not for production).
- [ ] `firstrun.md`: new `## AIO (all-in-one)` section: what runs on the host; Patroni + single-member etcd kept so moving to SPOF/HA is an inventory change; backups are local unless `backup.secondary_store` is enabled (S3 vault keys); switchover/failover/scale playbooks do not apply; converting to SPOF = set `profile: spof`, add a `monitor` node, `make deploy` (note the monitoring history and local repo stay on the old host).
- [ ] `roles/pgbackrest/README.md`: document `local` mode and how `pgbackrest_mode` is chosen; S3 settings now come from `backup.secondary_store` + vault keys.
- [ ] `ports.md`: 8432 row — "not opened in local mode (aio)".
- [ ] `monitoring_agents/README.md`: HAProxy scrape skipped for `spof` and `aio`.
- [ ] Commit — `docs: document the aio profile and pgBackRest local mode`.

---

## Amendments after Codex plan review (these override the tasks above)

**A1 (Task 3).** The `aio` early `return` in `_validate_nodes` goes immediately
after the `for name, node in nodes.items():` loop and **before** the
`primaries`/`replicas`/`monitors` counts and the self-hosted monitor check.

**A2 (Task 1/2).** The S3 `repo2-*` block renders in **every** mode
(`server`, `client`, `local`), not only server/local. `archive-push` runs on the
PostgreSQL host, so a client in SPOF/HA must know repo2 to push WAL to S3. Place
the block right after `start-fast=y`, outside any mode conditional. Template
test: `client` mode with S3 enabled contains `repo2-type=s3`.

**A3 (Task 1).** `pgbackrest-backup@.service.j2`: back up to each repo. Replace
the single `ExecStart` with:

```
ExecStart=/usr/bin/pgbackrest --stanza={{ pgbackrest_stanza }} --repo=1 --type=%i backup
{% if pgbackrest_s3_enabled | bool %}
ExecStart=/usr/bin/pgbackrest --stanza={{ pgbackrest_stanza }} --repo=2 --type=%i backup
{% endif %}
```

Static test: render with S3 off → one `ExecStart` containing `--repo=1`; with
S3 on → two, the second with `--repo=2`. The deploy-time initial backup stays
repo1-only (documented).

**A4 (Task 5).** In the `local` verify, the config/daemon assertions run in
the first play, **before** the on-demand backup.

**A5 (Task 6).** Docs state explicitly: a missing `backup:` block or
`backup.enabled: false` skips pgBackRest in `site.yml`; nothing deployed is
removed.
