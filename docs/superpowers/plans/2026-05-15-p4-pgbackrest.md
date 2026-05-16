# pgBackRest Role Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create `roles/pgbackrest` — a single Ansible role replacing `roles/bad_backup_client` and `roles/bad_backup_store`, supporting two modes: `server` (the `backup_server` host) and `client` (postgres nodes).

**Architecture:** One role, `pgbackrest_mode` variable selects which task files run. Both modes install the package, render config, and run the `pgbackrest server` TLS daemon. `server` mode additionally creates the stanza, sets `archive_command` on each postgres node (delegated), and installs backup timers. Certs are referenced directly from `pki_dir` (`/etc/pki/pigsty`) — no copy or symlink.

**No single-host mode.** Per the pigsty-lite design principles (§1.1 in `docs/superpowers/specs/2026-05-12-pigsty-lite-design.md`), the backup repo is never colocated with PostgreSQL — a backup on the same disk as the data it backs up is not a backup. Even the `single` profile uses a dedicated monitor/infra host that doubles as `backup_server`. Operators who want off-host durability on `single` enable the optional S3 secondary repo on `backup_server`.

**Why TLS, not SSH:** pgBackRest 2.55+ ships a native TLS server that eliminates the SSH keypair exchange that older pgBackRest deployments require. No SSH keypairs need to be generated, exchanged, distributed via Ansible facts, or rotated. Both the store and each client run `pgbackrest server` as a long-lived daemon; mutual TLS authentication uses the cluster PKI already deployed by `roles/certs`. The `tls-server-auth=<CN>=<stanza>` option maps the client cert's CN to the stanza it may access, giving per-host authorization without a second credential system. Net effect: one less secret type to manage, one less role boundary to cross (`certs` already owns PKI distribution), and a configuration surface that's purely declarative in `pgbackrest.conf`.

**etcd backup: intentionally not implemented.** etcd here only stores Patroni DCS state (leader lease, member list, dynamic config, failover history) — all of it ephemeral or reconstructible from `pg_controldata` plus the static `patroni.yml`. Recovery from total etcd loss is "reinstall etcd, restart Patroni, leadership re-elects in seconds." Adding an etcd snapshot timer would add a systemd unit, store path, rotation, and restore runbook for a recovery path slower than the rebuild it would replace. See `roles/etcd/README.md` for the recovery procedure.

**Tech Stack:** Ansible, pgBackRest 2.55+, systemd, firewalld, SELinux (RHEL/EL10), Patroni (for `postgres_extra_parameters` injection).

---

## File Map

| Path | Purpose |
|------|---------|
| `roles/pgbackrest/defaults/main.yml` | All role variables with defaults |
| `roles/pgbackrest/meta/main.yml` | Galaxy metadata |
| `roles/pgbackrest/handlers/main.yml` | `reload systemd`, `restart pgbackrest` |
| `roles/pgbackrest/tasks/main.yml` | Entry point — imports tasks based on mode |
| `roles/pgbackrest/tasks/_install.yml` | Install package, create dirs, SELinux labels |
| `roles/pgbackrest/tasks/_config.yml` | Render `pgbackrest.conf` |
| `roles/pgbackrest/tasks/_service.yml` | Deploy + start `pgbackrest.service` (server + client) |
| `roles/pgbackrest/tasks/_firewall.yml` | Open TLS port from postgres nodes (server only) |
| `roles/pgbackrest/tasks/_stanza.yml` | `stanza-create` + `check` (server only) |
| `roles/pgbackrest/tasks/_archive.yml` | Set `archive_mode`/`archive_command` via `postgres_extra_parameters` on each postgres node, reload Patroni (server only, delegated) |
| `roles/pgbackrest/tasks/_timers.yml` | Deploy full/diff systemd timers (server only) |
| `roles/pgbackrest/templates/pgbackrest.conf.j2` | Config template — mode-conditional |
| `roles/pgbackrest/templates/pgbackrest.service.j2` | TLS server daemon unit |
| `roles/pgbackrest/templates/pgbackrest-backup@.service.j2` | Oneshot backup service (instantiated with `full`/`diff`) |
| `roles/pgbackrest/templates/pgbackrest-backup@.timer.j2` | Timer for backup service |
| `roles/pgbackrest/README.md` | Usage docs |

---

### Task 1: Scaffold role skeleton

**Files:**

- Create: `roles/pgbackrest/defaults/main.yml`
- Create: `roles/pgbackrest/meta/main.yml`
- Create: `roles/pgbackrest/handlers/main.yml`
- Create: `roles/pgbackrest/tasks/main.yml`
- Create: `roles/pgbackrest/README.md`

- [ ] **Step 1: Create `defaults/main.yml`**

```yaml
---
# roles/pgbackrest/defaults/main.yml

pgbackrest_mode: ~                   # required: server | client (set per group_vars)

pgbackrest_package: pgbackrest
pgbackrest_support_packages:
  - python3-libselinux
  - policycoreutils-python-utils

pgbackrest_stanza: pigsty
pgbackrest_repo_path: /var/lib/pgbackrest
pgbackrest_log_path: /var/log/pgbackrest
pgbackrest_config_file: /etc/pgbackrest/pgbackrest.conf
pgbackrest_config_dir: /etc/pgbackrest
pgbackrest_tls_port: 8432
pgbackrest_retention_full: 4
pgbackrest_schedule_full: "Sun *-*-* 01:00:00"
pgbackrest_schedule_diff: "Mon..Sat *-*-* 01:00:00"

# PKI — certs role deploys <hostname>.crt, <hostname>.key, ca.crt here
pgbackrest_pki_dir: "{{ pki_dir | default('/etc/pki/pigsty') }}"

# OS user that owns the repo and runs the daemon (always postgres)
pgbackrest_user: "{{ postgres_user | default('postgres') }}"
pgbackrest_group: "{{ postgres_group | default('postgres') }}"

# PostgreSQL paths — used by server mode to render per-node pg<N>-path/port
pgbackrest_pg_path: "{{ postgres_data_dir | default('/var/lib/pgsql/' ~ (postgres_version | default(18)) ~ '/data') }}"
pgbackrest_pg_port: "{{ postgres_port | default(5432) }}"

# Server host — used by client mode to locate repo1-host
pgbackrest_server_host: "{{ groups['backup_server'][0] }}"

# Firewalld
pgbackrest_firewalld_zone: "{{ firewalld_default_zone | default('public') }}"

# S3 secondary repo (optional, repo2)
pgbackrest_s3_enabled: false
pgbackrest_s3_bucket: ~
pgbackrest_s3_endpoint: ~
pgbackrest_s3_region: us-east-1
pgbackrest_s3_path: /pgbackrest
pgbackrest_s3_key: ~            # from Ansible Vault
pgbackrest_s3_key_secret: ~     # from Ansible Vault
pgbackrest_s3_retention_full: "{{ pgbackrest_retention_full }}"
```

- [ ] **Step 2: Create `meta/main.yml`**

```yaml
---
galaxy_info:
  role_name: pgbackrest
  author: pigsty-lite
  description: pgBackRest backup — server and client modes.
  license: Apache-2.0
  min_ansible_version: "2.16"
  platforms:
    - name: EL
      versions: ["10"]
dependencies: []
```

- [ ] **Step 3: Create `handlers/main.yml`**

```yaml
---
- name: Reload systemd
  ansible.builtin.systemd:
    daemon_reload: true

- name: Restart pgbackrest
  ansible.builtin.systemd:
    name: pgbackrest
    state: restarted
```

- [ ] **Step 4: Create `tasks/main.yml`**

```yaml
---
- name: Install pgBackRest
  ansible.builtin.import_tasks: _install.yml
  tags: [pgbackrest, install]

- name: Render pgBackRest configuration
  ansible.builtin.import_tasks: _config.yml
  tags: [pgbackrest, config]

- name: Deploy and start pgBackRest TLS server daemon
  ansible.builtin.import_tasks: _service.yml
  when: pgbackrest_mode in ['server', 'client']
  tags: [pgbackrest, service]

- name: Open firewalld for pgBackRest TLS port
  ansible.builtin.import_tasks: _firewall.yml
  when: pgbackrest_mode == 'server'
  tags: [pgbackrest, firewall]

- name: Create pgBackRest stanza
  ansible.builtin.import_tasks: _stanza.yml
  when: pgbackrest_mode == 'server'
  tags: [pgbackrest, stanza]

- name: Activate WAL archiving
  ansible.builtin.import_tasks: _archive.yml
  when: pgbackrest_mode == 'server'
  tags: [pgbackrest, archive]

- name: Install backup timers
  ansible.builtin.import_tasks: _timers.yml
  when: pgbackrest_mode == 'server'
  tags: [pgbackrest, timers]
```

- [ ] **Step 5: Create `README.md`**

```markdown
# pgbackrest

Installs and configures pgBackRest. Two modes selected via `pgbackrest_mode`:

- `server` — the `backup_server` host. Runs `pgbackrest server` daemon, owns repo, creates stanza, installs backup timers, sets `archive_command` on each postgres node, connects to postgres nodes over TLS to pull WAL and run backups.
- `client` — postgres node. Runs `pgbackrest server` daemon so the server can reach back to read PG data; points `repo1-host` at the server.

There is no single-host mode. See §1.1 of the main design doc.

## Requirements

- `roles/certs` must run first (deploys PKI certs to `pki_dir`).
- `roles/patroni` must run first on postgres nodes (provides `postgres_extra_parameters` injection point).
- Inventory group `backup_server` must exist with exactly one host.

## Key Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `pgbackrest_mode` | _(required)_ | `server` or `client` |
| `pgbackrest_stanza` | `pigsty` | Stanza name |
| `pgbackrest_repo_path` | `/var/lib/pgbackrest` | Repository path |
| `pgbackrest_retention_full` | `4` | Number of full backups to retain |
| `pgbackrest_schedule_full` | `Sun *-*-* 01:00:00` | systemd OnCalendar for full backups |
| `pgbackrest_schedule_diff` | `Mon..Sat *-*-* 01:00:00` | systemd OnCalendar for differential backups |
| `pgbackrest_pki_dir` | `/etc/pki/pigsty` | Path where certs role deployed certs |
| `pgbackrest_s3_enabled` | `false` | Enable S3 secondary repo |
| `pgbackrest_tls_port` | `8432` | pgBackRest TLS server port |

## S3 Secondary Repo

Set `pgbackrest_s3_enabled: true` and supply vault-encrypted values for:
- `pgbackrest_s3_key`
- `pgbackrest_s3_key_secret`
- `pgbackrest_s3_bucket`, `pgbackrest_s3_endpoint`, `pgbackrest_s3_region`, `pgbackrest_s3_path`
```

- [ ] **Step 6: Commit**

```bash
git add roles/pgbackrest/
git commit -m "feat(pgbackrest): scaffold role skeleton — defaults, meta, handlers, tasks entry point, README"
```

---

### Task 2: Install task

**Files:**

- Create: `roles/pgbackrest/tasks/_install.yml`

- [ ] **Step 1: Create `tasks/_install.yml`**

```yaml
---
- name: Install pgBackRest and SELinux helpers
  ansible.builtin.dnf:
    name: "{{ [pgbackrest_package] + pgbackrest_support_packages }}"
    state: present

- name: Ensure pgBackRest config directory exists
  ansible.builtin.file:
    path: "{{ pgbackrest_config_dir }}"
    state: directory
    owner: root
    group: "{{ pgbackrest_group }}"
    mode: "0750"

- name: Ensure pgBackRest log directory exists
  ansible.builtin.file:
    path: "{{ pgbackrest_log_path }}"
    state: directory
    owner: "{{ pgbackrest_user }}"
    group: "{{ pgbackrest_group }}"
    mode: "0750"

- name: Ensure pgBackRest repo directory exists
  ansible.builtin.file:
    path: "{{ pgbackrest_repo_path }}"
    state: directory
    owner: "{{ pgbackrest_user }}"
    group: "{{ pgbackrest_group }}"
    mode: "0750"
  when: pgbackrest_mode == 'server'

- name: Register SELinux fcontext for repo directory
  community.general.sefcontext:
    target: "{{ pgbackrest_repo_path }}(/.*)?"
    setype: var_t
    state: present
  register: _pgbackrest_repo_fcontext
  when:
    - pgbackrest_mode == 'server'
    - ansible_facts.selinux.status == "enabled"

- name: Relabel repo directory if fcontext changed
  ansible.builtin.command:
    cmd: "restorecon -RF {{ pgbackrest_repo_path }}"
  when:
    - pgbackrest_mode == 'server'
    - ansible_facts.selinux.status == "enabled"
    - _pgbackrest_repo_fcontext.changed
  changed_when: true

- name: Register SELinux fcontext for log directory
  community.general.sefcontext:
    target: "{{ pgbackrest_log_path }}(/.*)?"
    setype: postgresql_log_t
    state: present
  register: _pgbackrest_log_fcontext
  when: ansible_facts.selinux.status == "enabled"

- name: Relabel log directory if fcontext changed
  ansible.builtin.command:
    cmd: "restorecon -RF {{ pgbackrest_log_path }}"
  when:
    - ansible_facts.selinux.status == "enabled"
    - _pgbackrest_log_fcontext.changed
  changed_when: true
```

- [ ] **Step 2: Commit**

```bash
git add roles/pgbackrest/tasks/_install.yml
git commit -m "feat(pgbackrest): add _install task — package, dirs, SELinux labels"
```

---

### Task 3: Config template

**Files:**

- Create: `roles/pgbackrest/templates/pgbackrest.conf.j2`
- Create: `roles/pgbackrest/tasks/_config.yml`

The template uses `pgbackrest_mode` to render the correct sections. Key facts:

- Cert files are at `{{ pgbackrest_pki_dir }}/ca.crt`, `{{ pgbackrest_pki_dir }}/{{ inventory_hostname }}.crt`, `{{ pgbackrest_pki_dir }}/{{ inventory_hostname }}.key`
- `tls-server-auth` uses the client's cert CN = `inventory_hostname`
- On server mode, each postgres node gets a `pgN-host` entry in the stanza section
- On client mode, `repo1-host` points at `pgbackrest_server_host`; also runs TLS server so the server can reach back

- [ ] **Step 1: Create `templates/pgbackrest.conf.j2`**

```jinja2
# {{ ansible_managed }}
[global]
log-level-console=info
log-level-file=detail
log-path={{ pgbackrest_log_path }}
start-fast=y
{% if pgbackrest_mode == 'server' %}
repo1-path={{ pgbackrest_repo_path }}
repo1-retention-full={{ pgbackrest_retention_full }}
{% if pgbackrest_s3_enabled %}
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
repo1-host-ca-file={{ pgbackrest_pki_dir }}/ca.crt
repo1-host-cert-file={{ pgbackrest_pki_dir }}/{{ inventory_hostname }}.crt
repo1-host-key-file={{ pgbackrest_pki_dir }}/{{ inventory_hostname }}.key
repo1-host-port={{ pgbackrest_tls_port }}
archive-async=y
spool-path=/var/spool/pgbackrest
{% endif %}
tls-server-address=*
tls-server-port={{ pgbackrest_tls_port }}
tls-server-ca-file={{ pgbackrest_pki_dir }}/ca.crt
tls-server-cert-file={{ pgbackrest_pki_dir }}/{{ inventory_hostname }}.crt
tls-server-key-file={{ pgbackrest_pki_dir }}/{{ inventory_hostname }}.key
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
{% for host in groups['postgres'] %}
pg{{ loop.index }}-host={{ host }}
pg{{ loop.index }}-host-type=tls
pg{{ loop.index }}-host-ca-file={{ pgbackrest_pki_dir }}/ca.crt
pg{{ loop.index }}-host-cert-file={{ pgbackrest_pki_dir }}/{{ inventory_hostname }}.crt
pg{{ loop.index }}-host-key-file={{ pgbackrest_pki_dir }}/{{ inventory_hostname }}.key
pg{{ loop.index }}-host-port={{ pgbackrest_tls_port }}
pg{{ loop.index }}-path={{ hostvars[host].pgbackrest_pg_path | default(pgbackrest_pg_path) }}
pg{{ loop.index }}-port={{ hostvars[host].pgbackrest_pg_port | default(pgbackrest_pg_port) }}
{% endfor %}
{% endif %}
{% if pgbackrest_mode == 'client' %}
pg1-path={{ pgbackrest_pg_path }}
pg1-port={{ pgbackrest_pg_port }}
{% endif %}
```

- [ ] **Step 2: Create `tasks/_config.yml`**

```yaml
---
- name: Render pgBackRest configuration
  ansible.builtin.template:
    src: pgbackrest.conf.j2
    dest: "{{ pgbackrest_config_file }}"
    owner: "{{ pgbackrest_user }}"
    group: "{{ pgbackrest_group }}"
    mode: "0640"
  no_log: "{{ pgbackrest_s3_enabled }}"
  notify: Restart pgbackrest
```

- [ ] **Step 3: Commit**

```bash
git add roles/pgbackrest/templates/pgbackrest.conf.j2 roles/pgbackrest/tasks/_config.yml
git commit -m "feat(pgbackrest): add config template and _config task"
```

---

### Task 4: TLS server service

**Files:**

- Create: `roles/pgbackrest/templates/pgbackrest.service.j2`
- Create: `roles/pgbackrest/tasks/_service.yml`

The official RHEL guide uses:

- `User=pgbackrest` on the repo host
- `User=postgres` on pg hosts

Since we always use `postgres`, `User={{ pgbackrest_user }}` resolves to `postgres` in both cases.

- [ ] **Step 1: Create `templates/pgbackrest.service.j2`**

```jinja2
# {{ ansible_managed }}
[Unit]
Description=pgBackRest Server
After=network.target
StartLimitIntervalSec=0

[Service]
Type=simple
Restart=always
RestartSec=1
User={{ pgbackrest_user }}
ExecStart=/usr/bin/pgbackrest server
ExecStartPost=/bin/sleep 3
ExecStartPost=/bin/bash -c "[ ! -z $MAINPID ]"
ExecReload=/bin/kill -HUP $MAINPID

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Create `tasks/_service.yml`**

```yaml
---
- name: Deploy pgBackRest server systemd unit
  ansible.builtin.template:
    src: pgbackrest.service.j2
    dest: /etc/systemd/system/pgbackrest.service
    owner: root
    group: root
    mode: "0644"
  notify: Reload systemd

- name: Flush handlers to reload systemd before starting service
  ansible.builtin.meta: flush_handlers

- name: Enable and start pgBackRest server daemon
  ansible.builtin.systemd:
    name: pgbackrest
    enabled: true
    state: started
```

- [ ] **Step 3: Commit**

```bash
git add roles/pgbackrest/templates/pgbackrest.service.j2 roles/pgbackrest/tasks/_service.yml
git commit -m "feat(pgbackrest): add TLS server systemd service template and _service task"
```

---

### Task 5: Firewall task (server mode)

**Files:**

- Create: `roles/pgbackrest/tasks/_firewall.yml`

Opens `pgbackrest_tls_port` (8432/tcp) on the server host, accepting connections from each postgres node's address.

- [ ] **Step 1: Create `tasks/_firewall.yml`**

```yaml
---
- name: Open pgBackRest TLS port from postgres nodes
  ansible.posix.firewalld:
    rich_rule: >-
      rule family='ipv4'
      source address='{{ hostvars[item].ansible_host | default(item) }}'
      port port='{{ pgbackrest_tls_port }}' protocol='tcp'
      accept
    permanent: true
    state: enabled
    immediate: true
    zone: "{{ pgbackrest_firewalld_zone }}"
  loop: "{{ groups['postgres'] }}"
  loop_control:
    label: "{{ item }}"
```

- [ ] **Step 2: Commit**

```bash
git add roles/pgbackrest/tasks/_firewall.yml
git commit -m "feat(pgbackrest): add _firewall task — open TLS port from postgres nodes"
```

---

### Task 6: Stanza creation

**Files:**

- Create: `roles/pgbackrest/tasks/_stanza.yml`

Runs on the server host only. Idempotent — ignores "already exists" errors.

- [ ] **Step 1: Create `tasks/_stanza.yml`**

```yaml
---
- name: Create pgBackRest stanza
  ansible.builtin.command:
    cmd: pgbackrest --stanza={{ pgbackrest_stanza }} stanza-create
  become: true
  become_user: "{{ pgbackrest_user }}"
  register: _pgbackrest_stanza_create
  changed_when: >-
    'completed successfully' in (_pgbackrest_stanza_create.stdout ~ _pgbackrest_stanza_create.stderr)
  failed_when: >-
    _pgbackrest_stanza_create.rc != 0
    and 'already exists' not in (_pgbackrest_stanza_create.stderr | default(''))

- name: Check pgBackRest stanza configuration
  ansible.builtin.command:
    cmd: pgbackrest --stanza={{ pgbackrest_stanza }} check
  become: true
  become_user: "{{ pgbackrest_user }}"
  changed_when: false
  register: _pgbackrest_stanza_check
  failed_when: _pgbackrest_stanza_check.rc != 0
```

- [ ] **Step 2: Commit**

```bash
git add roles/pgbackrest/tasks/_stanza.yml
git commit -m "feat(pgbackrest): add _stanza task — stanza-create and check"
```

---

### Task 7: WAL archive activation

**Files:**

- Create: `roles/pgbackrest/tasks/_archive.yml`

Sets `archive_mode` and `archive_command` by merging into `postgres_extra_parameters` on each postgres node, then re-renders `patroni.yml` and reloads Patroni.

The patroni template at `roles/patroni/templates/patroni.yml.j2:113` already iterates `postgres_extra_parameters` into the `parameters:` block. So injecting there is the right hook.

Runs from the server host and delegates to each postgres node.

- [ ] **Step 1: Create `tasks/_archive.yml`**

```yaml
---
- name: Inject archive_mode and archive_command into postgres_extra_parameters
  ansible.builtin.set_fact:
    postgres_extra_parameters: >-
      {{
        (hostvars[item].postgres_extra_parameters | default({})) | combine({
          'archive_mode': 'on',
          'archive_command': "pgbackrest --stanza=" ~ pgbackrest_stanza ~ " archive-push %p"
        })
      }}
  delegate_to: "{{ item }}"
  delegate_facts: true
  loop: "{{ groups['postgres'] }}"
  loop_control:
    label: "{{ item }}"

- name: Re-render patroni.yml with archive settings on each postgres node
  ansible.builtin.template:
    src: "{{ playbook_dir }}/../roles/patroni/templates/patroni.yml.j2"
    dest: "{{ hostvars[item].patroni_config_file | default('/etc/patroni/patroni.yml') }}"
    owner: root
    group: "{{ hostvars[item].postgres_group | default('postgres') }}"
    mode: "0640"
  delegate_to: "{{ item }}"
  loop: "{{ groups['postgres'] }}"
  loop_control:
    label: "{{ item }}"
  register: _pgbackrest_patroni_config

- name: Reload Patroni to apply archive settings
  ansible.builtin.systemd:
    name: patroni
    state: reloaded
  delegate_to: "{{ item }}"
  loop: "{{ groups['postgres'] }}"
  loop_control:
    label: "{{ item }}"
  when: _pgbackrest_patroni_config.results | selectattr('item', 'equalto', item) | map(attribute='changed') | first | default(false)

- name: Wait for archive_command to be active in PostgreSQL
  ansible.builtin.command:
    cmd: psql -U {{ hostvars[item].pgbackrest_user | default('postgres') }} -tAc "SHOW archive_command"
  become: true
  become_user: "{{ hostvars[item].pgbackrest_user | default('postgres') }}"
  delegate_to: "{{ item }}"
  loop: "{{ groups['postgres'] }}"
  loop_control:
    label: "{{ item }}"
  register: _pgbackrest_archive_cmd
  until: "'pgbackrest' in (_pgbackrest_archive_cmd.stdout | default(''))"
  retries: 12
  delay: 5
  changed_when: false
```

- [ ] **Step 2: Commit**

```bash
git add roles/pgbackrest/tasks/_archive.yml
git commit -m "feat(pgbackrest): add _archive task — inject archive_command via postgres_extra_parameters"
```

---

### Task 8: Backup timers

**Files:**

- Create: `roles/pgbackrest/templates/pgbackrest-backup@.service.j2`
- Create: `roles/pgbackrest/templates/pgbackrest-backup@.timer.j2`
- Create: `roles/pgbackrest/tasks/_timers.yml`

Two timers: `pgbackrest-backup@full.timer` and `pgbackrest-backup@diff.timer`. The `%i` instance name is passed as `--type` to `pgbackrest backup`.

- [ ] **Step 1: Create `templates/pgbackrest-backup@.service.j2`**

```jinja2
# {{ ansible_managed }}
[Unit]
Description=pgBackRest %i backup for stanza {{ pgbackrest_stanza }}
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User={{ pgbackrest_user }}
Group={{ pgbackrest_group }}
ExecStart=/usr/bin/pgbackrest --stanza={{ pgbackrest_stanza }} --type=%i backup
```

- [ ] **Step 2: Create `templates/pgbackrest-backup@.timer.j2`**

```jinja2
# {{ ansible_managed }}
[Unit]
Description=Scheduled pgBackRest %i backup for stanza {{ pgbackrest_stanza }}

[Timer]
OnCalendar={{ (timer_type == 'full') | ternary(pgbackrest_schedule_full, pgbackrest_schedule_diff) }}
Persistent=true
Unit=pgbackrest-backup@%i.service

[Install]
WantedBy=timers.target
```

- [ ] **Step 3: Create `tasks/_timers.yml`**

```yaml
---
- name: Deploy pgBackRest backup oneshot service template
  ansible.builtin.template:
    src: pgbackrest-backup@.service.j2
    dest: /etc/systemd/system/pgbackrest-backup@.service
    owner: root
    group: root
    mode: "0644"
  notify: Reload systemd

- name: Deploy pgBackRest backup timer units
  ansible.builtin.template:
    src: pgbackrest-backup@.timer.j2
    dest: "/etc/systemd/system/pgbackrest-backup@{{ item }}.timer"
    owner: root
    group: root
    mode: "0644"
  loop:
    - full
    - diff
  vars:
    timer_type: "{{ item }}"
  notify: Reload systemd

- name: Flush handlers to reload systemd before enabling timers
  ansible.builtin.meta: flush_handlers

- name: Enable and start backup timers
  ansible.builtin.systemd:
    name: "pgbackrest-backup@{{ item }}.timer"
    enabled: true
    state: started
  loop:
    - full
    - diff
```

- [ ] **Step 4: Commit**

```bash
git add roles/pgbackrest/templates/pgbackrest-backup@.service.j2 roles/pgbackrest/templates/pgbackrest-backup@.timer.j2 roles/pgbackrest/tasks/_timers.yml
git commit -m "feat(pgbackrest): add backup timer templates and _timers task"
```

---

### Task 9: Create the `_pgbackrest.yml` playbook and retire the old ones

The role implements both sides via `pgbackrest_mode`, so a single playbook runs the role twice — server play first (sets up the repo + daemon + stanza), then client play (registers `archive_command` and starts the client-side TLS daemon so the server can pull).

**Files:**

- Create: `playbooks/_pgbackrest.yml`
- Delete: `playbooks/_backup_client.yml`
- Delete: `playbooks/_backup_store.yml`

- [ ] **Step 1: Create `playbooks/_pgbackrest.yml`**

```yaml
---
- name: pgBackRest — server (backup_server group)
  hosts: backup_server
  become: true
  vars:
    pgbackrest_mode: server
  roles:
    - role: pgbackrest
      tags: [pgbackrest, backup]

- name: pgBackRest — client (postgres group)
  hosts: postgres
  become: true
  vars:
    pgbackrest_mode: client
  roles:
    - role: pgbackrest
      tags: [pgbackrest, backup]
```

The server play runs first because: (a) it owns the repo path and TLS daemon the clients connect back to, (b) `_archive.yml` inside server mode delegates to each postgres node and reloads Patroni — that delegation requires the postgres group to exist in inventory but does NOT require the client play to have run first (the client play only adds the postgres-side TLS daemon that the server uses to *pull* backups, not to push archive). Running server-then-client also means the server's stanza-create + initial check happen against fresh archiving on the postgres nodes.

- [ ] **Step 2: Remove the old playbooks**

```bash
git rm playbooks/_backup_client.yml playbooks/_backup_store.yml
```

- [ ] **Step 3: Commit**

```bash
git add playbooks/_pgbackrest.yml
git commit -m "feat(playbooks): replace _backup_client.yml + _backup_store.yml with _pgbackrest.yml"
```

---

### Task 10: Wire `_pgbackrest.yml` into orchestration

**Files:**

- Modify: `playbooks/site.yml`
- Modify: `playbooks/scale_add_replica.yml`
- Modify: `playbooks/scale_remove_replica.yml`

- [ ] **Step 1: Update `playbooks/site.yml`**

Find the block that currently imports both old playbooks:

```yaml
- name: Import P4 backup client playbook
  import_playbook: _backup_client.yml
  tags: [backup]
- name: Import P4 backup store playbook
  import_playbook: _backup_store.yml
  tags: [backup]
```

Replace with:

```yaml
- name: Import P4 pgbackrest playbook
  import_playbook: _pgbackrest.yml
  tags: [backup]
```

- [ ] **Step 2: Update `playbooks/scale_add_replica.yml`**

Around line 95 it imports `_backup_client.yml` limited to the new replica. Replace with `_pgbackrest.yml` limited to the new replica. Because `_pgbackrest.yml` has two plays (`backup_server` first, then `postgres`), limiting to one host runs only the client play (the new replica is in `postgres`, not in `backup_server`) — which is exactly what we want for adding a replica. Verify by reading the surrounding `ansible_limit` / `--limit` pattern in that file and matching its style.

The completion message text further down that file also references "the backup_store and monitoring" — update to "the backup_server and monitoring".

- [ ] **Step 3: Update `playbooks/scale_remove_replica.yml`**

Around line 116 the completion message says "the backup_store and monitoring" — update to "the backup_server and monitoring". No playbook import change is needed (replica removal doesn't run the backup playbook).

- [ ] **Step 4: Commit**

```bash
git add playbooks/site.yml playbooks/scale_add_replica.yml playbooks/scale_remove_replica.yml
git commit -m "feat(playbooks): wire _pgbackrest.yml into site.yml and replica scaling"
```

---

### Task 11: Rename the inventory group and group_vars

The inventory group is renamed from `backup_store` to `backup_server` to match the new role's terminology and the design-doc convention (the host is a *server* — the pgbackrest TLS server daemon — not a passive store).

**Files:**

- Rename: `group_vars/backup_store.yml` → `group_vars/backup_server.yml`
- Modify: `group_vars/all.yml`
- Modify: `inventory/examples/single.yml`
- Modify: `inventory/examples/ha.yml`
- Modify: `inventory/site.yml` (currently a "generated" file, but no `configure` script exists yet — edit it directly)

- [ ] **Step 1: Rename `group_vars/backup_store.yml`**

```bash
git mv group_vars/backup_store.yml group_vars/backup_server.yml
```

Then update its header comments to refer to `backup_server` instead of `backup_store`.

- [ ] **Step 2: Update `group_vars/all.yml`**

Rename two variables to match the new group:

- `backup_store_path` → `backup_server_path`
- `backup_store_user` → `backup_server_user`

Grep the whole repo for any other consumer of these variables after renaming and update them. (At time of writing, there should be none outside `group_vars/` and templates inside `roles/pgbackrest/` you wrote in Tasks 1–8.)

- [ ] **Step 3: Update inventory examples and `inventory/site.yml`**

In `inventory/examples/single.yml`, `inventory/examples/ha.yml`, and `inventory/site.yml`, rename the YAML key `backup_store:` (under `all.children:`) to `backup_server:`. The anchor/alias structure (`pgmon01: &id001` and `pgmon01: *id001`) stays as-is.

- [ ] **Step 4: Verify nothing still references the old group name**

```bash
grep -rn "backup_store\|backup_client" --include="*.yml" --include="*.yaml" --include="*.j2" .
```

The only remaining hits should be in `docs/` (intentional historical refs in plans/specs) and `roles/pgbackrest/` (only if you wrote them there — re-check; the role uses `groups['backup_server']`).

- [ ] **Step 5: Commit**

```bash
git add group_vars/ inventory/
git commit -m "refactor: rename inventory group backup_store -> backup_server"
```

---

### Task 12: Syntax-check and final verification

- [ ] **Step 1: Ansible syntax check**

```bash
ansible-playbook --syntax-check -i inventory/examples/single.yml playbooks/site.yml
ansible-playbook --syntax-check -i inventory/examples/ha.yml playbooks/site.yml
```

Both must exit zero. If they fail, fix and re-run before continuing.

- [ ] **Step 2: ansible-lint (if installed)**

```bash
ansible-lint roles/pgbackrest/ playbooks/_pgbackrest.yml
```

Address any new warnings/errors introduced by this work. Pre-existing lint debt in unchanged roles is not in scope.

- [ ] **Step 3: markdownlint on changed docs**

```bash
markdownlint-cli2 docs/superpowers/plans/2026-05-15-p4-pgbackrest.md roles/pgbackrest/README.md
```

Must report zero errors. The project linter config at `.markdownlint.yaml` is already relaxed for the codebase's house style.

- [ ] **Step 4: Molecule (libvirt) — DO NOT run as part of this plan**

Molecule scenarios for pgbackrest require libvirt and are local-only (see `docs/superpowers/specs/2026-05-12-pigsty-lite-design.md` §13.2). Flag in the final report that the user should run them on their libvirt host before considering P4 complete.

- [ ] **Step 5: No commit for this task** — syntax check is a verification gate, not a change.

---

## Self-Review

**Spec coverage:**

- ✓ Single role with two modes (`server`, `client`); no single-host mode per §1.1
- ✓ No `pgbackrest` OS user — all tasks use `User=postgres`
- ✓ Certs referenced directly from `pki_dir` — no copy/symlink
- ✓ `archive_command` via `postgres_extra_parameters` injection
- ✓ S3 secondary repo with Ansible Vault credentials
- ✓ Systemd timers for full + diff backups with configurable schedules
- ✓ `stanza-create` + `check` on server
- ✓ Firewall task on server only
- ✓ Single `_pgbackrest.yml` playbook replaces `_backup_client.yml` + `_backup_store.yml`
- ✓ Inventory group renamed `backup_store` → `backup_server` everywhere
- ✓ `playbooks/scale_add_replica.yml` updated to use `_pgbackrest.yml`
- ✓ Syntax check + ansible-lint + markdownlint gates before declaring done

**Out of scope (already done in prior commits):**

- `roles/backup_client/` and `roles/backup_store/` directories — already removed by commit `2fe4306` ("Remove old backup implementation"). The plan's earlier wording about "deleting bad_backup_*" is historical; the directories no longer exist on disk and Task 9+ below is the actual wiring work.

**Placeholder scan:** No TBDs, all steps include actual code.

**Type consistency:**

- `pgbackrest_user` used consistently across all templates and tasks
- `pgbackrest_stanza` referenced consistently in config, stanza, archive, and timer tasks
- `pgbackrest_pki_dir` used consistently in config template
- Handler name `Reload systemd` matches `handlers/main.yml` exactly
- Handler name `Restart pgbackrest` matches `handlers/main.yml` exactly
