# Single-node (SPOF) example

A complete, worked `spof` deployment: one monitor/backup host and one
PostgreSQL host carrying two application databases.

`responses/spof.rsp.yml.example` in the repo root is a skeleton — it shows
which keys exist and leaves the values for you to invent. This bundle is the
opposite: every optional block a real deployment wants is filled in, and the
generated output is checked in next to it so you can see what each response
key turns into before you run anything.

## What's here

| File | What it is |
| --- | --- |
| `site.rsp.yml` | The response file. **This is the only file you edit.** |
| `generated/inventory-site.yml` | What `./configure` emits as `inventory/site.yml` |
| `generated/group_vars-all-response.yml` | What it emits as `inventory/group_vars/all/response.yml` |

The two files under `generated/` are reference copies, produced by running
`./configure -s -f examples/single-node/site.rsp.yml --no-vault` and copying
the results out of `inventory/`. (Their `# Command:` header always names
`responses/site.rsp.yml` — `configure` writes the conventional path, not the
one you passed.) Nothing reads them at deploy time; they are there so you can
diff a response-file change against its effect without touching your live
inventory.

## Using it

```bash
cp examples/single-node/site.rsp.yml responses/site.rsp.yml
$EDITOR responses/site.rsp.yml          # IPs, domain, CIDRs, db/user names
./configure --validate responses/site.rsp.yml
```

Application logins are not auto-generated (`./configure` only manages the
fixed infrastructure secrets — see [`../../docs/secrets.md`](../../docs/secrets.md)).
The response file references vault keys, so add the values before deploying:

```bash
./configure -s -f responses/site.rsp.yml   # bootstraps the vault
ansible-vault edit inventory/group_vars/all/vault.yml
# vault_orders_app_password: ...
# vault_billing_app_password: ...
# vault_reporting_password: ...
```

Then:

```bash
make plan      # site.yml --check --diff
make deploy    # site.yml
```

`make plan` and `make deploy` both run `make regen` first, so an edited
response file is never stale against the inventory.

## The topology this produces

```
pgmon01  10.20.30.10   monitor + backup_server
                       VictoriaMetrics, VictoriaLogs, Alertmanager,
                       Grafana, nginx, pgBackRest repository

pgnode01 10.20.30.11   etcd + postgres
                       etcd (1 member), PostgreSQL 18, Patroni, pgBouncer,
                       vip-manager (disabled here), and the four exporters
```

Two details about `spof` that catch people out:

- **etcd runs on the PostgreSQL node.** `./configure` places it there
  automatically; you do not declare an etcd role in `nodes:`. A single
  member is a quorum of one — it has no fault tolerance, it exists because
  Patroni needs a DCS.
- **HAProxy is not deployed.** `playbooks/site.yml` gates the HAProxy play
  on `cluster_profile != 'spof'`. The `db_routing.haproxy` block is still
  in the response file so the file converts to an `ha` profile by editing
  two keys, but it has no effect here. Clients connect to pgnode01's
  pgBouncer (6432) or PostgreSQL (5432) directly.

## Reaching the deployment

| | Where |
| --- | --- |
| PostgreSQL | `pgnode01:5432` |
| pgBouncer | `pgnode01:6432` |
| Patroni REST | `https://pgnode01:8008` |
| Grafana | `https://pgmon01.pg-single.example.internal/` via nginx, admin / `vault_grafana_admin_password`. Grafana itself binds loopback:3000 |
| VictoriaMetrics | `pgmon01:8428` — this is how vmagent on pgnode01 remote-writes, so it is firewall-gated, not loopback-bound |

`firewall.operator_cidrs` gates the admin surfaces and
`firewall.postgres_client_cidrs` gates 5432/6432. They are deliberately
different ranges in the example: keep client access narrower than operator
access.

## Running one part of the stack

`playbooks/site.yml` is tag-driven, so you do not need a separate playbook
to call a single role:

```bash
ansible-playbook playbooks/site.yml --tags monitoring   # exporters + VM + Grafana
ansible-playbook playbooks/site.yml --tags provision    # databases, users, HBA
ansible-playbook playbooks/site.yml --tags backup       # pgBackRest
```

Or run the per-phase playbook directly — these are the same files
`site.yml` imports:

```bash
ansible-playbook playbooks/_monitoring_agents.yml
ansible-playbook playbooks/_provision.yml
```

See [`../../playbooks/tags.md`](../../playbooks/tags.md) for the full tag list.

## Related

- [`../../docs/operations/migrate-into-spof.md`](../../docs/operations/migrate-into-spof.md)
  — loading existing live databases into this deployment
- [`../../docs/secrets.md`](../../docs/secrets.md) — what the vault manages
  and what it does not
- [`../../responses/spof.rsp.yml.example`](../../responses/spof.rsp.yml.example)
  — the minimal skeleton this example expands on
