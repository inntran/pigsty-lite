# Grafana Role Self-Hosted Design

**Date:** 2026-05-17
**Status:** Draft

## Goal

Replace the `grafana.grafana.grafana` upstream role currently embedded in
`roles/grafana/tasks/main.yml` with first-party install + configure tasks
inside `roles/grafana/`. Drop the `grafana.grafana` collection dependency.
Keep `community.grafana` (still used for the REST-API datasource/dashboard
calls in `_datasources.yml` and `_dashboards.yml`).

## Why

The upstream `grafana.grafana.grafana` role has two contract problems and one
maintenance problem:

1. **`grafana_version` is overloaded.** On RHEL the upstream role renders
   `grafana_package = "grafana{% if grafana_version != 'latest' %}-{{ grafana_version }}{% endif %}"`,
   so `grafana_version: 12` produces the literal package name `grafana-12`,
   which does not exist in `rpm.grafana.com`. The variable only accepts a full
   NEVR-style string (e.g. `12.2.4`) or the keyword `latest` — and `latest`
   now resolves to Grafana 13 since 2026-05, breaking installs that intended
   to track the 12.x line.
2. **No way to skip the install step.** The role's `main.yml` unconditionally
   includes `install.yml`. There is no `grafana_install_enabled` flag. To
   install Grafana ourselves and keep using the role for config rendering we
   would have to either pre-install the RPM and pass `grafana_version: latest`
   (so the role's `dnf: state=latest` becomes a no-op) or call individual task
   files via `include_role: tasks_from:`. Both are fragile.
3. **Maintenance cost vs. value.** Of the upstream role's ~600 lines of tasks,
   we use install + configure only. We already bypass datasources, dashboards,
   plugins, notifications, and API keys by passing empty lists; we replaced
   them with REST-API tasks in `_datasources.yml` and `_dashboards.yml`. The
   remaining surface area we'd inherit is small enough to own outright, and
   owning it removes the version-pin footgun.

## Scope

In scope:

- Grafana RPM repo file at `/etc/yum.repos.d/grafana.repo`.
- Package install pinned to the 12.x line via a configurable glob.
- `/etc/grafana/grafana.ini` rendering from a `grafana_ini` dict.
- `grafana-server` systemd enable + start + restart-on-config-change handler.
- Required directories for grafana provisioning (kept minimal — only dirs we
  actually use or that grafana-server expects to exist at startup).

Out of scope (already handled elsewhere or unused):

- Datasource provisioning — stays in `_datasources.yml` via
  `community.grafana.grafana_datasource`.
- Dashboard provisioning — stays in `_dashboards.yml`.
- LDAP, socket protocol, `CAP_NET_BIND_SERVICE` for ports < 1024,
  Debian/Ubuntu/SUSE support, AmbientCapabilities systemd drop-in. We bind
  loopback:3000 behind nginx, on EL only.

## Version selection

The role exposes `grafana_version_pin` (default `"12.*"`) which is passed
directly to `dnf` as the package name (`grafana-{{ grafana_version_pin }}`).
This lets dnf select the latest matching version in the repo. Operators can
pin tighter (`grafana_version_pin: "12.2.4-1"`) if they want bit-for-bit
reproducibility.

Removing the variable `grafana_version` (current default `12`) is a breaking
change for any operator who set it. Group-vars override in
`group_vars/monitor.yml` is removed in the same change; the migration note in
the role README points operators at `grafana_version_pin`.

## Variables (`defaults/main.yml`)

New / renamed:

```yaml
# Package selection. Passed to dnf as `grafana-{{ grafana_version_pin }}`.
# Wildcards are allowed. To track 12.x: "12.*". To pin: "12.2.4-1".
# Crossing a major boundary requires also editing files/grafana.repo to
# remove or change the `exclude=` line.
grafana_version_pin: "12.*"

# Service
grafana_service_name: grafana-server
grafana_config_dir: /etc/grafana
grafana_data_dir: /var/lib/grafana
grafana_logs_dir: /var/log/grafana
```

Unchanged / kept:

- `grafana_listen_address`, `grafana_default_port`, `grafana_root_url`,
  `grafana_serve_from_sub_path`, `grafana_admin_user`,
  `grafana_admin_password`, `grafana_api_url`, datasource URLs.

Removed:

- `grafana_version` (replaced by `grafana_version_pin`).

The `grafana_ini` dict moves from a literal inside `tasks/main.yml` to
`defaults/main.yml`, where operators can override individual sections via
the standard Ansible variable precedence rules. Default contents mirror what
`tasks/main.yml` currently passes:

```yaml
grafana_ini:
  instance_name: "{{ ansible_facts['fqdn'] | default(inventory_hostname) }}"
  paths:
    logs: "{{ grafana_logs_dir }}"
    data: "{{ grafana_data_dir }}"
  server:
    http_addr: "{{ grafana_listen_address }}"
    http_port: "{{ grafana_default_port }}"
    root_url: "{{ grafana_root_url }}"
    serve_from_sub_path: "{{ grafana_serve_from_sub_path }}"
    protocol: http
  database:
    type: sqlite3
  security:
    admin_user: "{{ grafana_admin_user }}"
    admin_password: "{{ grafana_admin_password }}"
```

## File layout

```
roles/grafana/
├── defaults/main.yml              # updated
├── files/
│   └── grafana.repo               # new (static yum repo file)
├── handlers/main.yml              # unchanged ("Restart grafana")
├── meta/main.yml                  # unchanged
├── tasks/
│   ├── main.yml                   # rewritten: import _install, _configure, then datasources/dashboards
│   ├── _install.yml               # new
│   ├── _configure.yml             # new
│   ├── _datasources.yml           # unchanged
│   └── _dashboards.yml            # unchanged
├── templates/
│   ├── grafana.ini.j2             # new (copy of upstream's 28-line generic renderer)
│   └── dashboard-provider.yml.j2  # unchanged
└── README.md                      # updated
```

## Task contents

### `_install.yml`

```yaml
- name: Install Grafana yum repo file
  ansible.builtin.copy:
    src: grafana.repo
    dest: /etc/yum.repos.d/grafana.repo
    owner: root
    group: root
    mode: "0644"

- name: Install Grafana
  ansible.builtin.dnf:
    name: "grafana-{{ grafana_version_pin }}"
    state: present
```

### `_configure.yml`

```yaml
- name: Ensure Grafana config and data directories exist
  ansible.builtin.file:
    path: "{{ item.path }}"
    state: directory
    owner: "{{ item.owner | default('root') }}"
    group: grafana
    mode: "{{ item.mode | default('0755') }}"
  loop:
    - { path: "{{ grafana_config_dir }}" }
    - { path: "{{ grafana_config_dir }}/provisioning" }
    - { path: "{{ grafana_config_dir }}/provisioning/datasources" }
    - { path: "{{ grafana_config_dir }}/provisioning/dashboards" }
    - { path: "{{ grafana_logs_dir }}", owner: grafana }
    - { path: "{{ grafana_data_dir }}", owner: grafana }
    - { path: "{{ grafana_data_dir }}/dashboards", owner: grafana }
    - { path: "{{ grafana_data_dir }}/plugins", owner: grafana }

- name: Render /etc/grafana/grafana.ini
  ansible.builtin.template:
    src: grafana.ini.j2
    dest: "{{ grafana_config_dir }}/grafana.ini"
    owner: root
    group: grafana
    mode: "0640"
  no_log: "{{ 'false' if lookup('env', 'CI') else 'true' }}"
  notify: Restart grafana

- name: Enable and start grafana-server
  ansible.builtin.systemd:
    name: "{{ grafana_service_name }}"
    enabled: true
    state: started
    daemon_reload: true
```

### `files/grafana.repo`

Pure static yum repo definition — no per-host templating. The `exclude=`
line is part of the file rather than a variable; crossing major versions
is an explicit operator action that edits this file (or replaces it).

```ini
# MANAGED BY pigsty-lite grafana role
[grafana]
name = Grafana OSS
baseurl = https://rpm.grafana.com/oss/rpm
repo_gpgcheck = 1
enabled = 1
gpgcheck = 1
gpgkey = https://rpm.grafana.com/gpg.key
sslverify = 1
sslcacert = /etc/pki/tls/certs/ca-bundle.crt
```

### `grafana.ini.j2`

Copy of the upstream 28-line generic dict-to-INI renderer (no logic changes;
it walks `grafana_ini` rendering top-level scalars, then `[section]` blocks,
then nested `[section.subsection]` blocks).

### `main.yml`

```yaml
- name: Validate preconditions
  ansible.builtin.import_tasks: _assert.yml
  tags: [monitoring, assert]

- name: Install Grafana
  ansible.builtin.import_tasks: _install.yml
  tags: [monitoring, install]

- name: Configure Grafana
  ansible.builtin.import_tasks: _configure.yml
  tags: [monitoring, config]

- name: Flush handlers before talking to the API
  ansible.builtin.meta: flush_handlers
  tags: [monitoring]

- name: Wait for Grafana HTTP API
  ansible.builtin.wait_for:
    host: "{{ grafana_listen_address }}"
    port: "{{ grafana_default_port }}"
    timeout: 60
  tags: [monitoring]

- name: Configure datasources
  ansible.builtin.import_tasks: _datasources.yml
  tags: [monitoring, config]

- name: Provision dashboards
  ansible.builtin.import_tasks: _dashboards.yml
  tags: [monitoring, config]
```

The pre-existing monitor-group assertions in `tasks/main.yml` move into a new
`_assert.yml` for parity with how other roles (`etcd`, `pgbackrest`) lay out
preflight checks.

## What we are not bringing across

- **LDAP config rendering** — we don't use it.
- **Socket protocol + tmpfiles.d** — we always serve HTTP behind nginx.
- **`CAP_NET_BIND_SERVICE` + AmbientCapabilities drop-in** — we bind on 3000.
- **Recursive ownership pass over `dashboards/` after every dashboard JSON
  change** — `community.grafana.grafana_dashboard` writes via the API, not
  the filesystem, so ownership of the on-disk provisioning tree does not
  rotate per dashboard.
- **Plugin install via `grafana-cli`** — we don't install plugins. If we do
  in the future, a small `_plugins.yml` is easier to add than to extract from
  the upstream role.
- **The big upstream `grafana.ini.j2` template** — already small (28 lines)
  and generic; we copy it verbatim. No license issue (upstream is Apache-2.0,
  same as pigsty-lite).

## Migration

1. Remove `grafana.grafana` from `requirements.yml`.
2. Delete the old `grafana.grafana` collection from `collections/` (or let
   `make init` skip it on the next sync).
3. Drop `grafana_version: 12` from `group_vars/monitor.yml`. If an operator
   was overriding `grafana_version`, the README's "migration" section maps it
   to `grafana_version_pin`.

## Test plan

- `make test-role ROLE=grafana` — converges the new role end-to-end on the
  standalone scenario. Verifies `grafana-server` is active, `/etc/grafana/
  grafana.ini` is rendered, and the API responds on `127.0.0.1:3000`.
- `make test-role ROLE=nginx_proxy` — the nginx_proxy prepare runs
  `monitoring_server` + `grafana`; this is the CI-relevant smoke.
- `make test-role ROLE=monitoring_agents` — full-stack prepare also runs
  grafana, catching regressions in the full pipeline.

## Open questions

- Should we install `grafana` (OSS) or `grafana-enterprise`? Spec assumes
  OSS — repo path `/oss/rpm`. Enterprise lives at `/enterprise/rpm`. If we
  need both, expose `grafana_edition: oss | enterprise` and derive the repo
  URL. Default OSS.
- Should we keep the upstream `no_log` behavior on the `grafana.ini` template
  task? Current draft preserves it (admin password lives in `grafana_ini`).
