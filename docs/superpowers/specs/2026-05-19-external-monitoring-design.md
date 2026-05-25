# External monitoring mode

Date: 2026-05-19
Status: Approved — ready for implementation

## Problem

pigsty-lite currently always deploys its own observability stack: a
`monitor` host running VictoriaMetrics (vmsingle), VictoriaLogs
(vlsingle), vmalert, Alertmanager, Grafana, and an nginx reverse proxy.
Some operators already run a managed monitoring service and want to hand
observability off to it rather than stand up a parallel stack.

This spec adds a `monitoring.mode` option so a deployment can target an
external monitoring service instead of self-hosting.

## Goals

- Let an operator point pigsty-lite at an external VictoriaMetrics /
  VictoriaLogs (or compatible) service instead of self-hosting.
- Support both ingestion directions, because operators do not always
  control the external service:
  - **push** — local agents `remote_write` to the external service.
  - **pull** — the external service scrapes the postgres nodes.
- Drop the `monitor` host entirely when monitoring is external.
- Keep `self_hosted` behaviour byte-for-byte unchanged and the default.

## Non-goals

- Backups. Backup topology (`backup_store` node, monitor fallback, or
  `backup: {enabled: false}`) is out of scope for this change. The
  `pgbackrest_exporter` already follows the pgBackRest repo host via the
  `monitoring_agents` role running on `hosts: all`; it is unaffected.
- Replicated / HA self-hosted monitoring.
- Provisioning the external service itself (creating tenants, dashboards
  on the managed side, etc.).

## Background — current monitoring model

Important facts that shape this design:

- pigsty-lite uses a **push** model internally. Every exporter
  (node, postgres, pgbouncer, pgbackrest) binds **loopback only**. The
  local `vmagent` on each node scrapes them over loopback, then
  `remote_write`s to the central vmsingle. The external monitor never
  pulls from postgres nodes in `self_hosted` mode.
- `roles/monitoring_agents/tasks/_firewall.yml` opens the exporter
  ports to the monitor host *only* — that is the sole scrape source.
- `roles/monitoring_agents` runs on `hosts: all`.
- The `monitor` node currently also serves as the `backup_server`
  fallback (`bin/_generate_inventory.py`) and hosts Grafana +
  nginx_proxy.
- HAProxy runs only on postgres nodes (`_haproxy.yml` is
  `hosts: postgres`); `backup_store` and `monitor` nodes have no
  HAProxy. The `external_pull` frontend therefore cannot rely on
  HAProxy and uses nginx, which `monitoring_agents` installs itself.
- `monitoring_host` is defined in `group_vars/all/main.yml` as
  `{{ groups['monitor'][0] }}`.

## Design overview

A new `monitoring.mode` with three values:

| mode             | self-hosted stack | agents     | ingestion                          |
|------------------|-------------------|------------|------------------------------------|
| `self_hosted`    | yes (unchanged)   | push to monitor | n/a                           |
| `external_push`  | no                | push       | agents `remote_write` to external  |
| `external_pull`  | no                | local scrape only | external service scrapes nodes |

`self_hosted` is the default; existing response files keep working with
no edits.

## 1. Response file schema (`bin/_response_schema.py`)

New shape under the existing `monitoring:` block:

```yaml
monitoring:
  mode: external_push        # self_hosted | external_push | external_pull
  scrape_interval: 15s       # existing key, still optional, default 15s

  # required iff mode == external_push; forbidden otherwise
  external_push:
    metrics_url: https://vm.corp/api/v1/write     # required, http(s) URL
    logs_url: https://vl.corp/insert/jsonline     # required, http(s) URL
    auth:                                          # optional block
      username: pigsty       # optional; basic-auth identifier
      bearer: false          # optional bool; true => bearer token prompted
    tls_skip_verify: false   # optional bool, default false

  # required iff mode == external_pull; forbidden otherwise
  external_pull:
    metrics_port: 9965       # required int, 1-65535
    auth:
      username: pigsty       # required; basic auth is always on for pull
    source_cidrs:            # required, non-empty list of CIDRs;
      - "10.0.0.0/8"         #   use "0.0.0.0/0" to disable IP filtering
    tls: true                # optional bool, default true
```

### Validation rules

- `monitoring` is still a required top-level key (unchanged).
- `mode` is optional; defaults to `self_hosted`. Must be one of the
  three allowed values. Add `ALLOWED_MONITORING_MODES` set.
- `mode == self_hosted`:
  - `vmsingle_retention` and `vlsingle_retention` remain **required**
    (current behaviour).
  - `external_push` and `external_pull` keys must be **absent**.
- `mode == external_push`:
  - `external_push` block **required**; `external_pull` absent.
  - `vmsingle_retention` / `vlsingle_retention` are **ignored** (not
    required; if present, allowed but unused — they only govern
    self-hosted stores).
  - `metrics_url` and `logs_url` required, must be non-empty strings
    parseable as `http://` or `https://` URLs. Add a `_check_url`
    helper.
  - `auth` optional. If present: `username` optional string; `bearer`
    optional bool. Reject unknown keys in `auth`.
  - `tls_skip_verify` optional bool, default `false`.
- `mode == external_pull`:
  - `external_pull` block **required**; `external_push` absent.
  - `vmsingle_retention` / `vlsingle_retention` ignored (as above).
  - `metrics_port` required int in `1..65535`.
  - `auth.username` required non-empty string (basic auth always on).
  - `source_cidrs` required non-empty list; each entry validated with
    the existing `_check_cidr` helper against `network.ip_version`.
  - `tls` optional bool, default `true`.
- Secrets never appear in the response file. The schema validates only
  the *shape*: `username` (an identifier) and `bearer` (a flag). The
  password and bearer token live in the vault.

### Node-count validation (`_validate_nodes`)

`_validate_nodes` currently requires exactly 1 `monitor`. Change so it
takes the monitoring mode and:

- `self_hosted`: exactly 1 `monitor` (unchanged).
- `external_push` / `external_pull`: 0 or 1 `monitor` allowed; more than
  1 still rejected. 0 is the expected case.

The `pg_primary` / `pg_replica` rules are unchanged.

## 2. Inventory generator (`bin/_generate_inventory.py`)

- Tolerate zero monitor nodes. `monitor_hosts` may be an empty list; the
  `monitor` group is still emitted but with no hosts.
- `backup_server` fallback chain: today it falls back to the monitor
  hosts when there is no `backup_store` node. Extend so that when there
  is also no monitor node, it falls back to the `pg_primary` node.
  Order: `backup_store` → `monitor` → `pg_primary`.

## 3. Playbook gating (`playbooks/site.yml`)

Add `when: groups['monitor'] | length > 0` to these three
`import_playbook` entries so they no-op when there is no monitor host:

- `_monitoring_server.yml`
- `_grafana.yml`
- `_nginx_proxy.yml`

`_monitoring_agents.yml` still runs unconditionally (`hosts: all`).

`roles/monitoring_server/tasks/_assert.yml` keeps its "exactly 1
monitor" assert — that play simply will not be imported in external
modes, so the assert is never reached.

## 4. `monitoring_agents` role — three branches

The role keys behaviour off a single resolved variable
`monitoring_mode` (plumbed from the response file via
`group_vars/response.yml`). Default `self_hosted`.

- **`self_hosted`** — unchanged. vmagent/vlagent push to
  `monitoring_host`; `_firewall.yml` opens exporter ports to the monitor
  host.
- **`external_push`**:
  - `vmagent` `remote_write` URL = `external_push.metrics_url`;
    `vlagent` ship URL = `external_push.logs_url`.
  - Apply basic-auth (`username` + vault password), bearer token (vault),
    and `tls_skip_verify` to the vmagent/vlagent remote-write config per
    the agents' config templates.
  - `_firewall.yml` exporter-opening tasks are **skipped** — exporters
    stay loopback-only and nothing pulls in. Only outbound egress to the
    external URLs is needed (assumed already permitted).
- **`external_pull`**:
  - Exporters stay loopback-only.
  - vmagent/vlagent are **not** configured for remote_write (no external
    push target). The simplest correct behaviour is to not deploy
    vmagent/vlagent remote-write config at all in this mode; the nginx
    metrics frontend is the data path.
  - Deploy the nginx metrics frontend (section 5).
  - `_firewall.yml` opens `external_pull.metrics_port` to
    `external_pull.source_cidrs` only (reuse the rich-rule pattern
    already in `_firewall.yml`, retargeted from the monitor host to the
    configured CIDRs; one rule per CIDR, family derived per entry).

`monitoring_host` references in `roles/monitoring_agents/` must be
guarded so they are only evaluated in `self_hosted` mode (they would
fail with an empty `monitor` group otherwise).

## 5. nginx metrics frontend (owned by `monitoring_agents`)

Only deployed in `external_pull` mode. nginx — not HAProxy — is the
frontend, because `monitoring_agents` runs on `hosts: all` and HAProxy
is only installed on postgres nodes (`_haproxy.yml` is `hosts:
postgres`). A `backup_store` or `monitor` node has no HAProxy, so a
HAProxy-based frontend cannot be built there. `monitoring_agents`
installs nginx itself, giving every node a consistent metrics endpoint
regardless of role.

- `monitoring_agents` installs the `nginx` package and renders
  `/etc/nginx/conf.d/pigsty-metrics.conf` — one `server` block bound on
  `external_pull.metrics_port`. nginx reads `conf.d/*.conf` natively, so
  no systemd drop-in or unit override is needed.
- Path routing via `location` blocks; the exporters all serve at
  `/metrics`, so each location `proxy_pass`es to the exporter's
  `/metrics`:

  ```
  /metrics/node       -> 127.0.0.1:9100/metrics
  /metrics/postgres   -> 127.0.0.1:9187/metrics
  /metrics/pgbouncer  -> 127.0.0.1:9127/metrics
  /metrics/pgbackrest -> 127.0.0.1:9854/metrics
  ```

  `/metrics/node` exists on every host. The postgres/pgbouncer/pgbackrest
  locations exist only on postgres hosts; the template guards them the
  same way `vmagent-scrape.yml.j2` does
  (`inventory_hostname in groups.get('postgres', [])`). Any other path
  returns 404.
- Basic auth: an `auth_basic` realm plus an `auth_basic_user_file`
  htpasswd file. The htpasswd entry is generated from
  `external_pull.auth.username` and the vault password, **hashed** at
  render time (`community.general.htpasswd` module or
  `password_hash('apr_md5_crypt')`) so the cleartext password is not
  written into an on-disk config file. Always on.
- TLS: when `external_pull.tls` is true, the `server` block uses
  `listen <port> ssl` with `ssl_certificate` / `ssl_certificate_key`
  pointing at the node's existing P0 certs-role material
  (`{{ pigsty_pki_dir }}/{{ inventory_hostname }}.crt` and `.key`). nginx
  takes the cert and key as separate directives, so no PEM concatenation
  step is needed. When `tls` is false, plain HTTP `listen`.
- The external monitor scrapes with one job per path, e.g.:

  ```yaml
  scrape_configs:
    - job_name: pigsty-node
      scheme: https
      metrics_path: /metrics/node
      basic_auth: { username: pigsty, password: <secret> }
      static_configs:
        - targets: ['pgnode01.corp:9965']
  ```

## 6. configure prompts (`configure`, interactive)

After the existing cluster prompts, add a monitoring-mode prompt:

```
Monitoring mode (self_hosted|external_push|external_pull) [self_hosted]:
```

- `self_hosted` — no further monitoring prompts (current behaviour).
- `external_push` — prompt: `metrics_url`, `logs_url`,
  optional `username` (if given, prompt password -> vault),
  `bearer?` (if yes, prompt token -> vault), `tls_skip_verify?`.
- `external_pull` — prompt: `metrics_port` (default 9965),
  `username` (required; prompt password -> vault),
  `source_cidrs` (comma-separated), `tls?` (default yes).

Write the collected non-secret values into the `monitoring:` block of
`responses/site.rsp.yml` and validate before emitting inventory /
response vars.

Silent mode (`-s -f FILE`) reads the same `monitoring:` keys from the
response file. As today, silent mode does not prompt: the relevant vault
secrets must already exist (run interactive `configure` first to
bootstrap them, consistent with `_ensure_vault_silent`).

## 7. Vault keys (`bin/_passwords.py`)

Add human-secret keys, prompted by `ensure_human_secrets` only when the
selected mode needs them:

- `vault_monitoring_external_password` — `external_push`, only when
  `auth.username` is set.
- `vault_monitoring_external_bearer_token` — `external_push`, only when
  `auth.bearer` is true.
- `vault_monitoring_pull_password` — `external_pull` (always, basic auth
  is mandatory there).

These are conditional human secrets: `ensure_human_secrets` needs the
resolved monitoring config to decide which to prompt for. Plumb the
monitoring block (or the specific flags) into the secret-ensuring path.

## 8. Response vars generator (`bin/_generate_response_vars.py`)

Emit into `group_vars/response.yml`:

- `monitoring_mode`
- For `external_push`: `monitoring_external_metrics_url`,
  `monitoring_external_logs_url`, `monitoring_external_auth_username`
  (or empty), `monitoring_external_auth_bearer` (bool),
  `monitoring_external_tls_skip_verify` (bool).
- For `external_pull`: `monitoring_pull_metrics_port`,
  `monitoring_pull_auth_username`, `monitoring_pull_source_cidrs`,
  `monitoring_pull_tls` (bool).

Existing `vmsingle_retention` / `vlsingle_retention` /
`alertmanager_receivers` / `monitoring_scrape_interval` keys are emitted
only in `self_hosted` mode (or emitted always but unused — pick the
former for cleanliness; they are meaningless without a monitor host).

## 9. Operator documentation for the external service

The spec so far covers the pigsty-lite side. The operator also has to
configure the *external* monitoring service to receive (push) or scrape
(pull) the data. That is delivered two ways:

### 9a. Static conceptual doc

New file `docs/operations/external-monitoring.md`. Covers the parts that
are identical for every deployment:

- The three modes and the push vs. pull tradeoff.
- What endpoints the external service must accept in push mode
  (`/api/v1/write` for metrics, `/insert/jsonline` for logs) and that
  any VictoriaMetrics/Prometheus-remote-write-compatible service works.
- The `external_pull` nginx metrics frontend: the
  `/metrics/<exporter>` URL scheme, basic auth, TLS, the per-node
  `metrics_port`.
- The auth model (basic auth / bearer, where each secret lives).
- A pointer to the generated per-deployment snippet (9b).

This file uses placeholder hostnames/ports — it is concepts, not a
copy-paste config.

### 9b. Generated per-deployment snippet

On every non-silent `configure` run in an external mode, emit
`responses/external-monitoring.md` — an annotated-markdown file that
mixes ready config blocks with prose explaining each field and where it
goes in the operator's monitoring system. It is regenerated each run, so
it always matches the current response file. Generation lives in a new
`bin/_generate_external_monitoring_doc.py` module, called from
`configure` after the response/inventory files are written, only when
`monitoring.mode` is external.

- **`external_pull`** — the file contains a `scrape_configs` block with
  one job per exporter (`node`, and `postgres`/`pgbouncer`/`pgbackrest`
  for postgres hosts), every job pre-filled with the deployment's real
  node hostnames (from `nodes`), the configured `metrics_port`, the
  `/metrics/<exporter>` path, `scheme` derived from `external_pull.tls`,
  and `basic_auth.username` from `external_pull.auth.username`. The
  password is shown as a `<from vault: vault_monitoring_pull_password>`
  placeholder — the doc explains where to retrieve it, it is never
  written in clear text. Prose explains this is for a Prometheus- or
  vmagent-style scraper and the operator pastes it into their config.
- **`external_push`** — the file is a checklist plus the remote-write
  target details: `metrics_url`, `logs_url`, the auth method in use
  (basic-auth username and/or bearer, with vault-key placeholders for the
  secrets), and `tls_skip_verify`. Prose explains the operator's service
  must expose those ingest endpoints and accept the configured auth;
  this is mostly a verification checklist since pigsty-lite's agents do
  the pushing.

Secrets are never rendered into this file — only vault-key-name
placeholders. The file is regenerated (overwritten) each run. Add
`responses/external-monitoring.md` to `.gitignore` if response artifacts
are not already ignored (check current `.gitignore`; `responses/` may
already be partly ignored — match existing convention).

## 10. Response presets + day-2 docs

- `responses/spof.rsp.yml.example` and `responses/ha.rsp.yml.example`:
  add commented-out `external_push` and `external_pull` example blocks
  under `monitoring:` showing the full shape.
- `docs/operations/day2-monitoring.md`: add an "External monitoring"
  section that summarizes the three modes and links to
  `docs/operations/external-monitoring.md` (the detailed doc from 9a)
  rather than duplicating it.

## Error handling

- Schema rejects: unknown `mode`; missing/extra `external_*` block for
  the mode; malformed URLs; out-of-range `metrics_port`; empty
  `source_cidrs`; invalid CIDRs; >1 monitor node.
- Inventory generator must not raise on zero monitor nodes.
- `monitoring_agents` role: any `monitoring_host` /
  `hostvars[monitoring_host]` access is guarded by
  `monitoring_mode == 'self_hosted'`.
- Silent `configure` with an external mode but missing vault secret:
  fail with a clear message pointing at interactive `configure`,
  consistent with the existing `_ensure_vault_silent` behaviour.

## Testing

- Schema unit tests: valid `self_hosted` (default + explicit), valid
  `external_push` (with and without `auth`), valid `external_pull`;
  rejection cases for each rule in section 1; node-count tests for
  0 / 1 / 2 monitor nodes per mode.
- Inventory generator test: zero-monitor response produces an empty
  `monitor` group and `backup_server` falling back to the primary.
- Molecule scenario for `monitoring_agents` in `external_pull`:
  verify the nginx metrics frontend serves `/metrics/node` and
  requires basic auth (401 without credentials, 200 with).
- Unit test for `_generate_external_monitoring_doc`: an `external_pull`
  response produces a `scrape_configs` block with one job per exporter
  and the real node hostnames; an `external_push` response produces the
  remote-write checklist; secrets appear only as vault-key placeholders,
  never in clear text.

## Open questions

None — all resolved during brainstorming.
