# Molecule tiered base images design

## Problem

`tests/molecule/Containerfile` builds a single kitchen-sink image (`localhost/molecule-base:latest`) containing every package any scenario might need. The image is monolithic, and every scenario also re-runs the full `roles/repos` + `roles/node` install path during `prepare.yml` even though those tasks are immutable across runs. Two problems follow:

- Slow molecule turnaround. Each converge re-installs packages and repos that were already present in the image.
- Weak signal. Because every scenario starts from the same fully-loaded image, we never exercise the "true minimal → working stack" path. A regression in `roles/repos` against a bare OS would not be caught by any current scenario.

## Goals

- Provide a small, focused image per testing tier so each scenario starts close to the state its target role expects.
- Preserve at least one scenario that bootstraps from raw upstream OS to prove the repos + node roles still work end-to-end.
- Move work that is purely "bake-time" (package install, repo enablement, the `pigsty` system user) out of molecule prepare and the affected role tasks' hot path on test runs, without modifying the roles themselves.
- Keep layer sharing across images so disk and build cost stays bounded.

## Non-goals

- Refactoring `roles/repos` or `roles/node`. Their tasks remain untouched and run as idempotent no-ops on the baked images.
- CI workflow restructuring beyond image build + job-level parallelism between bootstrap scenarios and image builds. The scenario list and molecule invocation are unchanged.
- Switching base OS off Oracle Linux 10.
- Multi-arch (arm64) builds.
- Introducing a remote registry or registry service.

## Architecture

Four images in play, three of them first-party:

```
container-registry.oracle.com/os/oraclelinux:10        upstream "true minimal"
  │   used directly by: provision/default, repos/default
  │
  ▼
localhost/molecule-base-common:latest                  shared baseline
  │   used by: preflight, node, ca, certs
  │
  ├──▶ localhost/molecule-base-data:latest             data-plane stack
  │
  └──▶ localhost/molecule-base-infra:latest            infra/monitoring stack
```

`data` and `infra` are derived from `common` via `FROM`, so podman shares the underlying layers on disk. Only the tier-specific RUN steps add new layers.

### Image contents

**common** (`FROM container-registry.oracle.com/os/oraclelinux:10`)

- Bootstrap system packages: `iproute`, `man-db`, `sudo`, `firewalld`, `policycoreutils`, `policycoreutils-python-utils`, `python3-libselinux`, `python3-cryptography`, `dnf-plugins-core`.
- EPEL + CRB enabled: install `epel-release` RPM, enable `ol10_codeready_builder`. EPEL repo is then set to `enabled=0` to match the terminal state of `roles/repos` and keep converge idempotence checks green.
- PGDG: install `pgdg-redhat-repo-latest.noarch.rpm`, enable `pgdg-rhel10-extras` and `pgdg18`, disable `pgdg14..pgdg17`, set `priority=10` on PGDG sections.
- PG default module disabled: `dnf -y module disable postgresql` plus `/etc/dnf/modules.d/postgresql-disabled` marker file so `roles/repos` skips the disable step.
- `pigsty` group (gid 926) and `pigsty` user (uid 926, home `/var/lib/pigsty` mode 0750, shell `/sbin/nologin`).
- Pre-created `/etc/sysctl.d/` and `/etc/systemd/journald.conf.d/` (cheap; saves a few node-role tasks).
- Sudoers: `%wheel ALL=(ALL) NOPASSWD: ALL` and `ALL ALL=(ALL) NOPASSWD: ALL`.
- `pgbackrest` package (used by both data-side and infra-side; mode selected at converge time).
- VictoriaMetrics binaries unpacked into `/usr/local/bin/`: `victoria-metrics-prod`, `victoria-logs-prod`, `vmalert-prod`, `vmagent-prod`, `vlagent-prod`. Versions are resolved at image build time by following the GitHub `/releases/latest` redirect (same pattern the upstream `victoriametrics.cluster` roles use when `*_version: latest`). This intentionally accepts non-reproducible builds across days in exchange for staying in lockstep with whatever the role would have downloaded.
- `CMD ["/sbin/init"]`.

**data** (`FROM localhost/molecule-base-common:latest`)

- `etcd`, `patroni`, `patroni-etcd`
- `postgresql18-server`, `postgresql18-contrib`, `postgresql18`
- `pgbouncer`, `haproxy`, `vip-manager`
- `python3-psycopg3`
- Exporters: `golang-github-prometheus-node-exporter`, `postgres_exporter`, `pgbouncer_exporter`, `pgbackrest_exporter`

**infra** (`FROM localhost/molecule-base-common:latest`)

- `alertmanager`, `grafana`, `nginx`

### Scenario → image mapping

| Scenario | Image(s) |
|---|---|
| `provision/default` | raw `oraclelinux:10` |
| `repos/default` | raw `oraclelinux:10` |
| `preflight/default` | common |
| `node/default` | common |
| `ca/default` | common |
| `certs/default` | common |
| `cluster_ops/default` | data |
| `etcd/spof`, `etcd/ha` | data |
| `postgres/default` | data |
| `patroni/spof`, `patroni/ha` | data |
| `pgbouncer/default` | data |
| `haproxy/default`, `haproxy/ha` | data |
| `vip_manager/default` | data |
| `provision/ha` | data |
| `monitoring_agents/default` | data |
| `monitoring_server/default` | infra |
| `grafana/default` | infra |
| `nginx_proxy/default` | infra |
| `backup/default` | data (data host) + infra (backup_server host) |
| `backup/ha` | data ×3 + infra ×1 |

Multi-host scenarios set `image:` per platform, so `backup/*` mixes data and infra images in the same scenario.

## Source layout

```
tests/molecule/images/
├── common/Containerfile     # FROM container-registry.oracle.com/os/oraclelinux:10
├── data/Containerfile       # FROM localhost/molecule-base-common:latest
└── infra/Containerfile      # FROM localhost/molecule-base-common:latest
```

The existing `tests/molecule/Containerfile` is removed. The prior design at `docs/superpowers/specs/2026-05-16-molecule-local-image-design.md` is superseded by this one.

## Build & invocation

A new `Makefile.d/images.mk` (included from top-level `Makefile`) exposes:

- `make images-common` — build `localhost/molecule-base-common:latest`.
- `make images-data` — depends on `images-common`; builds `localhost/molecule-base-data:latest`.
- `make images-infra` — depends on `images-common`; builds `localhost/molecule-base-infra:latest`.
- `make images` — builds all three. `data` and `infra` may run in parallel once `common` completes (`make -j2 images-data images-infra`).
- `make images-clean` — removes the three local tags.
- `REBUILD=1` forces rebuild even if the tag exists.

Each target shells out to:

```
podman build \
  -f tests/molecule/images/<name>/Containerfile \
  -t localhost/molecule-base-<name>:latest \
  tests/molecule/images/<name>
```

CI (`.github/workflows/molecule.yml`) replaces its single-image build step with `make images`. The molecule matrix step is unchanged.

### CI parallelism

The raw-upstream scenarios (`provision/default`, `repos/default`) do not need any first-party image. To shorten the critical path, the workflow runs two tracks in parallel:

- **Track A — bootstrap scenarios.** As soon as `container-registry.oracle.com/os/oraclelinux:10` is pulled, dispatch `provision/default` and `repos/default`. These are the longest individual scenarios (full repo + node + product install from scratch) so starting them early matters most.
- **Track B — image build, then derived scenarios.** Run `make images-common`; on completion, fan out `make images-data` and `make images-infra` in parallel; as each image lands, dispatch the scenarios pinned to it.

Implementation options on GitHub Actions:

- A single job with `make -j` and a script that orchestrates podman builds and molecule runs.
- Separate jobs in the matrix with `needs:` edges expressing the dependency. `bootstrap` jobs depend only on the OS pull; `common-scenarios` depend on a `build-common` job; `data-scenarios` and `infra-scenarios` depend on `build-data` / `build-infra`, which depend on `build-common`.

The matrix-with-`needs` form is the cleaner of the two and matches the existing workflow shape. Concrete wiring is plan-level detail.

## What moves where

### Into the common image (out of `roles/repos` + `roles/node` hot path on baked-image scenarios)

- All repo enablement and priority work.
- `dnf-plugins-core` install.
- PG default module disable + marker.
- `pigsty` user/group.
- Pre-created sysctl and journald config directories.

These role tasks still run during `prepare.yml`/`converge.yml` on common/data/infra scenarios; they detect the baked state and become no-op `ok` tasks. They run for real on the two raw-upstream scenarios.

### Into the data and infra images

- Package installs only. No config, no users, no systemd units. Everything that depends on inventory, IP addresses, generated certs, or running systemd stays at converge time.

### Stays at converge time (cannot or should not be baked)

- `/etc/hosts` and hostname (inventory-dependent).
- Sysctl values (kernel namespace; meaningless at image build).
- Firewalld zone and service rules (requires running firewalld).
- CA and TLS cert material.
- VictoriaMetrics role-driven config and systemd units (binaries are baked; units are rendered).
- Any `archive_command`, Patroni DCS, replication-slot, or stanza work.

## Molecule prepare changes

For scenarios on common/data/infra:

- Drop the `Ensure iproute is present` bootstrap task (iproute is in the image).
- Keep the rest of `prepare.yml` unchanged. Roles run idempotently against the baked state.

For `provision/default` and `repos/default`:

- `prepare.yml` keeps the iproute bootstrap step (raw image has no iproute) and exercises the full `roles/repos` + `roles/node` install path. This is the regression signal we want.

## Smoke check on build

`make images` ends with a per-image smoke test:

- `podman run --rm <image> rpm -q <sentinel-package>` for each image, where the sentinel is a tier-defining package (e.g. `etcd` for data, `grafana` for infra, `pgbackrest` for common).
- For VM binaries on common: `podman run --rm <image> /usr/local/bin/vmsingle --version | head -1` and similar for the other four binaries.

Failures fail the make target. This catches missing packages and broken VM downloads before any molecule run.

## VM binary version resolution

Two different policies on the two sides:

- **Test images (this change).** Binary versions are resolved at `podman build` time, not pinned in the Containerfile. The build step issues a `HEAD` against `https://github.com/<repo>/releases/latest`, reads the `Location` redirect, extracts the tag (`v\d.*`), and downloads the matching tarball. This is the same logic the upstream `victoriametrics.cluster.*` roles use when their `*_version` var is set to `latest`. Two rebuilds days apart can pull different binary versions; the build-end smoke check (`<binary> -version`) catches the rare case where an upstream release breaks the tarball layout. CI caches the resulting image by Containerfile hash, so an existing cached image stays put until the Containerfile changes.

- **Production deploys.** The upstream `victoriametrics.cluster.*` role defaults already pin specific versions (currently `v1.143.0` for VictoriaMetrics and `v1.50.0` for VictoriaLogs). Operators who want to lock or upgrade those versions set the collection's own variables in inventory:

  ```yaml
  # group_vars/all/monitoring.yml (operator-edited)
  victoriametrics_version: v1.143.0   # or "latest"
  victorialogs_version: v1.50.0
  vmagent_version: v1.143.0
  vlagent_version: v1.50.0
  ```

  The pigsty-lite roles do not need to be modified to expose these — they pass through to the collection's role vars. We add a short note in `docs/` so operators know the knob exists.

## Risks

- **EPEL state mismatch.** Role terminal state is "installed but disabled." We mirror that in the image. If we ever forget to set `enabled=0` after `dnf install epel-release`, the repos role will flip it on first converge and break idempotence.
- **Non-reproducible image builds.** Pulling "latest" at build time means a rebuild on a different day can produce different binaries. Mitigation: the smoke check verifies each binary runs and reports a version; the CI image cache keys are content-hashes of the Containerfile, so once an image is cached it stays put until the Containerfile changes.
- **Hidden package coupling.** A role may pull in a package not listed above (e.g., a transitive dep via systemd unit). Mitigation: smoke check runs `rpm -q` only on sentinels; if a converge fails on a missing package, add it to the appropriate tier.
- **Multi-host image mixing in `backup/*`.** Per-platform `image:` works in molecule but is less common in this repo. Mitigation: explicit in molecule.yml, verified by running `backup/default` locally as part of the rollout.

## Testing strategy

After implementation:

1. `make images` builds clean from a cache-cleared state.
2. Run `molecule test -s default` for: `repos`, `provision/default`, `preflight`, `etcd/spof`, `monitoring_server`, `grafana`, `backup/default`. These cover all four image roles (raw upstream, common, data, infra, mixed).
3. Run the CI matrix as configured (`643a80f` trimmed it to five high-coverage scenarios).
4. Diff converge timings before/after on `etcd/spof` and `monitoring_server` as a sanity check on bake savings.

## Rollout

Single PR. All scenarios switch images in the same change so we don't carry a half-migrated tree. The old `tests/molecule/Containerfile` is removed in the same PR.
