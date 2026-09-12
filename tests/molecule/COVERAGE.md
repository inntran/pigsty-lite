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

The CI matrix is five derived-image scenarios plus the two raw bootstrap
scenarios chosen to collectively exercise every role as either a converge
target or via production-playbook prepare. Other scenarios remain in-tree and
can be run locally via `make test-role ROLE=<name>` but do not run in CI.

| Scenario                  | CI | Role(s) under test (converge) | Supporting roles (prepare)                                                                                  |
|---------------------------|:--:|-------------------------------|-------------------------------------------------------------------------------------------------------------|
| `cluster_ops / default`   | ✓  | `cluster_ops`*                | `preflight`, `ca`, `repos`, `node`, `certs`, `etcd`, `postgres`, `patroni`                                  |
| `backup / ha`             | ✓  | `pgbackrest`                  | `ca`, `repos`, `node`, `certs`, `etcd`, `postgres`, `patroni`                                               |
| `haproxy / ha`            | ✓  | `haproxy`                     | `ca`, `repos`, `node`, `certs`, `etcd`, `postgres`, `patroni`, `pgbouncer`                                  |
| `nginx_proxy / default`   | ✓  | `nginx_proxy`                 | `preflight`, `ca`, `repos`, `node`, `certs`, `monitoring_server`, `grafana`                                 |
| `monitoring_agents / default` |    | `monitoring_agents`          | `preflight`, `ca`, `repos`, `node`, `certs`, `etcd`, `postgres`, `patroni`, `pgbouncer`, `haproxy`, `pgbackrest`, `monitoring_server` |
| `monitoring_agents / external_push` | ✓ | `monitoring_agents`    | `preflight`, `ca`, `repos`, `node`, `certs`                                                                 |
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
current CI matrix: either as the converge target of one of those derived-image
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
| `monitoring_agents`| `monitoring_agents/default`                            | not in CI                                                        |
| `nginx_proxy`      | `nginx_proxy/default`                                  | ✓ converge: `nginx_proxy/default`                                |
| `vip_manager`      | `vip_manager/default`                                  | not in CI                                                        |

## Bootstrap track (raw oraclelinux:10)

Two scenarios run on the upstream `container-registry.oracle.com/os/oraclelinux:10`
image, not on a baked first-party image:

- `provision/default` — exercises `roles/repos` + `roles/node` + the full
  data-plane install path from a bare OS. Catches regressions in repo
  enablement, PGDG priority handling, the `pigsty` user/group, and
  systemd unit installation.
- `repos/default` — focused regression test for `roles/repos`.

These run in parallel with `build-common`/`build-data`/`build-infra` in
CI (see `.github/workflows/molecule.yml`).

`monitoring_agents/default` and `monitoring_agents/external_pull` were both
excluded from CI because the role's `_exporters.yml` and `_firewall.yml`
imports were commented out — none of the four exporters are packaged for
Oracle Linux 10 by PGDG, the vendor repos, or EPEL, so the `dnf` install they
used could not resolve.

That is fixed: the exporters now install from pinned upstream release
tarballs (`roles/monitoring_agents/vars/exporter_versions.yml`, refreshed by
`./bin/check_exporter_releases.py`), and both imports are re-enabled. The
install path was verified directly in a `molecule-base-data` container — all
four install at their pinned versions, a second run is `changed=0` with every
download skipped, and a corrupted binary triggers a reinstall of only that
exporter.

`external_pull` should now converge end to end.

`default` remains blocked, but on an unrelated pre-existing bug rather than
packaging: its `molecule.yml` places hosts in `monitor`/`backup_server` and
`postgres` but none in `etcd`, so `prepare` fails at `patroni : Fail if etcd
group is empty` before the role under test ever runs. Compare `backup/ha`,
which uses `groups: [etcd, postgres]`. Fixing that is a separate change.

`monitoring_agents/external_push` does run in CI. It covers the path where the
role handles secrets: remote_write credentials are staged as root-owned `0640`
files under `/etc/pigsty/monitoring` and passed to vmagent/vlagent as
`-remoteWrite.basicAuth.passwordFile` / `-remoteWrite.bearerTokenFile`. The
upstream `victoriametrics.cluster` roles render every service arg into the
`ExecStart` line of a world-readable unit, so verify asserts the secret values
appear in neither the unit file nor `/proc/<pid>/cmdline`. A local nginx stub
stands in for the external receiver, and its access log proves the agents
authenticated successfully rather than silently failing.

## VM binary versions

- **Test images** (`molecule-base-common`) chase GitHub `/releases/latest`
  at `podman build` time. Image rebuilds days apart can pull different
  binaries; the CI cache key is the Containerfile hash, so once cached an
  image stays put until the file changes.
- **Production deploys** use the version pinned in the upstream
  `victoriametrics.cluster.*` collection defaults (currently `v1.143.0` /
  `v1.50.0`). Operators override via inventory if needed:

  ```yaml
  victoriametrics_version: v1.143.0
  victorialogs_version:    v1.50.0
  vmagent_version:         v1.143.0
  vlagent_version:         v1.50.0
  ```
