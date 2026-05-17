# Molecule Test Coverage

This table maps each molecule scenario to the roles it exercises, distinguishing
the **role(s) under test** (run by `converge.yml` — the only roles whose own
`verify.yml` assertions execute) from **supporting roles** (run by
`prepare.yml`, exercised only insofar as they must succeed for the converge
step to begin).

When a scenario uses `import_playbook:` from `playbooks/_*.yml`, the imported
production playbook is expanded inline so the supporting-role column reflects
what actually runs.

## Production playbook → role mapping

These are the building blocks the import-based scenarios pull in:

| Playbook                       | Roles invoked              |
|--------------------------------|----------------------------|
| `_preflight.yml`               | `preflight`                |
| `_ca.yml`                      | `ca`                       |
| `_node.yml`                    | `repos`, `node`, `certs`   |
| `_etcd.yml`                    | `etcd`                     |
| `_postgres_install.yml`        | `postgres`                 |
| `_postgres_bootstrap.yml`      | `patroni`                  |
| `_pgbouncer.yml`               | `pgbouncer`                |
| `_haproxy.yml`                 | `haproxy`                  |
| `_vip_manager.yml`             | `vip_manager`              |
| `_provision.yml`               | `provision`                |
| `_pgbackrest.yml`              | `pgbackrest`               |
| `_monitoring_server.yml`       | `monitoring_server`        |
| `_monitoring_agents.yml`       | `monitoring_agents`        |
| `_grafana.yml`                 | `grafana`                  |
| `_nginx_proxy.yml`             | `nginx_proxy`              |

## Scenario coverage

`CI` column: ✓ = currently in `.github/workflows/molecule.yml` matrix.

The CI matrix is five scenarios chosen to collectively exercise every role as
either a converge target or via production-playbook prepare. Other scenarios
remain in-tree and can be run locally via `make test-role ROLE=<name>` but do
not run in CI.

| Scenario                  | CI | Role(s) under test (converge) | Supporting roles (prepare)                                                                                  |
|---------------------------|:--:|-------------------------------|-------------------------------------------------------------------------------------------------------------|
| `cluster_ops / default`   | ✓  | `cluster_ops`*                | `preflight`, `ca`, `repos`, `node`, `certs`, `etcd`, `postgres`, `patroni`                                  |
| `backup / ha`             | ✓  | `pgbackrest`                  | `ca`, `repos`, `node`, `certs`, `etcd`, `postgres`, `patroni`                                               |
| `haproxy / ha`            | ✓  | `haproxy`                     | `ca`, `repos`, `node`, `certs`, `etcd`, `postgres`, `patroni`, `pgbouncer`                                  |
| `nginx_proxy / default`   | ✓  | `nginx_proxy`                 | `preflight`, `ca`, `repos`, `node`, `certs`, `monitoring_server`, `grafana`                                 |
| `monitoring_agents / default` | ✓ | `monitoring_agents`          | `preflight`, `ca`, `repos`, `node`, `certs`, `etcd`, `postgres`, `patroni`, `pgbouncer`, `haproxy`, `pgbackrest`, `monitoring_server` |
| `preflight / default`     |    | `preflight`                   | —                                                                                                           |
| `repos / default`         |    | `repos`                       | —                                                                                                           |
| `node / default`          |    | `repos`, `node`               | —                                                                                                           |
| `ca / default`            |    | `ca`                          | —                                                                                                           |
| `certs / default`         |    | `certs`                       | `ca`, `node`                                                                                                |
| `etcd / spof`             |    | `etcd`                        | `ca`, `repos`, `node`, `certs`                                                                              |
| `etcd / ha`               |    | `etcd`                        | `ca`, `repos`, `node`, `certs`                                                                              |
| `postgres / default`      |    | `postgres`                    | `ca`, `repos`, `node`, `certs`                                                                              |
| `patroni / spof`          |    | `patroni`                     | `ca`, `repos`, `node`, `certs`, `etcd`, `postgres`                                                          |
| `patroni / ha`            |    | `patroni`                     | `ca`, `repos`, `node`, `certs`, `etcd`, `postgres`                                                          |
| `pgbouncer / default`     |    | `pgbouncer`                   | `ca`, `repos`, `node`, `certs`, `etcd`, `postgres`, `patroni`                                               |
| `haproxy / default`       |    | `haproxy`                     | `ca`, `repos`, `node`, `certs`, `etcd`, `postgres`, `patroni`, `pgbouncer`                                  |
| `provision / default`     |    | `provision`                   | `ca`, `repos`, `node`, `certs`, `etcd`, `postgres`, `patroni`                                               |
| `provision / ha`          |    | `provision`                   | `ca`, `repos`, `node`, `certs`, `etcd`, `postgres`, `patroni`                                               |
| `backup / default`        |    | `pgbackrest`                  | `ca`, `repos`, `node`, `certs`, `etcd`, `postgres`, `patroni`                                               |
| `grafana / default`       |    | `grafana`                     | `preflight`, `ca`, `repos`, `node`, `certs`, `monitoring_server`                                            |
| `monitoring_server / default` |  | `monitoring_server`           | `preflight`, `ca`, `repos`, `node`, `certs`                                                                 |
| `vip_manager / default`   |    | `vip_manager`                 | `repos`                                                                                                     |

\* `cluster_ops/default`'s converge does not apply the `cluster_ops` role
directly; it invokes `cluster_ops`'s `find_leader.yml` and `assert_healthy.yml`
task files via `include_role`. The prepare brings up the full SPOF stack via
production playbooks, making this scenario the closest thing to an end-to-end
smoke test.

## Per-role coverage (which scenario asserts each role's outputs)

A role appears under "verified by" only for scenarios whose `verify.yml`
asserts against that role's outputs (i.e. converge target). Scenarios that
merely run the role in prepare exercise its tasks for breakage but do not
assert its results.

"Verified by" lists every in-tree scenario whose `verify.yml` asserts against
that role's outputs. "Exercised in CI" notes how the role gets touched by the
five-scenario CI matrix: either as the converge target of one of those five
scenarios, or as a supporting role run via prepare (in which case task-level
breakage is still caught, but the role's own `verify.yml` does not run).

| Role               | Verified by (converge target)                          | Exercised in CI                                                  |
|--------------------|--------------------------------------------------------|------------------------------------------------------------------|
| `preflight`        | `preflight/default`                                    | prepare of `cluster_ops/default`, `nginx_proxy/default`, `monitoring_agents/default` |
| `repos`            | `repos/default`, `node/default`                        | prepare of all five                                              |
| `node`             | `node/default`                                         | prepare of all five                                              |
| `ca`               | `ca/default`                                           | prepare of all five                                              |
| `certs`            | `certs/default`                                        | prepare of all five                                              |
| `etcd`             | `etcd/spof`, `etcd/ha`                                 | prepare of `cluster_ops/default`, `backup/ha`, `haproxy/ha`, `monitoring_agents/default` |
| `postgres`         | `postgres/default`                                     | prepare of `cluster_ops/default`, `backup/ha`, `haproxy/ha`, `monitoring_agents/default` |
| `patroni`          | `patroni/spof`, `patroni/ha`                           | prepare of `cluster_ops/default`, `backup/ha`, `haproxy/ha`, `monitoring_agents/default` |
| `pgbouncer`        | `pgbouncer/default`                                    | prepare of `haproxy/ha`, `monitoring_agents/default`             |
| `haproxy`          | `haproxy/default`, `haproxy/ha`                        | ✓ converge: `haproxy/ha`; prepare of `monitoring_agents/default` |
| `provision`        | `provision/default`, `provision/ha`                    | not in CI                                                        |
| `pgbackrest`       | `backup/default`, `backup/ha`                          | ✓ converge: `backup/ha`; prepare of `monitoring_agents/default`  |
| `cluster_ops`      | `cluster_ops/default`                                  | ✓ converge: `cluster_ops/default`                                |
| `grafana`          | `grafana/default`                                      | prepare of `nginx_proxy/default`                                 |
| `monitoring_server`| `monitoring_server/default`                            | prepare of `nginx_proxy/default`, `monitoring_agents/default`    |
| `monitoring_agents`| `monitoring_agents/default`                            | ✓ converge: `monitoring_agents/default`                          |
| `nginx_proxy`      | `nginx_proxy/default`                                  | ✓ converge: `nginx_proxy/default`                                |
| `vip_manager`      | `vip_manager/default`                                  | not in CI                                                        |
