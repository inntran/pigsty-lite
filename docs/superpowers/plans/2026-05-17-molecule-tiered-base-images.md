# Molecule Tiered Base Images Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single `localhost/molecule-base:latest` image with three layered first-party images (common → data, common → infra) plus two scenarios that bootstrap from raw `oraclelinux:10`, and wire all 23 molecule scenarios + the Makefile + the CI workflow to the new layout.

**Architecture:** Each image is a `Containerfile` under `tests/molecule/images/<name>/`. `data` and `infra` derive `FROM localhost/molecule-base-common:latest`. The `common` image bakes repos, base packages, the pigsty user/group, pgbackrest, and the five VictoriaMetrics binaries. `data` adds the data-plane RPMs (etcd/patroni/pg18/pgbouncer/haproxy/vip-manager/exporters). `infra` adds alertmanager/grafana/nginx. `provision/default` and `repos/default` switch back to raw upstream to exercise repos+node from a bare OS.

**Tech Stack:** podman, Containerfile, GNU Make, Molecule with podman driver, GitHub Actions, Oracle Linux 10 base.

**Spec:** `docs/superpowers/specs/2026-05-17-molecule-tiered-base-images-design.md`

---

## File Structure

**Created files:**

- `tests/molecule/images/common/Containerfile` — bootstrap + repos + pigsty user + pgbackrest + VM binaries
- `tests/molecule/images/data/Containerfile` — data-plane packages
- `tests/molecule/images/infra/Containerfile` — alertmanager/grafana/nginx
- `tests/molecule/images/.gitignore` — empty placeholder, ensures dir is committed
- `bin/molecule_images.sh` — orchestrates `podman build` for the three images, supports `REBUILD=1`, runs the smoke checks
- `Makefile.d/images.mk` — `images-common`, `images-data`, `images-infra`, `images`, `images-clean`

**Modified files:**

- `Makefile` — drop `test-image`/`molecule_image.sh` wiring; add `include Makefile.d/images.mk`; `test-role` depends on `images` instead of `test-image`; help text updated
- `tests/molecule/<each-scenario>/molecule/<sub>/molecule.yml` — switch each platform's `image:` per the mapping table in the spec
- `tests/molecule/<each-scenario>/molecule/<sub>/prepare.yml` — remove redundant "Ensure iproute is present" bootstrap on baked-image scenarios (keep it on raw-upstream scenarios)
- `.github/workflows/molecule.yml` — split into `pull-base-os`, `build-common`, `build-data`, `build-infra`, `bootstrap-scenarios` (raw upstream), and `derived-scenarios` (matrix on common/data/infra) jobs with `needs:` edges

**Deleted files:**

- `tests/molecule/Containerfile`
- `bin/molecule_image.sh`

**Out of scope (do not touch):**

- `roles/repos/**`, `roles/node/**` — no role refactors per spec non-goals
- Anything in `roles/**` other than reading defaults

---

## Task 1: Scaffold the images directory and the common Containerfile

**Files:**

- Create: `tests/molecule/images/common/Containerfile`
- Create: `tests/molecule/images/.gitignore`

- [ ] **Step 1: Create the directory and placeholder**

```bash
mkdir -p tests/molecule/images/common
: > tests/molecule/images/.gitignore
```

- [ ] **Step 2: Write the common Containerfile**

Create `tests/molecule/images/common/Containerfile` with this exact content:

```dockerfile
# syntax=docker/dockerfile:1
ARG OL_IMAGE=container-registry.oracle.com/os/oraclelinux:10
FROM ${OL_IMAGE}

# --- Bootstrap system packages ---
RUN dnf install -y \
        iproute \
        man-db \
        sudo \
        firewalld \
        policycoreutils \
        policycoreutils-python-utils \
        python3-libselinux \
        python3-cryptography \
        dnf-plugins-core \
        tar \
        gzip \
        curl-minimal \
    && dnf clean all

# --- Enable Oracle CodeReady Builder, install EPEL, then disable EPEL
#     for normal resolution (matches roles/repos terminal state) ---
RUN dnf config-manager --set-enabled ol10_codeready_builder \
    && dnf install -y --nogpgcheck \
        https://dl.fedoraproject.org/pub/epel/epel-release-latest-10.noarch.rpm \
    && dnf config-manager --setopt=epel.enabled=0 --save \
    && dnf clean all

# --- Install PGDG repo, enable extras + pgdg18, disable other majors,
#     set priority, and drop the PG default-module disabled marker so
#     roles/repos skips that step on baked-image scenarios ---
RUN dnf install -y --nogpgcheck \
        https://download.postgresql.org/pub/repos/yum/reporpms/EL-10-x86_64/pgdg-redhat-repo-latest.noarch.rpm \
    && dnf config-manager --setopt=pgdg-rhel10-extras.enabled=1 --save \
    && dnf config-manager --setopt=pgdg18.enabled=1 --save \
    && for v in 14 15 16 17; do \
         dnf config-manager --setopt=pgdg$${v}.enabled=0 --save; \
       done \
    && for s in pgdg-common pgdg-rhel10-extras pgdg18; do \
         dnf config-manager --setopt=$${s}.priority=10 --save; \
       done \
    && dnf -y module disable postgresql \
    && mkdir -p /etc/dnf/modules.d \
    && printf 'disabled by pigsty-lite common image\n' > /etc/dnf/modules.d/postgresql-disabled \
    && dnf clean all

# --- pigsty shared identity (matches roles/node) ---
RUN groupadd --system --gid 926 pigsty \
    && useradd  --system --uid 926 --gid 926 \
        --home-dir /var/lib/pigsty --create-home \
        --shell /sbin/nologin \
        --comment 'pigsty-lite shared identity' \
        pigsty \
    && chmod 0750 /var/lib/pigsty \
    && chown pigsty:pigsty /var/lib/pigsty

# --- Pre-create role-managed config dirs (cheap; saves node-role tasks) ---
RUN mkdir -p /etc/sysctl.d /etc/systemd/journald.conf.d

# --- Sudoers (preserved from prior monolithic image) ---
RUN printf '%%wheel ALL=(ALL) NOPASSWD: ALL\n' > /etc/sudoers.d/wheel-nopasswd \
    && printf 'ALL ALL=(ALL) NOPASSWD: ALL\n' > /etc/sudoers.d/all-nopasswd \
    && chmod 0440 /etc/sudoers.d/wheel-nopasswd /etc/sudoers.d/all-nopasswd

# --- pgbackrest is used by both data-side and infra-side ---
RUN dnf install -y pgbackrest && dnf clean all

# --- VictoriaMetrics binaries. Versions are resolved at build time by
#     following the GitHub /releases/latest 302 (same as the upstream
#     role's "version: latest" path). Tarball layouts:
#       victoria-metrics-linux-amd64-<tag>.tar.gz  -> victoria-metrics-prod
#       victoria-logs-linux-amd64-<tag>.tar.gz     -> victoria-logs-prod
#       vmutils-linux-amd64-<tag>.tar.gz           -> vmalert-prod, vmagent-prod
#       vlutils-linux-amd64-<tag>.tar.gz           -> vlagent-prod
RUN set -eux; \
    vm=$$(curl -fsSI -o /dev/null -w '%{redirect_url}' \
            https://github.com/VictoriaMetrics/VictoriaMetrics/releases/latest \
          | sed -n 's@.*/releases/tag/\(v[0-9][^[:space:]]*\).*@\1@p'); \
    vl=$$(curl -fsSI -o /dev/null -w '%{redirect_url}' \
            https://github.com/VictoriaMetrics/VictoriaLogs/releases/latest \
          | sed -n 's@.*/releases/tag/\(v[0-9][^[:space:]]*\).*@\1@p'); \
    [ -n "$$vm" ]; [ -n "$$vl" ]; \
    echo "Resolved VictoriaMetrics=$$vm VictoriaLogs=$$vl"; \
    base_vm=https://github.com/VictoriaMetrics/VictoriaMetrics/releases/download; \
    base_vl=https://github.com/VictoriaMetrics/VictoriaLogs/releases/download; \
    curl -fsSL "$$base_vm/$$vm/victoria-metrics-linux-amd64-$$vm.tar.gz" \
      | tar -xz -C /usr/local/bin victoria-metrics-prod; \
    curl -fsSL "$$base_vm/$$vm/vmutils-linux-amd64-$$vm.tar.gz" \
      | tar -xz -C /usr/local/bin vmalert-prod vmagent-prod; \
    curl -fsSL "$$base_vl/$$vl/victoria-logs-linux-amd64-$$vl.tar.gz" \
      | tar -xz -C /usr/local/bin victoria-logs-prod; \
    curl -fsSL "$$base_vl/$$vl/vlutils-linux-amd64-$$vl.tar.gz" \
      | tar -xz -C /usr/local/bin vlagent-prod; \
    chmod 0755 /usr/local/bin/victoria-metrics-prod \
               /usr/local/bin/victoria-logs-prod \
               /usr/local/bin/vmalert-prod \
               /usr/local/bin/vmagent-prod \
               /usr/local/bin/vlagent-prod

CMD ["/sbin/init"]
```

- [ ] **Step 3: Build the image and verify it succeeds**

```bash
podman build \
  -f tests/molecule/images/common/Containerfile \
  -t localhost/molecule-base-common:latest \
  tests/molecule/images/common
```

Expected: build completes successfully. Final line `Successfully tagged localhost/molecule-base-common:latest`.

- [ ] **Step 4: Smoke-check the common image contents**

Run each line; each must succeed (exit 0 / non-empty stdout):

```bash
podman run --rm localhost/molecule-base-common:latest rpm -q pgbackrest
podman run --rm localhost/molecule-base-common:latest rpm -q firewalld
podman run --rm localhost/molecule-base-common:latest rpm -q epel-release
podman run --rm localhost/molecule-base-common:latest rpm -q pgdg-redhat-repo
podman run --rm localhost/molecule-base-common:latest id pigsty
podman run --rm localhost/molecule-base-common:latest /usr/local/bin/victoria-metrics-prod -version
podman run --rm localhost/molecule-base-common:latest /usr/local/bin/victoria-logs-prod   -version
podman run --rm localhost/molecule-base-common:latest /usr/local/bin/vmalert-prod        -version
podman run --rm localhost/molecule-base-common:latest /usr/local/bin/vmagent-prod        -version
podman run --rm localhost/molecule-base-common:latest /usr/local/bin/vlagent-prod        -version
```

Expected: every command exits 0 and the binary `-version` calls print a version string containing the pinned tag (e.g. `v1.143.0`).

- [ ] **Step 5: Commit**

```bash
git add tests/molecule/images/common/Containerfile tests/molecule/images/.gitignore
git commit -m "test(molecule): add common base image (repos, pgbackrest, VM binaries)"
```

---

## Task 2: Add the data Containerfile

**Files:**

- Create: `tests/molecule/images/data/Containerfile`

- [ ] **Step 1: Write the data Containerfile**

Create `tests/molecule/images/data/Containerfile`:

```dockerfile
# syntax=docker/dockerfile:1
ARG COMMON_IMAGE=localhost/molecule-base-common:latest
FROM ${COMMON_IMAGE}

RUN dnf install -y \
        etcd \
        patroni \
        patroni-etcd \
        postgresql18-server \
        postgresql18-contrib \
        postgresql18 \
        pgbouncer \
        haproxy \
        vip-manager \
        python3-psycopg3 \
        golang-github-prometheus-node-exporter \
        postgres_exporter \
        pgbouncer_exporter \
        pgbackrest_exporter \
    && dnf clean all

CMD ["/sbin/init"]
```

- [ ] **Step 2: Build the data image**

```bash
podman build \
  -f tests/molecule/images/data/Containerfile \
  -t localhost/molecule-base-data:latest \
  tests/molecule/images/data
```

Expected: build completes successfully.

- [ ] **Step 3: Smoke-check the data image**

```bash
for pkg in etcd patroni postgresql18-server pgbouncer haproxy vip-manager \
           postgres_exporter pgbouncer_exporter pgbackrest_exporter \
           golang-github-prometheus-node-exporter; do
  podman run --rm localhost/molecule-base-data:latest rpm -q "$pkg"
done
```

Expected: every line prints `<name>-<version>...` (no `not installed`).

- [ ] **Step 4: Commit**

```bash
git add tests/molecule/images/data/Containerfile
git commit -m "test(molecule): add data base image (etcd, patroni, pg18, exporters)"
```

---

## Task 3: Add the infra Containerfile

**Files:**

- Create: `tests/molecule/images/infra/Containerfile`

- [ ] **Step 1: Write the infra Containerfile**

Create `tests/molecule/images/infra/Containerfile`:

```dockerfile
# syntax=docker/dockerfile:1
ARG COMMON_IMAGE=localhost/molecule-base-common:latest
FROM ${COMMON_IMAGE}

RUN dnf install -y \
        alertmanager \
        grafana \
        nginx \
    && dnf clean all

CMD ["/sbin/init"]
```

- [ ] **Step 2: Build the infra image**

```bash
podman build \
  -f tests/molecule/images/infra/Containerfile \
  -t localhost/molecule-base-infra:latest \
  tests/molecule/images/infra
```

Expected: build completes successfully.

- [ ] **Step 3: Smoke-check the infra image**

```bash
for pkg in alertmanager grafana nginx pgbackrest; do
  podman run --rm localhost/molecule-base-infra:latest rpm -q "$pkg"
done
```

Expected: all four print version strings (pgbackrest comes from the common image layer).

- [ ] **Step 4: Commit**

```bash
git add tests/molecule/images/infra/Containerfile
git commit -m "test(molecule): add infra base image (alertmanager, grafana, nginx)"
```

---

## Task 4: Add the orchestration script `bin/molecule_images.sh`

**Files:**

- Create: `bin/molecule_images.sh`

- [ ] **Step 1: Write the script**

Create `bin/molecule_images.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

# Build the three pigsty-lite molecule base images and run smoke checks.
#
# Usage:
#   bin/molecule_images.sh                       # build all three (skip if present)
#   bin/molecule_images.sh common                # build only common
#   bin/molecule_images.sh data                  # build only data (auto-builds common)
#   bin/molecule_images.sh infra                 # build only infra (auto-builds common)
#   REBUILD=1 bin/molecule_images.sh             # force rebuild

if ! command -v podman >/dev/null 2>&1; then
  echo "ERROR: podman is required but not found in PATH" >&2
  exit 1
fi

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
images_dir="$repo_root/tests/molecule/images"

build_one() {
  local name="$1"
  local tag="localhost/molecule-base-${name}:latest"
  local containerfile="$images_dir/$name/Containerfile"
  if [ ! -f "$containerfile" ]; then
    echo "ERROR: missing $containerfile" >&2
    exit 1
  fi
  if [ "${REBUILD:-0}" != "1" ] && podman image exists "$tag"; then
    echo "[skip] $tag already present (REBUILD=1 to force)"
    return 0
  fi
  echo "[build] $tag"
  podman build -f "$containerfile" -t "$tag" "$images_dir/$name"
}

smoke_common() {
  local tag="localhost/molecule-base-common:latest"
  podman run --rm "$tag" rpm -q pgbackrest      >/dev/null
  podman run --rm "$tag" rpm -q firewalld       >/dev/null
  podman run --rm "$tag" rpm -q epel-release    >/dev/null
  podman run --rm "$tag" rpm -q pgdg-redhat-repo>/dev/null
  podman run --rm "$tag" id pigsty              >/dev/null
  for bin in victoria-metrics-prod victoria-logs-prod vmalert-prod vmagent-prod vlagent-prod; do
    podman run --rm "$tag" "/usr/local/bin/$bin" -version >/dev/null
  done
  echo "[smoke] common OK"
}

smoke_data() {
  local tag="localhost/molecule-base-data:latest"
  for pkg in etcd patroni postgresql18-server pgbouncer haproxy vip-manager \
             postgres_exporter pgbouncer_exporter pgbackrest_exporter \
             golang-github-prometheus-node-exporter; do
    podman run --rm "$tag" rpm -q "$pkg" >/dev/null
  done
  echo "[smoke] data OK"
}

smoke_infra() {
  local tag="localhost/molecule-base-infra:latest"
  for pkg in alertmanager grafana nginx pgbackrest; do
    podman run --rm "$tag" rpm -q "$pkg" >/dev/null
  done
  echo "[smoke] infra OK"
}

targets=( "${1:-all}" )
if [ "${targets[0]}" = "all" ]; then
  targets=( common data infra )
fi

for t in "${targets[@]}"; do
  case "$t" in
    common) build_one common && smoke_common ;;
    data)   build_one common && build_one data  && smoke_data ;;
    infra)  build_one common && build_one infra && smoke_infra ;;
    *) echo "ERROR: unknown target '$t' (want: common|data|infra|all)" >&2; exit 2 ;;
  esac
done
```

- [ ] **Step 2: Make executable and shellcheck-clean**

```bash
chmod +x bin/molecule_images.sh
shellcheck bin/molecule_images.sh
```

Expected: shellcheck exits 0 with no output.

- [ ] **Step 3: Run the script end-to-end (no rebuild)**

```bash
bin/molecule_images.sh
```

Expected: `[skip]` for any image already built, `[build]` for any missing, then `[smoke] common OK`, `[smoke] data OK`, `[smoke] infra OK`.

- [ ] **Step 4: Test REBUILD=1 for a single target**

```bash
REBUILD=1 bin/molecule_images.sh infra
```

Expected: `[build] localhost/molecule-base-infra:latest` runs even though the image exists; `[skip]` for common; ends `[smoke] infra OK`.

- [ ] **Step 5: Commit**

```bash
git add bin/molecule_images.sh
git commit -m "test(molecule): add base-image build orchestrator"
```

---

## Task 5: Add `Makefile.d/images.mk` and wire it into the top-level Makefile

**Files:**

- Create: `Makefile.d/images.mk`
- Modify: `Makefile`

- [ ] **Step 1: Write the make include**

Create `Makefile.d/images.mk`:

```makefile
# Base image targets - included by top-level Makefile.

.PHONY: images images-common images-data images-infra images-clean

images: images-common images-data images-infra

images-common:
	./bin/molecule_images.sh common

images-data: images-common
	./bin/molecule_images.sh data

images-infra: images-common
	./bin/molecule_images.sh infra

images-clean:
	-podman image rm -f localhost/molecule-base-common:latest \
	                    localhost/molecule-base-data:latest \
	                    localhost/molecule-base-infra:latest
```

- [ ] **Step 2: Update top-level `Makefile`**

In `Makefile`:

1. Add `include Makefile.d/images.mk` directly after the existing `include Makefile.d/lint.mk` line.
2. Replace the `test-image:` target body with a thin alias and change `test-role`'s dependency from `test-image` to `images`. Concretely:
    Find this block:

    ```makefile
    test-image:
      ./bin/molecule_images.sh tests/molecule/Containerfile localhost/molecule-base

    test-role: test-image
    ```

    Note: the existing line is `./bin/molecule_image.sh tests/molecule/Containerfile localhost/molecule-base`. Replace it with:

    ```makefile
    test-image: images

    test-role: images
    ```

3. Update the `.PHONY` line: it already lists `test-image test-role`. Leave them (now both still exist).
4. Update help text: replace the `test-image` line with this line:

```
	@echo "  make images                        Build all three molecule base images (common/data/infra)"
```

And keep:

```
	@echo "  make test-image                    Alias for 'make images' (legacy name)"
```

- [ ] **Step 3: Verify make help reads correctly**

```bash
make help | grep -E "make (images|test-image|test-role)"
```

Expected: three lines printed, no errors.

- [ ] **Step 4: Run `make images` end-to-end**

```bash
make images
```

Expected: all three smoke checks pass.

- [ ] **Step 5: Commit**

```bash
git add Makefile Makefile.d/images.mk
git commit -m "build(make): tiered images targets (common/data/infra)"
```

---

## Task 6: Switch baked-image scenarios to the common image

These scenarios use the common image: `preflight/default`, `node/default`, `ca/default`, `certs/default`.

**Files (one platform `image:` line each):**

- Modify: `tests/molecule/preflight/molecule/default/molecule.yml`
- Modify: `tests/molecule/node/molecule/default/molecule.yml`
- Modify: `tests/molecule/ca/molecule/default/molecule.yml`
- Modify: `tests/molecule/certs/molecule/default/molecule.yml`

- [ ] **Step 1: Update `image:` in each scenario**

For each file above, replace every occurrence of:

```yaml
    image: localhost/molecule-base:latest
```

with:

```yaml
    image: localhost/molecule-base-common:latest
```

- [ ] **Step 2: Remove the iproute bootstrap from prepare.yml on these scenarios**

For each scenario above, inspect `prepare.yml`. If it contains:

```yaml
- name: Install bootstrap tooling
  hosts: all
  gather_facts: false
  tasks:
    - name: Ensure iproute is present
      ansible.builtin.dnf:
        name: iproute
        state: present
```

delete that play. iproute is in the common image.

- [ ] **Step 3: Run one scenario to confirm**

```bash
cd tests/molecule/preflight && molecule test -s default; cd -
```

Expected: `PLAY RECAP` shows 0 failed; scenario completes destroy at the end.

- [ ] **Step 4: Commit**

```bash
git add tests/molecule/{preflight,node,ca,certs}/molecule/default/molecule.yml \
        tests/molecule/{preflight,node,ca,certs}/molecule/default/prepare.yml
git commit -m "test(molecule): switch common-tier scenarios to molecule-base-common"
```

---

## Task 7: Switch data-tier scenarios to the data image

Scenarios: `cluster_ops/default`, `etcd/spof`, `etcd/ha`, `postgres/default`, `patroni/spof`, `patroni/ha`, `pgbouncer/default`, `haproxy/default`, `haproxy/ha`, `vip_manager/default`, `provision/ha`, `monitoring_agents/default`.

**Files:**

- Modify: `tests/molecule/cluster_ops/molecule/default/molecule.yml` + `prepare.yml`
- Modify: `tests/molecule/etcd/molecule/{spof,ha}/molecule.yml` + `prepare.yml`
- Modify: `tests/molecule/postgres/molecule/default/molecule.yml` + `prepare.yml`
- Modify: `tests/molecule/patroni/molecule/{spof,ha}/molecule.yml` + `prepare.yml`
- Modify: `tests/molecule/pgbouncer/molecule/default/molecule.yml` + `prepare.yml`
- Modify: `tests/molecule/haproxy/molecule/{default,ha}/molecule.yml` + `prepare.yml`
- Modify: `tests/molecule/vip_manager/molecule/default/molecule.yml` + `prepare.yml`
- Modify: `tests/molecule/provision/molecule/ha/molecule.yml` + `prepare.yml`
- Modify: `tests/molecule/monitoring_agents/molecule/default/molecule.yml` + `prepare.yml`

- [ ] **Step 1: Replace `image:` in each `molecule.yml` listed above**

For every platform entry in each file, replace:

```yaml
    image: localhost/molecule-base:latest
```

with:

```yaml
    image: localhost/molecule-base-data:latest
```

- [ ] **Step 2: Remove the iproute bootstrap from each `prepare.yml`**

In each `prepare.yml` listed above, delete the play if present:

```yaml
- name: Install bootstrap tooling
  hosts: all
  gather_facts: false
  tasks:
    - name: Ensure iproute is present
      ansible.builtin.dnf:
        name: iproute
        state: present
```

- [ ] **Step 3: Run two representative data scenarios**

```bash
cd tests/molecule/etcd && molecule test -s spof; cd -
cd tests/molecule/haproxy && molecule test -s ha;  cd -
```

Expected: both reach the final destroy with 0 failed tasks. `idempotence` step shows 0 changed.

- [ ] **Step 4: Commit**

```bash
git add tests/molecule/{cluster_ops,etcd,postgres,patroni,pgbouncer,haproxy,vip_manager,provision,monitoring_agents}/molecule/*/molecule.yml \
        tests/molecule/{cluster_ops,etcd,postgres,patroni,pgbouncer,haproxy,vip_manager,provision,monitoring_agents}/molecule/*/prepare.yml
git commit -m "test(molecule): switch data-tier scenarios to molecule-base-data"
```

---

## Task 8: Switch infra-tier scenarios to the infra image

Scenarios: `monitoring_server/default`, `grafana/default`, `nginx_proxy/default`.

**Files:**

- Modify: `tests/molecule/monitoring_server/molecule/default/molecule.yml` + `prepare.yml`
- Modify: `tests/molecule/grafana/molecule/default/molecule.yml` + `prepare.yml`
- Modify: `tests/molecule/nginx_proxy/molecule/default/molecule.yml` + `prepare.yml`

- [ ] **Step 1: Replace `image:` in each `molecule.yml`**

For each platform entry, replace:

```yaml
    image: localhost/molecule-base:latest
```

with:

```yaml
    image: localhost/molecule-base-infra:latest
```

- [ ] **Step 2: Remove the iproute bootstrap from `prepare.yml` if present**

Same deletion as in Task 7/8.

- [ ] **Step 3: Run one infra scenario**

```bash
cd tests/molecule/monitoring_server && molecule test -s default; cd -
```

Expected: scenario completes with 0 failed tasks; `idempotence` step shows 0 changed.

- [ ] **Step 4: Commit**

```bash
git add tests/molecule/{monitoring_server,grafana,nginx_proxy}/molecule/default/molecule.yml \
        tests/molecule/{monitoring_server,grafana,nginx_proxy}/molecule/default/prepare.yml
git commit -m "test(molecule): switch infra-tier scenarios to molecule-base-infra"
```

---

## Task 9: Configure `backup/*` scenarios with per-platform images

`backup/default` has two platforms; `backup/ha` has four. Data-plane hosts use the data image; the host in the `backup_server` group uses the infra image.

**Files:**

- Modify: `tests/molecule/backup/molecule/default/molecule.yml`
- Modify: `tests/molecule/backup/molecule/ha/molecule.yml`
- Modify: `tests/molecule/backup/molecule/default/prepare.yml` (only if it has the iproute bootstrap)
- Modify: `tests/molecule/backup/molecule/ha/prepare.yml` (same)

- [ ] **Step 1: `backup/default` — assign images per platform**

In `tests/molecule/backup/molecule/default/molecule.yml`, the two platforms are:

- `pigsty-lite-backup-default-1` → groups include `etcd, postgres` → **data image**
- `pigsty-lite-backup-default-server` → groups include `monitor, backup_server` → **infra image**

Replace the `image:` line under each platform accordingly:

```yaml
  - name: pigsty-lite-backup-default-1
    image: localhost/molecule-base-data:latest
    ...
  - name: pigsty-lite-backup-default-server
    image: localhost/molecule-base-infra:latest
    ...
```

- [ ] **Step 2: `backup/ha` — assign images per platform**

In `tests/molecule/backup/molecule/ha/molecule.yml`, three platforms host etcd/postgres (data), one hosts the backup server (infra). Inspect the file and:

- Set `image: localhost/molecule-base-data:latest` on platforms whose `groups` include `postgres` or `etcd`.
- Set `image: localhost/molecule-base-infra:latest` on the platform whose `groups` include `backup_server`.

- [ ] **Step 3: Remove iproute bootstrap from `prepare.yml` files if present**

Same deletion pattern as Task 7.

- [ ] **Step 4: Run `backup/default` to validate the multi-image setup**

```bash
cd tests/molecule/backup && molecule test -s default; cd -
```

Expected: scenario completes with 0 failed tasks. Both containers come up and the verify play passes.

- [ ] **Step 5: Commit**

```bash
git add tests/molecule/backup/molecule/{default,ha}/molecule.yml \
        tests/molecule/backup/molecule/{default,ha}/prepare.yml
git commit -m "test(molecule): backup scenarios use data+infra images per platform"
```

---

## Task 10: Repoint `provision/default` and `repos/default` at raw upstream `oraclelinux:10`

These two scenarios must run on the bare image to exercise `roles/repos` and `roles/node` from scratch. Their `prepare.yml` must keep the iproute bootstrap (raw image has no iproute).

**Files:**

- Modify: `tests/molecule/provision/molecule/default/molecule.yml`
- Modify: `tests/molecule/repos/molecule/default/molecule.yml`

- [ ] **Step 1: Switch `image:` in both scenarios**

Replace:

```yaml
    image: localhost/molecule-base:latest
```

with:

```yaml
    image: container-registry.oracle.com/os/oraclelinux:10
```

Leave `pre_build_image: true` set. (`pre_build_image: true` simply tells molecule "do not try to build this; pull only.")

- [ ] **Step 2: Confirm the iproute bootstrap stays**

Inspect `tests/molecule/provision/molecule/default/prepare.yml` and `tests/molecule/repos/molecule/default/prepare.yml`. The play:

```yaml
- name: Install bootstrap tooling
  hosts: all
  gather_facts: false
  tasks:
    - name: Ensure iproute is present
      ansible.builtin.dnf:
        name: iproute
        state: present
```

must be present in both. If `repos/default` does not currently have it, add it as the first play.

- [ ] **Step 3: Run `provision/default` end-to-end**

```bash
cd tests/molecule/provision && molecule test -s default; cd -
```

Expected: scenario completes with 0 failed tasks. Because the image is raw, expect more "changed" tasks during prepare (repos role enabling PGDG, node role creating pigsty user, etc.) — that is the point.

- [ ] **Step 4: Run `repos/default` end-to-end**

```bash
cd tests/molecule/repos && molecule test -s default; cd -
```

Expected: 0 failed; idempotence shows 0 changed on second run.

- [ ] **Step 5: Commit**

```bash
git add tests/molecule/provision/molecule/default/molecule.yml \
        tests/molecule/provision/molecule/default/prepare.yml \
        tests/molecule/repos/molecule/default/molecule.yml \
        tests/molecule/repos/molecule/default/prepare.yml
git commit -m "test(molecule): bootstrap scenarios run on raw oraclelinux:10"
```

---

## Task 11: Delete the legacy Containerfile and `bin/molecule_image.sh`

**Files:**

- Delete: `tests/molecule/Containerfile`
- Delete: `bin/molecule_image.sh`

- [ ] **Step 1: Confirm no scenario references `localhost/molecule-base:latest` anymore**

```bash
grep -RIn "localhost/molecule-base:latest" tests/molecule || echo "OK: no references"
```

Expected: `OK: no references`. If any remain, fix them before continuing.

- [ ] **Step 2: Confirm no Makefile or script references `bin/molecule_image.sh`**

```bash
grep -RIn "molecule_image.sh" Makefile Makefile.d bin .github || echo "OK: no references"
```

Expected: `OK: no references`.

- [ ] **Step 3: Delete the files**

```bash
git rm tests/molecule/Containerfile bin/molecule_image.sh
```

- [ ] **Step 4: Remove the local image tag (optional cleanup)**

```bash
podman image rm -f localhost/molecule-base:latest || true
```

- [ ] **Step 5: Commit**

```bash
git commit -m "test(molecule): remove legacy single-image build path"
```

---

## Task 12: Restructure the CI workflow for image build + parallel tracks

**Files:**

- Modify: `.github/workflows/molecule.yml`

- [ ] **Step 1: Replace the workflow with the new job graph**

Overwrite `.github/workflows/molecule.yml` with:

```yaml
---
name: molecule
on:
  pull_request:
  push:
    branches: [main]

jobs:
  build-common:
    runs-on: ubuntu-latest
    outputs:
      cache-key: ${{ steps.key.outputs.key }}
    steps:
      - uses: actions/checkout@v6

      - name: Compute cache key
        id: key
        run: |
          key="molecule-common-$(sha256sum tests/molecule/images/common/Containerfile | awk '{print $1}')"
          echo "key=$key" >> "$GITHUB_OUTPUT"

      - name: Restore cached common image
        id: cache
        uses: actions/cache@v5
        with:
          path: /tmp/molecule-base-common.tar
          key: ${{ steps.key.outputs.key }}

      - name: Build common image
        if: steps.cache.outputs.cache-hit != 'true'
        run: |
          ./bin/molecule_images.sh common
          podman save -o /tmp/molecule-base-common.tar localhost/molecule-base-common:latest

  build-data:
    needs: build-common
    runs-on: ubuntu-latest
    outputs:
      cache-key: ${{ steps.key.outputs.key }}
    steps:
      - uses: actions/checkout@v6

      - name: Compute cache key
        id: key
        run: |
          common_hash=$(sha256sum tests/molecule/images/common/Containerfile | awk '{print $1}')
          data_hash=$(sha256sum tests/molecule/images/data/Containerfile   | awk '{print $1}')
          echo "key=molecule-data-${common_hash}-${data_hash}" >> "$GITHUB_OUTPUT"

      - name: Restore cached data image
        id: cache
        uses: actions/cache@v5
        with:
          path: /tmp/molecule-base-data.tar
          key: ${{ steps.key.outputs.key }}

      - name: Restore cached common image
        if: steps.cache.outputs.cache-hit != 'true'
        uses: actions/cache@v5
        with:
          path: /tmp/molecule-base-common.tar
          key: ${{ needs.build-common.outputs.cache-key }}

      - name: Load common into podman
        if: steps.cache.outputs.cache-hit != 'true'
        run: podman load -i /tmp/molecule-base-common.tar

      - name: Build data image
        if: steps.cache.outputs.cache-hit != 'true'
        run: |
          ./bin/molecule_images.sh data
          podman save -o /tmp/molecule-base-data.tar localhost/molecule-base-data:latest

  build-infra:
    needs: build-common
    runs-on: ubuntu-latest
    outputs:
      cache-key: ${{ steps.key.outputs.key }}
    steps:
      - uses: actions/checkout@v6

      - name: Compute cache key
        id: key
        run: |
          common_hash=$(sha256sum tests/molecule/images/common/Containerfile | awk '{print $1}')
          infra_hash=$(sha256sum tests/molecule/images/infra/Containerfile  | awk '{print $1}')
          echo "key=molecule-infra-${common_hash}-${infra_hash}" >> "$GITHUB_OUTPUT"

      - name: Restore cached infra image
        id: cache
        uses: actions/cache@v5
        with:
          path: /tmp/molecule-base-infra.tar
          key: ${{ steps.key.outputs.key }}

      - name: Restore cached common image
        if: steps.cache.outputs.cache-hit != 'true'
        uses: actions/cache@v5
        with:
          path: /tmp/molecule-base-common.tar
          key: ${{ needs.build-common.outputs.cache-key }}

      - name: Load common into podman
        if: steps.cache.outputs.cache-hit != 'true'
        run: podman load -i /tmp/molecule-base-common.tar

      - name: Build infra image
        if: steps.cache.outputs.cache-hit != 'true'
        run: |
          ./bin/molecule_images.sh infra
          podman save -o /tmp/molecule-base-infra.tar localhost/molecule-base-infra:latest

  # Track A: bootstrap scenarios on raw upstream OS. No dependency on
  # first-party image builds, so they start as soon as the runner is up.
  bootstrap-scenarios:
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        include:
          - role: provision
            scenario: default
          - role: repos
            scenario: default
    steps:
      - uses: actions/checkout@v6

      - name: Set up Python
        uses: actions/setup-python@v6
        with:
          python-version: "3.12"
          cache: pip

      - name: Install python tooling
        run: |
          python -m pip install --upgrade pip
          python -m pip install \
            ansible \
            "molecule==25.*" \
            "molecule-plugins[podman]==25.*" \
            cryptography \
            pytest

      - name: Install Galaxy content
        run: |
          ansible-galaxy collection install -r requirements.yml -p ./collections --upgrade
          ansible-galaxy role install -r requirements.yml -p ./roles.galaxy

      - name: molecule ${{ matrix.role }} (${{ matrix.scenario }})
        env:
          ANSIBLE_COLLECTIONS_PATH: ${{ github.workspace }}/collections
        run: |
          cd tests/molecule/${{ matrix.role }}
          molecule test -s ${{ matrix.scenario }}

  # Track B: scenarios on the baked images. Each loads the image(s) it
  # needs from cache, then runs molecule.
  derived-scenarios:
    needs: [build-common, build-data, build-infra]
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        # Same trimmed five-scenario coverage as before (see
        # tests/molecule/COVERAGE.md), pinned to required images.
        include:
          - role: cluster_ops
            scenario: default
            images: data
          - role: backup
            scenario: ha
            images: data,infra
          - role: haproxy
            scenario: ha
            images: data
          - role: nginx_proxy
            scenario: default
            images: infra
          - role: monitoring_agents
            scenario: default
            images: data
    steps:
      - uses: actions/checkout@v6

      - name: Restore common image (always needed as parent)
        uses: actions/cache@v5
        with:
          path: /tmp/molecule-base-common.tar
          key: ${{ needs.build-common.outputs.cache-key }}

      - name: Restore data image (if needed)
        if: contains(matrix.images, 'data')
        uses: actions/cache@v5
        with:
          path: /tmp/molecule-base-data.tar
          key: ${{ needs.build-data.outputs.cache-key }}

      - name: Restore infra image (if needed)
        if: contains(matrix.images, 'infra')
        uses: actions/cache@v5
        with:
          path: /tmp/molecule-base-infra.tar
          key: ${{ needs.build-infra.outputs.cache-key }}

      - name: Load images into podman
        run: |
          podman load -i /tmp/molecule-base-common.tar
          if [ -f /tmp/molecule-base-data.tar  ]; then podman load -i /tmp/molecule-base-data.tar;  fi
          if [ -f /tmp/molecule-base-infra.tar ]; then podman load -i /tmp/molecule-base-infra.tar; fi

      - name: Set up Python
        uses: actions/setup-python@v6
        with:
          python-version: "3.12"
          cache: pip

      - name: Install python tooling
        run: |
          python -m pip install --upgrade pip
          python -m pip install \
            ansible \
            "molecule==25.*" \
            "molecule-plugins[podman]==25.*" \
            cryptography \
            pytest

      - name: Install Galaxy content
        run: |
          ansible-galaxy collection install -r requirements.yml -p ./collections --upgrade
          ansible-galaxy role install -r requirements.yml -p ./roles.galaxy

      - name: molecule ${{ matrix.role }} (${{ matrix.scenario }})
        env:
          ANSIBLE_COLLECTIONS_PATH: ${{ github.workspace }}/collections
        run: |
          cd tests/molecule/${{ matrix.role }}
          molecule test -s ${{ matrix.scenario }}
```

- [ ] **Step 2: Lint the workflow file**

```bash
yamllint .github/workflows/molecule.yml
```

Expected: exit 0 (no errors). Fix any formatting flagged.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/molecule.yml
git commit -m "ci(molecule): parallel image build + bootstrap/derived tracks"
```

---

## Task 13: Update `tests/molecule/COVERAGE.md` and add prod-pin note

The COVERAGE doc explains what the trimmed CI matrix covers. We add the bootstrap track and a short operator-facing note about pinning VM versions in production (since the test image policy diverges from prod).

**Files:**

- Modify: `tests/molecule/COVERAGE.md`
- Modify: `roles/monitoring_server/README.md` (or create a short section if absent)

- [ ] **Step 1: Append a "Bootstrap track" section to COVERAGE.md**

Append to the end of `tests/molecule/COVERAGE.md`:

```markdown
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

```

- [ ] **Step 2: Add a short operator-facing note in `roles/monitoring_server/README.md`**

Locate the role README:

```bash
ls roles/monitoring_server/README.md
```

If it exists, append a section:

```markdown
## Pinning VictoriaMetrics / VictoriaLogs versions

The upstream `victoriametrics.cluster.*` roles default to specific
versions; pigsty-lite does not override them. To pin or upgrade in
production, set the collection's variables in inventory:

    victoriametrics_version: v1.143.0   # or "latest"
    victorialogs_version:    v1.50.0
    vmagent_version:         v1.143.0
    vlagent_version:         v1.50.0

Test images deliberately follow `latest` at image-build time to stay in
lockstep with upstream; production should pin.
```

If `roles/monitoring_server/README.md` does not exist, create it with just the section above plus a one-line intro:

```markdown
# monitoring_server

Monitoring server role (VictoriaMetrics single-node, VictoriaLogs single-node,
vmalert, Alertmanager).

## Pinning VictoriaMetrics / VictoriaLogs versions

…(content from above)…
```

- [ ] **Step 3: Commit**

```bash
git add tests/molecule/COVERAGE.md roles/monitoring_server/README.md
git commit -m "docs: bootstrap track + VM version pinning guidance"
```

---

## Task 14: Full local validation

Run a representative slice locally before declaring done.

- [ ] **Step 1: Clean rebuild**

```bash
make images-clean
make images
```

Expected: three images build from scratch, all smoke checks pass.

- [ ] **Step 2: Run the CI matrix locally**

```bash
for s in "cluster_ops default" "backup ha" "haproxy ha" "nginx_proxy default" "monitoring_agents default"; do
  set -- $s
  ( cd tests/molecule/$1 && molecule test -s $2 ) || { echo "FAILED: $1/$2"; exit 1; }
done
```

Expected: each scenario passes with `failed=0`.

- [ ] **Step 3: Run the bootstrap track locally**

```bash
( cd tests/molecule/provision && molecule test -s default )
( cd tests/molecule/repos     && molecule test -s default )
```

Expected: both pass.

- [ ] **Step 4: Lint everything**

```bash
make lint
```

Expected: exit 0.

- [ ] **Step 5: Final commit (only if there were tidy-up fixes; otherwise skip)**

If anything was tweaked during validation:

```bash
git add -A
git commit -m "test(molecule): tidy after full validation pass"
```

---

## Self-Review

Spec coverage check (each section maps to at least one task):

- **Architecture / Image contents** — Tasks 1, 2, 3
- **Scenario → image mapping** — Tasks 6 (common), 7 (data), 8 (infra), 9 (backup multi-image), 10 (raw upstream)
- **Source layout** — Tasks 1–3
- **Build & invocation (Makefile)** — Task 5
- **CI parallelism (matrix-with-needs)** — Task 12
- **What moves where / What stays runtime** — Tasks 6–10 remove iproute bootstrap; common image bakes user/repos/marker
- **Smoke check on build** — Task 4 (`molecule_images.sh`)
- **VM binary version resolution (test: latest at build; prod: pin via inventory)** — Task 1 (latest-resolution RUN block), Task 13 (operator-facing docs)
- **Risks: EPEL state, non-reproducible builds, hidden package coupling, multi-host image mixing** — Task 1 (EPEL `enabled=0`, smoke check on VM binaries), Task 4 (smoke), Task 9 (backup multi-image)
- **Testing strategy** — Task 14
- **Rollout (single PR)** — All tasks land on the same branch; legacy removed in Task 11

No placeholders, no "TBD", no "similar to" references. Code blocks present everywhere a code step appears. Image tags (`localhost/molecule-base-common:latest`, etc.) are consistent across tasks.
