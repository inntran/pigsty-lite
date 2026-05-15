# P7 Libvirt Integration Tests Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up an end-to-end, libvirt-based integration test harness that boots a 4-VM HA cluster from an existing Rocky Linux qcow2 image on local disk, runs `make deploy`, and exercises the spec §13.3 acceptance checks — locally and opt-in only, never in CI.

**Architecture:** Plain bash scripts drive the entire lifecycle. `qemu-img create -b <base> -F qcow2 -f qcow2` creates a thin-clone OS overlay per VM from a user-supplied base image (base never modified). `qemu-img create -f raw` creates a sparse raw data disk per VM (pgdata for postgres nodes, backup store for the monitor node). Per-VM cloud-init `user-data` and `meta-data` files are generated from templates and injected into each OS overlay at `/var/lib/cloud/seed/nocloud/` via `virt-customize`, which also runs an SELinux relabel. `virt-install --import` boots each domain with both disks attached. On first boot cloud-init sets the hostname, injects the SSH key, formats the data disk as XFS, and mounts it at the appropriate path — no manual partition poking from outside. A wait loop polls SSH; once all VMs are reachable the harness runs `./configure -s -f` and `ansible-playbook playbooks/site.yml`. A verify playbook checks the spec §13.3 acceptance suite.

**Tech Stack:** libvirt/QEMU/KVM, libguestfs (`virt-customize` for cloud-init injection + SELinux relabel, `guestfish` for partition inspection in smoke-test), qemu-img, virt-install, virsh, existing Rocky Linux 10 GenericCloud qcow2 on local disk (user-supplied), cloud-init (pre-installed in GenericCloud image), Ansible (control-node only), existing pigsty-lite playbooks unchanged, psql, etcdctl, pgbackrest for the verify phase.

**Out of scope:** Chaos suite (P7c), GitHub CI execution, performance/RTO measurement, external-CIDR probe (P7d), all secondary scenarios (split-infra, byo-tls, minor-upgrade-rolling).

---

## Pre-execution prerequisites (read before starting)

1. **libvirt, QEMU/KVM, libguestfs** must be installed on the control node: `libvirt-daemon-kvm`, `qemu-kvm`, `libguestfs-tools` (provides `virt-customize` and `guestfish`), `virt-install`. On Fedora/RHEL: `dnf install -y libvirt-daemon-kvm qemu-kvm libguestfs-tools virt-install`. The executing user must be in the `libvirt` group or be root.
2. **Base image.** The user must supply a GenericCloud/KVM qcow2 already on local disk. Supported distros:
   - **Rocky Linux 10** — GenericCloud image, cloud-init pre-installed, no subscription needed. `PIGSTY_OS_VARIANT=rocky10`.
   - **Oracle Linux 10** — KVM image, cloud-init pre-installed, no subscription needed. `PIGSTY_OS_VARIANT=ol10`.
   - **RHEL 10** — KVM Guest Image, cloud-init pre-installed, requires an active subscription. `virt-customize --run-command 'subscription-manager register --username … --password … --auto-attach'` is injected before the cloud-init files. `PIGSTY_OS_VARIANT=rhel10` and `PIGSTY_RHSM_USER` / `PIGSTY_RHSM_PASSWORD` must be set.
   Path is read from `PIGSTY_BASE_IMAGE` (no default; preflight errors if unset).
3. **SELinux.** On SELinux-enforcing hosts, `virt-customize` and `guestfish` require `LIBGUESTFS_BACKEND=direct`. Exported in `bin/integration/lib/common.sh`.
4. **RAM/disk.** 4 VMs × 2 GB RAM = 8 GB needed. OS overlays are thin-provisioned qcow2; data disks are sparse raw files. Base image is never modified.
5. **Network.** The harness creates a dedicated libvirt NAT network `pigsty-lite-test` (`10.77.77.0/24`). IP↔MAC DHCP reservations guarantee static IPs; cloud-init writes the hostname, so no guest-side static network config is needed.

---

## IP / MAC / hostname topology (HA scenario)

| hostname  | role        | IP            | MAC               | OS disk (qcow2 overlay) | Data disk (raw sparse)          | Mount point              |
|-----------|-------------|---------------|-------------------|-------------------------|---------------------------------|--------------------------|
| pgmon01   | monitor     | 10.77.77.10   | 52:54:00:77:77:10 | 20 GB thin              | 50 GB (`pgmon01-backup.raw`)    | `/var/lib/pgbackrest`    |
| pgnode01  | pg_primary  | 10.77.77.11   | 52:54:00:77:77:11 | 20 GB thin              | 50 GB (`pgnode01-pgdata.raw`)   | `/var/lib/pgsql`         |
| pgnode02  | pg_replica  | 10.77.77.12   | 52:54:00:77:77:12 | 20 GB thin              | 50 GB (`pgnode02-pgdata.raw`)   | `/var/lib/pgsql`         |
| pgnode03  | pg_replica  | 10.77.77.13   | 52:54:00:77:77:13 | 20 GB thin              | 50 GB (`pgnode03-pgdata.raw`)   | `/var/lib/pgsql`         |

All data disks appear as `/dev/vdb` inside the VM. Cloud-init formats them as XFS and writes the fstab entry on first boot.

---

## File Structure

**New files:**

- `tests/integration/README.md` — operator runbook: prerequisites, run/inspect/destroy, troubleshooting guestfish under SELinux, expected runtimes.
- `tests/integration/ha/topology.env` — single source of truth: hostnames, IPs, MACs, disk sizes. Sourced by every shell script.
- `tests/integration/ha/network.xml` — libvirt NAT network definition (CIDR + DHCP + MAC→IP reservations).
- `tests/integration/ha/responses/ha.rsp.yml` — static response file for the HA integration scenario (feeds `./configure -s -f`).
- `tests/integration/ha/verify.yml` — Ansible verify playbook: imports per-check task files.
- `tests/integration/ha/verify/connectivity.yml`
- `tests/integration/ha/verify/patroni_state.yml`
- `tests/integration/ha/verify/etcd_quorum.yml`
- `tests/integration/ha/verify/backups.yml`
- `tests/integration/ha/verify/selinux.yml`
- `tests/integration/ha/verify/idempotency.yml`
- `bin/integration/lib/common.sh` — env defaults (`PIGSTY_BASE_IMAGE`, `PIGSTY_NETWORK`, etc.), logging helpers, `LIBGUESTFS_BACKEND=direct`.
- `bin/integration/lib/network.sh` — `network_define_if_missing`, `network_destroy`.
- `bin/integration/lib/image.sh` — `make_overlay`, `make_data_disk`, `write_cloud_init`, `inject_cloud_init`.
- `bin/integration/lib/vm.sh` — `vm_install`, `vm_wait_ssh`, `vm_destroy`.
- `tests/integration/ha/cloud-init/user-data.pgnode` — bash-rendered template for postgres node user-data (formats `/dev/vdb` → XFS → `/var/lib/pgsql`).
- `tests/integration/ha/cloud-init/user-data.pgmon` — bash-rendered template for monitor node user-data (formats `/dev/vdb` → XFS → `/var/lib/pgbackrest`).
- `tests/integration/ha/cloud-init/generated/` — **gitignored** directory where `write_cloud_init` writes per-VM `user-data` and `meta-data` files. Survives between runs for post-failure inspection.
- `bin/integration-preflight.sh` — workstation sanity check (required binaries, KVM accessible, `PIGSTY_BASE_IMAGE` set and readable, free RAM, free disk).
- `bin/integration-up.sh` — orchestrator: network → per-VM OS overlay + data disk + cloud-init injection → virt-install × 4 → wait SSH → `./configure -s -f`.
- `bin/integration-down.sh` — destroy + undefine domains, delete overlays, optionally destroy network.
- `bin/integration-converge.sh` — `ansible-playbook playbooks/site.yml` against the integration inventory.
- `bin/integration-verify.sh` — `ansible-playbook tests/integration/ha/verify.yml`.

**Modified files:**

- `Makefile` — add `test-integration`, `test-integration-up`, `test-integration-down`, `test-integration-converge`, `test-integration-verify` targets.
- `.gitignore` — add `tests/integration/**/.run/` (per-run rendered artifacts) and `tests/integration/**/overlays/` (qcow2 overlays).

**Explicitly NOT modified:**

- Any role under `roles/` — if a role bug surfaces, stop and file it separately.
- `playbooks/site.yml` — consumed unchanged.
- `.github/workflows/` — this scenario is local-only.

---

## Task 1: Write the README and topology.env

**Why first:** Locks in the operator contract and the IP/MAC/hostname constants before any code reads or writes them.

**Files:**
- Create: `tests/integration/README.md`
- Create: `tests/integration/ha/topology.env`

- [ ] **Step 1: Create `tests/integration/ha/topology.env`**

```bash
# tests/integration/ha/topology.env
# Single source of truth for the HA integration scenario.
# Source this file from every integration script.

PIGSTY_NETWORK_NAME="pigsty-lite-test"
PIGSTY_NETWORK_CIDR="10.77.77.0/24"
# Disk images must live under /var/lib/libvirt/images so the qemu user
# can read them. Creating files there requires sudo.
PIGSTY_IMAGE_DIR="${PIGSTY_IMAGE_DIR:-/var/lib/libvirt/images/pigsty-lite}"

# hostname:ip:mac:role tuples — iterate with the VMS array
# role is either "pgnode" (postgres data disk) or "pgmon" (backup store disk)
declare -a VMS=(
  "pgmon01:10.77.77.10:52:54:00:77:77:10:pgmon"
  "pgnode01:10.77.77.11:52:54:00:77:77:11:pgnode"
  "pgnode02:10.77.77.12:52:54:00:77:77:12:pgnode"
  "pgnode03:10.77.77.13:52:54:00:77:77:13:pgnode"
)

VM_RAM_MB=2048
VM_VCPUS=2
VM_OS_DISK_GB=20    # qcow2 overlay thin-clone ceiling
VM_DATA_DISK_GB=50  # raw sparse data disk per VM

# Mount points by role (must match cloud-init templates)
PGNODE_DATA_MOUNT="/var/lib/pgsql"
PGMON_DATA_MOUNT="/var/lib/pgbackrest"

PIGSTY_SSH_KEY="${PIGSTY_SSH_KEY:-${HOME}/.ssh/id_ed25519.pub}"

# OS variant passed to virt-install --os-variant and used to select
# the subscription injection path.  rocky10 | ol10 | rhel10
PIGSTY_OS_VARIANT="${PIGSTY_OS_VARIANT:-rocky10}"

# RHEL only: subscription-manager credentials.
# Leave unset for Rocky Linux / Oracle Linux.
# PIGSTY_RHSM_USER=""
# PIGSTY_RHSM_PASSWORD=""
```

- [ ] **Step 2: Create `tests/integration/README.md`**

```markdown
# Integration Tests

End-to-end libvirt-based integration test for the pigsty-lite HA profile.
Boots 4 KVM VMs, runs `make deploy`, exercises acceptance checks.

## Prerequisites

```bash
# Fedora / RHEL:
sudo dnf install -y libvirt-daemon-kvm qemu-kvm libguestfs-tools virt-install
sudo usermod -aG libvirt $USER   # log out and back in
sudo systemctl enable --now libvirtd
```

## Required environment variables

| Variable | Description |
|---|---|
| `PIGSTY_BASE_IMAGE` | Absolute path to a GenericCloud/KVM qcow2 (Rocky 10, Oracle Linux 10, or RHEL 10) |
| `PIGSTY_OS_VARIANT` | `rocky10` (default), `ol10`, or `rhel10` — passed to `virt-install --os-variant` |
| `PIGSTY_RHSM_USER` | RHEL only: Red Hat subscription username |
| `PIGSTY_RHSM_PASSWORD` | RHEL only: Red Hat subscription password |

## Run

```bash
export PIGSTY_BASE_IMAGE=/path/to/Rocky-10-GenericCloud.latest.x86_64.qcow2
make test-integration
```

Full run (fresh VMs + deploy + verify) takes ~25-35 minutes on a workstation with 8+ GB free RAM.

## Step-by-step (debug mode)

```bash
make test-integration-up        # create VMs, configure
make test-integration-converge  # ansible-playbook site.yml
make test-integration-verify    # run acceptance checks
make test-integration-down      # destroy VMs and overlays
```

## Inspect a failed run

```bash
virsh list --all                    # see domain state
ssh -i ~/.ssh/id_ed25519 root@10.77.77.11   # ssh into pgnode01
virsh console pgmon01               # console if SSH is not up yet
```

## Destroy without network removal

```bash
KEEP_NETWORK=1 make test-integration-down
```

## Troubleshooting

**virt-customize / guestfish: supermin appliance not found / permission denied**
Set `LIBGUESTFS_BACKEND=direct` (already set by `lib/common.sh`). On Fedora Silverblue or
snap-confined libvirt, run the integration scripts as root.

**virt-install: could not open disk image**
Images under `/var/lib/libvirt/images/` are owned by root and readable by the `qemu` user
by default — this is why the harness writes there. Ensure `$PIGSTY_BASE_IMAGE` is also
readable by the `qemu` user: `sudo setfacl -m u:qemu:r /path/to/base.qcow2`

**sudo: a password is required**
The harness uses `sudo` for all disk operations. Add a NOPASSWD entry for the relevant
commands or run the integration scripts as root directly.

**VM does not get expected IP**
The libvirt NAT network uses DHCP reservations keyed on MAC. Verify the domain XML with
`virsh dumpxml pgmon01 | grep mac`. The MAC must match `topology.env`.
```

- [ ] **Step 3: Commit**

```bash
git add tests/integration/README.md tests/integration/ha/topology.env
git commit -m "feat(integration): add ha topology.env and operator README"
```

---

## Task 2: Write `bin/integration/lib/common.sh`

**Files:**
- Create: `bin/integration/lib/common.sh`

- [ ] **Step 1: Create `bin/integration/lib/common.sh`**

```bash
#!/usr/bin/env bash
# bin/integration/lib/common.sh
# Shared env defaults and logging helpers. Source this file first.
set -euo pipefail

# Required: path to existing GenericCloud qcow2 on local disk.
: "${PIGSTY_BASE_IMAGE:?PIGSTY_BASE_IMAGE must be set to the path of the base qcow2 image}"

# guestfish/virt-customize backend: direct avoids supermin appliance
# permission issues under SELinux-enforcing hosts.
export LIBGUESTFS_BACKEND=direct

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

# Source topology constants
# shellcheck source=tests/integration/ha/topology.env
source "${PROJECT_ROOT}/tests/integration/ha/topology.env"

log()  { echo "[$(date '+%H:%M:%S')] $*" >&2; }
info() { log "INFO  $*"; }
warn() { log "WARN  $*"; }
die()  { log "ERROR $*"; exit 1; }

# Disk images live in /var/lib/libvirt/images which requires root access.
# SUDO is "sudo" when the caller is not already root, "" when running as root.
if [[ "${EUID}" -eq 0 ]]; then
  SUDO=""
else
  SUDO="sudo"
fi

# Parse a VMS entry "hostname:ip:mac1:mac2:mac3:role" into named vars.
# Usage: parse_vm_entry "pgnode01:10.77.77.11:52:54:00:77:77:11:pgnode"
#   sets: VM_HOSTNAME  VM_IP  VM_MAC  VM_ROLE
parse_vm_entry() {
  local entry="$1"
  VM_HOSTNAME="${entry%%:*}"
  local rest="${entry#*:}"
  VM_IP="${rest%%:*}"
  rest="${rest#*:}"
  # MAC is the next 5 colon-separated octets; role is the final field
  VM_ROLE="${rest##*:}"
  VM_MAC="${rest%:*}"
}
```

- [ ] **Step 2: Make executable**

```bash
chmod +x bin/integration/lib/common.sh
```

- [ ] **Step 3: Commit**

```bash
git add bin/integration/lib/common.sh
git commit -m "feat(integration): add lib/common.sh with env defaults and logging helpers"
```

---

## Task 3: Write `bin/integration/lib/network.sh` and `tests/integration/ha/network.xml`

**Files:**
- Create: `tests/integration/ha/network.xml`
- Create: `bin/integration/lib/network.sh`

- [ ] **Step 1: Create `tests/integration/ha/network.xml`**

```xml
<!--
  tests/integration/ha/network.xml
  Libvirt NAT network for the HA integration scenario.
  DHCP reservations fix IPs to MACs so VMs get static IPs without
  guest-side static configuration.
-->
<network>
  <name>pigsty-lite-test</name>
  <forward mode="nat"/>
  <bridge name="virbr77" stp="on" delay="0"/>
  <mac address="52:54:00:77:77:00"/>
  <ip address="10.77.77.1" netmask="255.255.255.0">
    <dhcp>
      <range start="10.77.77.2" end="10.77.77.9"/>
      <host mac="52:54:00:77:77:10" name="pgmon01"  ip="10.77.77.10"/>
      <host mac="52:54:00:77:77:11" name="pgnode01" ip="10.77.77.11"/>
      <host mac="52:54:00:77:77:12" name="pgnode02" ip="10.77.77.12"/>
      <host mac="52:54:00:77:77:13" name="pgnode03" ip="10.77.77.13"/>
    </dhcp>
  </ip>
</network>
```

- [ ] **Step 2: Create `bin/integration/lib/network.sh`**

```bash
#!/usr/bin/env bash
# bin/integration/lib/network.sh
# network_define_if_missing  — idempotent network create
# network_destroy            — tear down the libvirt network

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
NETWORK_XML="${PROJECT_ROOT}/tests/integration/ha/network.xml"

network_define_if_missing() {
  local name="${PIGSTY_NETWORK_NAME}"
  if virsh net-info "${name}" &>/dev/null; then
    info "Network ${name} already exists — skipping define"
    return
  fi
  info "Defining libvirt network ${name}"
  virsh net-define "${NETWORK_XML}"
  virsh net-autostart "${name}"
  virsh net-start "${name}"
}

network_destroy() {
  local name="${PIGSTY_NETWORK_NAME}"
  if ! virsh net-info "${name}" &>/dev/null; then
    info "Network ${name} not found — nothing to destroy"
    return
  fi
  virsh net-destroy "${name}" 2>/dev/null || true
  virsh net-undefine "${name}"
  info "Network ${name} destroyed"
}
```

- [ ] **Step 3: Make executable and commit**

```bash
chmod +x bin/integration/lib/network.sh
git add tests/integration/ha/network.xml bin/integration/lib/network.sh
git commit -m "feat(integration): add libvirt NAT network definition and network helpers"
```

---

## Task 4: Write cloud-init templates and `bin/integration/lib/image.sh`

**Why:** Each VM needs a per-role cloud-init `user-data` that formats `/dev/vdb` as XFS and mounts it at the correct path. `meta-data` provides the instance-id and hostname to cloud-init. Both files are copied into the OS overlay at `/var/lib/cloud/seed/nocloud/` via `virt-customize`, which also runs an SELinux relabel so labels are correct on first boot. No ISO, no cdrom device.

**Files:**
- Create: `tests/integration/ha/cloud-init/user-data.pgnode`
- Create: `tests/integration/ha/cloud-init/user-data.pgmon`
- Create: `bin/integration/lib/image.sh`

- [ ] **Step 1: Create `tests/integration/ha/cloud-init/user-data.pgnode`**

This template is rendered by `write_cloud_init` via bash variable substitution (`envsubst`). Variables: `HOSTNAME`, `PUBKEY`.

```yaml
#cloud-config
hostname: ${HOSTNAME}
fqdn: ${HOSTNAME}.inttest.local
manage_etc_hosts: true

users:
  - name: root
    ssh_authorized_keys:
      - ${PUBKEY}

disk_setup:
  /dev/vdb:
    table_type: gpt
    layout: true
    overwrite: false

fs_setup:
  - label: pgdata
    filesystem: xfs
    device: /dev/vdb1
    overwrite: false

mounts:
  - [/dev/vdb1, /var/lib/pgsql, xfs, "defaults,noatime", "0", "2"]

runcmd:
  - mkdir -p /var/lib/pgsql
  - mount -a || true
```

- [ ] **Step 2: Create `tests/integration/ha/cloud-init/user-data.pgmon`**

```yaml
#cloud-config
hostname: ${HOSTNAME}
fqdn: ${HOSTNAME}.inttest.local
manage_etc_hosts: true

users:
  - name: root
    ssh_authorized_keys:
      - ${PUBKEY}

disk_setup:
  /dev/vdb:
    table_type: gpt
    layout: true
    overwrite: false

fs_setup:
  - label: backupstore
    filesystem: xfs
    device: /dev/vdb1
    overwrite: false

mounts:
  - [/dev/vdb1, /var/lib/pgbackrest, xfs, "defaults,noatime", "0", "2"]

runcmd:
  - mkdir -p /var/lib/pgbackrest
  - mount -a || true
```

- [ ] **Step 3: Create `bin/integration/lib/image.sh`**

```bash
#!/usr/bin/env bash
# bin/integration/lib/image.sh
# make_overlay      — thin qcow2 overlay on top of the base image
# make_data_disk    — sparse raw data disk
# write_cloud_init  — render per-VM user-data + meta-data into a tmpdir
# inject_cloud_init — upload rendered files into overlay via virt-customize

SCRIPT_DIR_IMAGE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLOUD_INIT_TEMPLATES="${SCRIPT_DIR_IMAGE}/../../../tests/integration/ha/cloud-init"

# make_overlay HOSTNAME
# Creates: ${PIGSTY_IMAGE_DIR}/${HOSTNAME}.qcow2
# Requires sudo because PIGSTY_IMAGE_DIR is /var/lib/libvirt/images/pigsty-lite.
make_overlay() {
  local hostname="$1"
  local overlay="${PIGSTY_IMAGE_DIR}/${hostname}.qcow2"
  ${SUDO} mkdir -p "${PIGSTY_IMAGE_DIR}"
  if ${SUDO} test -f "${overlay}"; then
    info "OS overlay for ${hostname} already exists — skipping"
    return
  fi
  info "Creating OS overlay for ${hostname}"
  ${SUDO} qemu-img create \
    -b "${PIGSTY_BASE_IMAGE}" \
    -F qcow2 \
    -f qcow2 \
    "${overlay}" \
    "${VM_OS_DISK_GB}G"
  info "OS overlay created (thin, backing: ${PIGSTY_BASE_IMAGE})"
}

# make_data_disk HOSTNAME
# Creates: ${PIGSTY_IMAGE_DIR}/${HOSTNAME}-data.raw  (sparse raw)
# Requires sudo because PIGSTY_IMAGE_DIR is /var/lib/libvirt/images/pigsty-lite.
make_data_disk() {
  local hostname="$1"
  local disk="${PIGSTY_IMAGE_DIR}/${hostname}-data.raw"
  if ${SUDO} test -f "${disk}"; then
    info "Data disk for ${hostname} already exists — skipping"
    return
  fi
  info "Creating data disk for ${hostname} (${VM_DATA_DISK_GB}G sparse raw)"
  ${SUDO} qemu-img create -f raw "${disk}" "${VM_DATA_DISK_GB}G"
}

# write_cloud_init HOSTNAME IP ROLE
# Renders user-data (from template matching ROLE) and meta-data into
# tests/integration/ha/cloud-init/generated/${HOSTNAME}/.
# That directory is gitignored and survives runs for post-failure inspection.
# ROLE: pgnode | pgmon
write_cloud_init() {
  local hostname="$1"
  local ip="$2"
  local role="$3"
  local template="${CLOUD_INIT_TEMPLATES}/user-data.${role}"
  local outdir="${CLOUD_INIT_TEMPLATES}/generated/${hostname}"
  [[ -f "${template}" ]] || die "cloud-init template not found: ${template}"
  mkdir -p "${outdir}"
  HOSTNAME="${hostname}" PUBKEY="$(cat "${PIGSTY_SSH_KEY}")" \
    envsubst < "${template}" > "${outdir}/user-data"
  cat > "${outdir}/meta-data" <<EOF
instance-id: ${hostname}
local-hostname: ${hostname}
EOF
  info "cloud-init files written to ${outdir}"
}

# inject_cloud_init HOSTNAME
# Reads rendered files from tests/integration/ha/cloud-init/generated/${HOSTNAME}/,
# uploads them into the NoCloud seed path in the OS overlay,
# registers RHEL subscription if PIGSTY_OS_VARIANT=rhel10,
# then runs SELinux relabel so cloud-init files have correct labels on boot.
inject_cloud_init() {
  local hostname="$1"
  local cloudinit_dir="${CLOUD_INIT_TEMPLATES}/generated/${hostname}"
  local overlay="${PIGSTY_IMAGE_DIR}/${hostname}.qcow2"
  [[ -f "${cloudinit_dir}/user-data" ]] || die "user-data not found at ${cloudinit_dir} — run write_cloud_init first"
  info "Injecting cloud-init into ${hostname} overlay (os-variant: ${PIGSTY_OS_VARIANT})"

  local rhsm_args=()
  if [[ "${PIGSTY_OS_VARIANT}" == "rhel10" ]]; then
    : "${PIGSTY_RHSM_USER:?PIGSTY_RHSM_USER must be set for RHEL}"
    : "${PIGSTY_RHSM_PASSWORD:?PIGSTY_RHSM_PASSWORD must be set for RHEL}"
    rhsm_args=(
      --run-command "subscription-manager register --username '${PIGSTY_RHSM_USER}' --password '${PIGSTY_RHSM_PASSWORD}' --auto-attach"
    )
  fi

  ${SUDO} virt-customize \
    -a "${overlay}" \
    "${rhsm_args[@]+"${rhsm_args[@]}"}" \
    --mkdir /var/lib/cloud/seed/nocloud \
    --upload "${cloudinit_dir}/user-data:/var/lib/cloud/seed/nocloud/user-data" \
    --upload "${cloudinit_dir}/meta-data:/var/lib/cloud/seed/nocloud/meta-data" \
    --selinux-relabel
}
```

- [ ] **Step 4: Make executable and commit**

```bash
chmod +x bin/integration/lib/image.sh
git add tests/integration/ha/cloud-init/ bin/integration/lib/image.sh
git commit -m "feat(integration): add cloud-init templates and image.sh disk/injection helpers"
```

---

## Task 5: Write `bin/integration/lib/vm.sh` — virt-install, wait-SSH, destroy

**Files:**
- Create: `bin/integration/lib/vm.sh`

- [ ] **Step 1: Create `bin/integration/lib/vm.sh`**

```bash
#!/usr/bin/env bash
# bin/integration/lib/vm.sh
# vm_install  HOSTNAME MAC   — define + boot a domain from its overlay
# vm_wait_ssh HOSTNAME IP    — poll SSH until ready
# vm_destroy  HOSTNAME       — destroy + undefine domain

# vm_install HOSTNAME MAC OS_VARIANT
# OS_VARIANT: passed to --os-variant (e.g. rocky10, rhel10, oraclelinux10)
vm_install() {
  local hostname="$1"
  local mac="$2"
  local os_variant="${3:-rocky10}"
  local overlay="${PIGSTY_IMAGE_DIR}/${hostname}.qcow2"
  local data_disk="${PIGSTY_IMAGE_DIR}/${hostname}-data.raw"

  if virsh dominfo "${hostname}" &>/dev/null; then
    info "Domain ${hostname} already defined — skipping virt-install"
    virsh start "${hostname}" 2>/dev/null || true
    return
  fi

  info "Installing domain ${hostname} (MAC ${mac}, os-variant ${os_variant})"
  virt-install \
    --name "${hostname}" \
    --ram "${VM_RAM_MB}" \
    --vcpus "${VM_VCPUS}" \
    --disk "path=${overlay},format=qcow2,bus=virtio" \
    --disk "path=${data_disk},format=raw,bus=virtio" \
    --network "network=${PIGSTY_NETWORK_NAME},mac=${mac},model=virtio" \
    --os-variant "${os_variant}" \
    --import \
    --noautoconsole \
    --noreboot
  virsh start "${hostname}"
}

# vm_wait_ssh HOSTNAME IP [TIMEOUT_SECS]
vm_wait_ssh() {
  local hostname="$1"
  local ip="$2"
  local timeout="${3:-300}"
  local elapsed=0
  info "Waiting for SSH on ${hostname} (${ip}) — timeout ${timeout}s"
  until ssh -o StrictHostKeyChecking=no \
            -o ConnectTimeout=3 \
            -o BatchMode=yes \
            -i "${PIGSTY_SSH_KEY%.pub}" \
            "root@${ip}" true 2>/dev/null; do
    sleep 5
    elapsed=$(( elapsed + 5 ))
    if (( elapsed >= timeout )); then
      die "Timed out waiting for SSH on ${hostname} (${ip})"
    fi
    (( elapsed % 30 == 0 )) && info "Still waiting for ${hostname}... ${elapsed}s elapsed"
  done
  info "SSH ready on ${hostname} (${ip})"
}

# vm_destroy HOSTNAME
vm_destroy() {
  local hostname="$1"
  if ! virsh dominfo "${hostname}" &>/dev/null; then
    info "Domain ${hostname} not found — nothing to destroy"
    return
  fi
  virsh destroy "${hostname}" 2>/dev/null || true
  virsh undefine "${hostname}" --remove-all-storage 2>/dev/null || true
  info "Domain ${hostname} destroyed"
}
```

- [ ] **Step 2: Make executable and commit**

```bash
chmod +x bin/integration/lib/vm.sh
git add bin/integration/lib/vm.sh
git commit -m "feat(integration): add vm.sh — virt-install, wait-SSH, destroy helpers"
```

---

## Task 6: Write the preflight script

**Files:**
- Create: `bin/integration-preflight.sh`

- [ ] **Step 1: Create `bin/integration-preflight.sh`**

```bash
#!/usr/bin/env bash
# bin/integration-preflight.sh
# Sanity-check the workstation before running the integration test.
# Exits non-zero if any check fails.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/integration/lib/common.sh"

ERRORS=0
fail() { warn "FAIL: $*"; (( ERRORS++ )) || true; }

# Required binaries
for cmd in virsh virt-install virt-customize qemu-img guestfish ssh ansible-playbook; do
  command -v "${cmd}" &>/dev/null || fail "Missing binary: ${cmd}"
done

# KVM accessible
[[ -w /dev/kvm ]] || fail "/dev/kvm not writable — add user to kvm/libvirt group or run as root"

# libvirtd running
systemctl is-active libvirtd &>/dev/null || fail "libvirtd is not running"

# sudo access (required for /var/lib/libvirt/images)
if [[ "${EUID}" -ne 0 ]]; then
  sudo -n true 2>/dev/null || fail "sudo access required (passwordless preferred); run: sudo visudo"
fi

# Base image present and readable
[[ -f "${PIGSTY_BASE_IMAGE}" ]] || fail "PIGSTY_BASE_IMAGE=${PIGSTY_BASE_IMAGE} not found"
[[ -r "${PIGSTY_BASE_IMAGE}" ]] || fail "PIGSTY_BASE_IMAGE=${PIGSTY_BASE_IMAGE} not readable"
qemu-img info "${PIGSTY_BASE_IMAGE}" &>/dev/null || fail "PIGSTY_BASE_IMAGE is not a valid qemu image"

# SSH public key present
[[ -f "${PIGSTY_SSH_KEY}" ]] || fail "PIGSTY_SSH_KEY=${PIGSTY_SSH_KEY} not found"
PRIV_KEY="${PIGSTY_SSH_KEY%.pub}"
[[ -f "${PRIV_KEY}" ]] || fail "SSH private key ${PRIV_KEY} not found"

# Free RAM: need at least 8 GB
FREE_MB=$(free -m | awk '/^Mem:/ {print $7}')
(( FREE_MB >= 7500 )) || warn "Low free RAM: ${FREE_MB}MB available, 8000MB recommended"

# Free disk in image dir (requires sudo to create it if absent)
${SUDO} mkdir -p "${PIGSTY_IMAGE_DIR}"
FREE_DISK_MB=$(df -m "${PIGSTY_IMAGE_DIR}" | awk 'NR==2 {print $4}')
# 4 OS overlays (~5 GB actual each) + 4 data disks (50 GB sparse each) = ~220 GB ceiling
(( FREE_DISK_MB >= 220000 )) || warn "Low disk in ${PIGSTY_IMAGE_DIR}: ${FREE_DISK_MB}MB available, 220000MB ceiling (sparse files won't consume it all at once)"

# RHEL subscription credentials required when OS variant is rhel10
if [[ "${PIGSTY_OS_VARIANT:-rocky10}" == "rhel10" ]]; then
  [[ -n "${PIGSTY_RHSM_USER:-}"     ]] || fail "PIGSTY_RHSM_USER must be set for RHEL"
  [[ -n "${PIGSTY_RHSM_PASSWORD:-}" ]] || fail "PIGSTY_RHSM_PASSWORD must be set for RHEL"
fi

if (( ERRORS > 0 )); then
  die "Preflight failed with ${ERRORS} error(s). Fix them before running test-integration-up."
fi
info "Preflight OK (os-variant: ${PIGSTY_OS_VARIANT:-rocky10})"
```

- [ ] **Step 2: Make executable and commit**

```bash
chmod +x bin/integration-preflight.sh
git add bin/integration-preflight.sh
git commit -m "feat(integration): add preflight script"
```

---

## Task 7: Write the static HA response file

The harness does not template the response file at runtime. A static file is simpler and covers the integration scenario exactly.

**Files:**
- Create: `tests/integration/ha/responses/ha.rsp.yml`

- [ ] **Step 1: Create `tests/integration/ha/responses/ha.rsp.yml`**

```yaml
---
profile: ha
network:
  ip_version: ipv4
cluster:
  name: pg-inttest
  domain: inttest.local
nodes:
  pgmon01:  {ip: 10.77.77.10, role: monitor}
  pgnode01: {ip: 10.77.77.11, role: pg_primary}
  pgnode02: {ip: 10.77.77.12, role: pg_replica}
  pgnode03: {ip: 10.77.77.13, role: pg_replica}
postgres:
  version: 18
  port: 5432
  tune: tiny
  shared_buffer_ratio: 0.15
  extensions: [pg_stat_statements]
  databases: [{name: app, owner: app}]
  users: []
  hba_rules:
    - {db: app, user: app, source: 10.77.77.0/24, method: scram-sha-256}
backup:
  enabled: true
  tool: pgbackrest
  schedule: {full: "0 1 * * 0", differential: "0 1 * * 1-6"}
  retention: {full: 2}
tls:
  internal_ca: generate
  user_facing: {mode: self_signed}
monitoring:
  vmsingle_retention: 7d
  vlsingle_retention: 7d
  alertmanager: {receivers: []}
repos:
  pigsty: {enabled: false, packages: []}
firewall:
  operator_cidrs: ["10.77.77.0/24"]
  postgres_client_cidrs: ["10.77.77.0/24"]
```

- [ ] **Step 2: Commit**

```bash
git add tests/integration/ha/responses/ha.rsp.yml
git commit -m "feat(integration): add static HA response file for integration scenario"
```

---

## Task 8: Write `bin/integration-up.sh` — the main orchestrator

**Files:**
- Create: `bin/integration-up.sh`

- [ ] **Step 1: Create `bin/integration-up.sh`**

```bash
#!/usr/bin/env bash
# bin/integration-up.sh
# Creates the libvirt network, overlays, and VMs, then runs ./configure.
# Usage: bin/integration-up.sh [--recreate]
#   --recreate  Destroy existing VMs and overlays before starting.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/integration/lib/common.sh"
source "${SCRIPT_DIR}/integration/lib/network.sh"
source "${SCRIPT_DIR}/integration/lib/image.sh"
source "${SCRIPT_DIR}/integration/lib/vm.sh"

RECREATE=0
for arg in "$@"; do [[ "${arg}" == "--recreate" ]] && RECREATE=1; done

if (( RECREATE )); then
  info "RECREATE requested — destroying existing VMs and disks"
  for entry in "${VMS[@]}"; do
    parse_vm_entry "${entry}"
    vm_destroy "${VM_HOSTNAME}"
    ${SUDO} rm -f "${PIGSTY_IMAGE_DIR}/${VM_HOSTNAME}.qcow2"
    ${SUDO} rm -f "${PIGSTY_IMAGE_DIR}/${VM_HOSTNAME}-data.raw"
  done
fi

info "=== Phase 1: network ==="
network_define_if_missing

info "=== Phase 2: disk creation + cloud-init injection ==="
for entry in "${VMS[@]}"; do
  parse_vm_entry "${entry}"
  make_overlay   "${VM_HOSTNAME}"
  make_data_disk "${VM_HOSTNAME}"
  write_cloud_init  "${VM_HOSTNAME}" "${VM_IP}" "${VM_ROLE}"
  inject_cloud_init "${VM_HOSTNAME}"
done

info "=== Phase 3: boot VMs ==="
for entry in "${VMS[@]}"; do
  parse_vm_entry "${entry}"
  vm_install "${VM_HOSTNAME}" "${VM_MAC}" "${PIGSTY_OS_VARIANT:-rocky10}"
done

info "=== Phase 4: wait for SSH ==="
for entry in "${VMS[@]}"; do
  parse_vm_entry "${entry}"
  vm_wait_ssh "${VM_HOSTNAME}" "${VM_IP}"
done

info "=== Phase 5: configure ==="
RSP="${PROJECT_ROOT}/tests/integration/ha/responses/ha.rsp.yml"
cp "${RSP}" "${PROJECT_ROOT}/responses/site.rsp.yml"
cd "${PROJECT_ROOT}"
./configure -s -f responses/site.rsp.yml

info "=== integration-up complete ==="
info "VMs are up and inventory is rendered. Run: make test-integration-converge"
```

- [ ] **Step 2: Make executable and commit**

```bash
chmod +x bin/integration-up.sh
git add bin/integration-up.sh
git commit -m "feat(integration): add integration-up.sh orchestrator"
```

---

## Task 9: Write `bin/integration-down.sh`

**Files:**
- Create: `bin/integration-down.sh`

- [ ] **Step 1: Create `bin/integration-down.sh`**

```bash
#!/usr/bin/env bash
# bin/integration-down.sh
# Destroys VMs, deletes overlays, optionally destroys the network.
# Usage: bin/integration-down.sh
#   KEEP_NETWORK=1 bin/integration-down.sh   — leave the libvirt network intact
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/integration/lib/common.sh"
source "${SCRIPT_DIR}/integration/lib/network.sh"
source "${SCRIPT_DIR}/integration/lib/vm.sh"

info "=== Destroying VMs ==="
for entry in "${VMS[@]}"; do
  parse_vm_entry "${entry}"
  vm_destroy "${VM_HOSTNAME}"
done

info "=== Removing disks ==="
for entry in "${VMS[@]}"; do
  parse_vm_entry "${entry}"
  overlay="${PIGSTY_IMAGE_DIR}/${VM_HOSTNAME}.qcow2"
  data_disk="${PIGSTY_IMAGE_DIR}/${VM_HOSTNAME}-data.raw"
  ${SUDO} test -f "${overlay}"   && ${SUDO} rm -f "${overlay}"   && info "Removed ${overlay}"
  ${SUDO} test -f "${data_disk}" && ${SUDO} rm -f "${data_disk}" && info "Removed ${data_disk}"
done

if [[ "${KEEP_NETWORK:-0}" == "1" ]]; then
  info "KEEP_NETWORK=1 — leaving network ${PIGSTY_NETWORK_NAME} intact"
else
  info "=== Destroying network ==="
  network_destroy
fi

info "=== integration-down complete ==="
```

- [ ] **Step 2: Make executable and commit**

```bash
chmod +x bin/integration-down.sh
git add bin/integration-down.sh
git commit -m "feat(integration): add integration-down.sh"
```

---

## Task 10: Write `bin/integration-converge.sh` and `bin/integration-verify.sh`

**Files:**
- Create: `bin/integration-converge.sh`
- Create: `bin/integration-verify.sh`

- [ ] **Step 1: Create `bin/integration-converge.sh`**

```bash
#!/usr/bin/env bash
# bin/integration-converge.sh
# Runs ansible-playbook playbooks/site.yml against the integration inventory.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/integration/lib/common.sh"

info "Running site.yml against integration inventory"
cd "${PROJECT_ROOT}"
ansible-playbook playbooks/site.yml \
  -i inventory/site.yml \
  --private-key "${PIGSTY_SSH_KEY%.pub}" \
  -u root \
  "$@"
info "converge complete"
```

- [ ] **Step 2: Create `bin/integration-verify.sh`**

```bash
#!/usr/bin/env bash
# bin/integration-verify.sh
# Runs the Ansible verify playbook.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/integration/lib/common.sh"

info "Running verify playbook"
cd "${PROJECT_ROOT}"
ansible-playbook tests/integration/ha/verify.yml \
  -i inventory/site.yml \
  --private-key "${PIGSTY_SSH_KEY%.pub}" \
  -u root \
  "$@"
info "verify complete"
```

- [ ] **Step 3: Make executable and commit**

```bash
chmod +x bin/integration-converge.sh bin/integration-verify.sh
git add bin/integration-converge.sh bin/integration-verify.sh
git commit -m "feat(integration): add converge and verify runner scripts"
```

---

## Task 11: Write the Ansible verify playbook and check tasks

**Files:**
- Create: `tests/integration/ha/verify.yml`
- Create: `tests/integration/ha/verify/connectivity.yml`
- Create: `tests/integration/ha/verify/patroni_state.yml`
- Create: `tests/integration/ha/verify/etcd_quorum.yml`
- Create: `tests/integration/ha/verify/backups.yml`
- Create: `tests/integration/ha/verify/selinux.yml`
- Create: `tests/integration/ha/verify/idempotency.yml`

- [ ] **Step 1: Create `tests/integration/ha/verify.yml`**

```yaml
---
- name: Integration verify — HA scenario
  hosts: all
  gather_facts: false
  tasks:
    - name: Connectivity checks
      import_tasks: verify/connectivity.yml
      tags: [connectivity]

    - name: Patroni state checks
      import_tasks: verify/patroni_state.yml
      tags: [patroni]
      when: "'postgres' in group_names"

    - name: etcd quorum checks
      import_tasks: verify/etcd_quorum.yml
      tags: [etcd]
      when: "'etcd' in group_names"

    - name: Backup checks
      import_tasks: verify/backups.yml
      tags: [backups]
      when: "'postgres' in group_names"

    - name: SELinux checks
      import_tasks: verify/selinux.yml
      tags: [selinux]

    - name: Idempotency check
      import_tasks: verify/idempotency.yml
      tags: [idempotency]
      when: inventory_hostname == groups['postgres'][0]
```

- [ ] **Step 2: Create `tests/integration/ha/verify/connectivity.yml`**

```yaml
---
- name: Check host is reachable
  ansible.builtin.ping:

- name: Check postgres port is listening
  ansible.builtin.wait_for:
    host: "{{ inventory_hostname }}"
    port: 5432
    timeout: 10
  when: "'postgres' in group_names"

- name: Check etcd client port is listening
  ansible.builtin.wait_for:
    host: "{{ inventory_hostname }}"
    port: 2379
    timeout: 10
  when: "'etcd' in group_names"
```

- [ ] **Step 3: Create `tests/integration/ha/verify/patroni_state.yml`**

```yaml
---
- name: Get Patroni cluster state
  ansible.builtin.command: patronictl -c /etc/patroni/patroni.yml list --format json
  register: patroni_list
  changed_when: false
  when: "'postgres' in group_names"

- name: Parse Patroni JSON
  ansible.builtin.set_fact:
    patroni_members: "{{ patroni_list.stdout | from_json }}"
  when: "'postgres' in group_names"

- name: Assert exactly one Leader in Patroni
  ansible.builtin.assert:
    that:
      - patroni_members | selectattr('Role', 'equalto', 'Leader') | list | length == 1
    fail_msg: "Expected exactly 1 Patroni Leader, got: {{ patroni_members | selectattr('Role', 'equalto', 'Leader') | list | length }}"
  when: "'postgres' in group_names"

- name: Assert all Patroni members are running
  ansible.builtin.assert:
    that:
      - patroni_members | selectattr('State', 'ne', 'running') | list | length == 0
    fail_msg: "Some Patroni members not running: {{ patroni_members | selectattr('State', 'ne', 'running') | map(attribute='Member') | list }}"
  when: "'postgres' in group_names"
```

- [ ] **Step 4: Create `tests/integration/ha/verify/etcd_quorum.yml`**

```yaml
---
- name: Check etcd cluster health
  ansible.builtin.command: >
    etcdctl endpoint health
    --endpoints=https://127.0.0.1:2379
    --cacert=/etc/pki/pigsty-lite/ca.crt
    --cert=/etc/pki/pigsty-lite/etcd/peer.crt
    --key=/etc/pki/pigsty-lite/etcd/peer.key
  register: etcd_health
  changed_when: false
  when: "'etcd' in group_names"

- name: Assert etcd is healthy
  ansible.builtin.assert:
    that:
      - "'is healthy' in etcd_health.stdout"
    fail_msg: "etcd endpoint not healthy: {{ etcd_health.stdout }}"
  when: "'etcd' in group_names"

- name: Get etcd member list
  ansible.builtin.command: >
    etcdctl member list
    --endpoints=https://127.0.0.1:2379
    --cacert=/etc/pki/pigsty-lite/ca.crt
    --cert=/etc/pki/pigsty-lite/etcd/peer.crt
    --key=/etc/pki/pigsty-lite/etcd/peer.key
    --write-out=fields
  register: etcd_members
  changed_when: false
  when: "'etcd' in group_names and inventory_hostname == groups['etcd'][0]"

- name: Assert 3 etcd members
  ansible.builtin.assert:
    that:
      - etcd_members.stdout_lines | select('search', '"IsLearner"') | list | length == 3
    fail_msg: "Expected 3 etcd members"
  when: "'etcd' in group_names and inventory_hostname == groups['etcd'][0]"
```

- [ ] **Step 5: Create `tests/integration/ha/verify/backups.yml`**

```yaml
---
- name: Check pgbackrest stanza info
  ansible.builtin.command: pgbackrest --stanza={{ backup_stanza | default('pg-inttest') }} info
  register: pgbackrest_info
  changed_when: false
  become: true
  become_user: postgres
  when: "'postgres' in group_names"

- name: Assert pgbackrest stanza is OK
  ansible.builtin.assert:
    that:
      - "'status: ok' in pgbackrest_info.stdout"
    fail_msg: "pgbackrest stanza not OK: {{ pgbackrest_info.stdout }}"
  when: "'postgres' in group_names"
```

- [ ] **Step 6: Create `tests/integration/ha/verify/selinux.yml`**

```yaml
---
- name: Check SELinux mode
  ansible.builtin.command: getenforce
  register: selinux_mode
  changed_when: false

- name: Assert SELinux is Enforcing or Permissive (not Disabled)
  ansible.builtin.assert:
    that:
      - selinux_mode.stdout in ['Enforcing', 'Permissive']
    fail_msg: "SELinux is {{ selinux_mode.stdout }}, expected Enforcing or Permissive"

- name: Check for SELinux AVC denials related to pigsty-lite services
  ansible.builtin.command: ausearch -m AVC -ts recent
  register: avc_check
  changed_when: false
  failed_when: false

- name: Warn if recent AVC denials found
  ansible.builtin.debug:
    msg: "WARNING: AVC denials found — inspect with: ausearch -m AVC -ts recent\n{{ avc_check.stdout }}"
  when: avc_check.rc == 0 and avc_check.stdout != ''
```

- [ ] **Step 7: Create `tests/integration/ha/verify/idempotency.yml`**

```yaml
---
# Run site.yml a second time on just the primary; assert zero changes.
- name: Run site.yml in check mode to verify idempotency
  ansible.builtin.command: >
    ansible-playbook playbooks/site.yml
    -i inventory/site.yml
    --check
    --diff
    -u root
    --private-key {{ ansible_ssh_private_key_file | default('~/.ssh/id_ed25519') }}
  delegate_to: localhost
  register: idempotency_run
  changed_when: false

- name: Assert no changes in idempotency run
  ansible.builtin.assert:
    that:
      - "'changed=0' in idempotency_run.stdout"
    fail_msg: "Idempotency check found changes:\n{{ idempotency_run.stdout }}"
```

- [ ] **Step 8: Commit**

```bash
git add tests/integration/ha/verify.yml tests/integration/ha/verify/
git commit -m "feat(integration): add Ansible verify playbook and acceptance check tasks"
```

---

## Task 12: Add Makefile targets and .gitignore entries

**Files:**
- Modify: `Makefile`
- Modify: `.gitignore` (create if missing)

- [ ] **Step 1: Read the current Makefile bottom to find the insertion point**

Run: `tail -20 Makefile`

- [ ] **Step 2: Add integration targets to `Makefile`**

Append after the last existing target:

```makefile
# Integration tests (local only — requires libvirt + KVM + PIGSTY_BASE_IMAGE)
.PHONY: test-integration test-integration-up test-integration-down test-integration-converge test-integration-verify

test-integration: test-integration-up test-integration-converge test-integration-verify test-integration-down

test-integration-up:
	@echo "=== Integration: preflight ==="
	bash bin/integration-preflight.sh
	@echo "=== Integration: up ==="
	bash bin/integration-up.sh $(if $(filter 1,$(RECREATE)),--recreate,)

test-integration-down:
	@echo "=== Integration: down ==="
	bash bin/integration-down.sh

test-integration-converge:
	@echo "=== Integration: converge ==="
	bash bin/integration-converge.sh

test-integration-verify:
	@echo "=== Integration: verify ==="
	bash bin/integration-verify.sh
```

- [ ] **Step 3: Update the help target in `Makefile`**

Add these lines to the help echo block (inside the existing `help:` recipe):

```makefile
	@echo
	@echo "  Integration tests (local only, requires PIGSTY_BASE_IMAGE):"
	@echo "  make test-integration PIGSTY_BASE_IMAGE=/path/to/base.qcow2"
	@echo "  make test-integration-up / down / converge / verify"
	@echo "  RECREATE=1 make test-integration-up  — destroy and recreate VMs"
```

- [ ] **Step 4: Update `.gitignore`**

```bash
# Check if .gitignore exists
ls .gitignore 2>/dev/null || touch .gitignore
```

Append to `.gitignore`:

```
# Integration test per-run artifacts
tests/integration/**/cloud-init/generated/
```

- [ ] **Step 4b: Create `.gitkeep` so git tracks the generated directory**

```bash
mkdir -p tests/integration/ha/cloud-init/generated
touch tests/integration/ha/cloud-init/generated/.gitkeep
```

- [ ] **Step 5: Commit**

```bash
git add Makefile .gitignore tests/integration/ha/cloud-init/generated/.gitkeep
git commit -m "feat(integration): add Makefile integration targets, gitignore, and generated dir placeholder"
```

---

## Task 13: Smoke-test the harness end-to-end

This task is a manual verification checklist — no new code is written.

- [ ] **Step 1: Run preflight**

```bash
export PIGSTY_BASE_IMAGE=/path/to/Rocky-10-GenericCloud.latest.x86_64.qcow2
bash bin/integration-preflight.sh
```

Expected output ends with: `INFO  Preflight OK`

- [ ] **Step 2: Verify guestfish can see the base image partitions**

```bash
guestfish -a "${PIGSTY_BASE_IMAGE}" run : list-filesystems
```

Expected: output includes at least one line containing `ext4` or `xfs`. If the root partition is not `/dev/sda4`, update `bin/integration/lib/image.sh` `mount` commands to the correct device.

- [ ] **Step 3: Inspect base image partition layout**

```bash
guestfish -a "${PIGSTY_BASE_IMAGE}" run : list-filesystems
```

Expected: output lists filesystems including the root partition (e.g. `/dev/sda4: xfs`). This is informational only — `virt-customize` auto-detects the root filesystem.

- [ ] **Step 4: Create OS overlay, data disk, render and inject cloud-init**

```bash
source bin/integration/lib/common.sh
source bin/integration/lib/image.sh
parse_vm_entry "${VMS[0]}"   # sets VM_HOSTNAME, VM_IP, VM_MAC, VM_ROLE
make_overlay   "${VM_HOSTNAME}"
make_data_disk "${VM_HOSTNAME}"
sudo qemu-img info "${PIGSTY_IMAGE_DIR}/${VM_HOSTNAME}.qcow2"
sudo qemu-img info "${PIGSTY_IMAGE_DIR}/${VM_HOSTNAME}-data.raw"
write_cloud_init  "${VM_HOSTNAME}" "${VM_IP}" "${VM_ROLE}"
inject_cloud_init "${VM_HOSTNAME}"
```

Expected: `qemu-img info` on the overlay shows `file format: qcow2` and `backing file: <PIGSTY_BASE_IMAGE>`. The raw file shows `file format: raw`. `virt-customize` exits without errors.

- [ ] **Step 5: Inspect generated cloud-init files and verify injection**

```bash
# Inspect the rendered files on disk (no sudo needed — written to repo tree)
cat "tests/integration/ha/cloud-init/generated/${VM_HOSTNAME}/user-data"
cat "tests/integration/ha/cloud-init/generated/${VM_HOSTNAME}/meta-data"

# Verify the files were injected into the overlay
sudo virt-cat -a "${PIGSTY_IMAGE_DIR}/${VM_HOSTNAME}.qcow2" \
  /var/lib/cloud/seed/nocloud/user-data
```

Expected: `user-data` starts with `#cloud-config`, contains the correct hostname and your public key. `meta-data` contains `instance-id: pgmon01`.

- [ ] **Step 6: Clean up test overlays**

```bash
rm -rf /tmp/pigsty-test-overlays
```

- [ ] **Step 7: Run full integration test**

```bash
make test-integration PIGSTY_BASE_IMAGE=/path/to/Rocky-10-GenericCloud.latest.x86_64.qcow2
```

Expected: exits 0. Total runtime ~25-35 minutes.

- [ ] **Step 8: Verify repeatable run (RECREATE=0)**

```bash
make test-integration-converge
make test-integration-verify
```

Expected: both exit 0 without re-creating VMs.

- [ ] **Step 9: Tear down**

```bash
make test-integration-down
virsh list --all   # should show no pigsty-lite-test domains
virsh net-list     # should show pigsty-lite-test is gone (or present if KEEP_NETWORK=1)
```

---

## Self-review: spec coverage and placeholder check

**Spec requirements covered:**

| Requirement | Task |
|---|---|
| 4 KVM VMs from existing base image on local disk | Tasks 4, 5, 8 |
| cloud-init: SSH key, hostname, disk format + mount via NoCloud seed | Tasks 4 (templates + injection) |
| Sparse raw data disk per VM (pgdata / backup store) | Tasks 4, 5, 8, 9 |
| Multi-distro support (Rocky 10, Oracle Linux 10, RHEL 10 + subscription) | Tasks 1, 4, 6 |
| Dedicated libvirt NAT network with MAC→IP DHCP | Tasks 3, 4 |
| `make deploy` end-to-end | Tasks 7, 8, 10 |
| Connectivity acceptance check | Task 11 |
| Patroni state acceptance check | Task 11 |
| etcd quorum acceptance check | Task 11 |
| Backup (pgbackrest) acceptance check | Task 11 |
| SELinux acceptance check | Task 11 |
| Idempotency check | Task 11 |
| `make test-integration` Makefile entry point | Task 12 |
| RECREATE flag for re-running without destroy | Tasks 8, 12 |
| Operator README with troubleshooting | Task 1 |
| Preflight workstation check | Task 6 |
| Local-only, not in CI | Not added to any workflow |

**Placeholder scan:** No TBD, TODO, or "similar to Task N" items present.

**Type consistency:** All scripts source `topology.env` before calling `parse_vm_entry`; `VM_ROLE` is parsed from the VMS tuple and passed through `write_cloud_init` → template selection and `inject_cloud_init` → RHEL branch; `PIGSTY_BASE_IMAGE`, `PIGSTY_SSH_KEY`, `PIGSTY_IMAGE_DIR`, `PIGSTY_NETWORK_NAME`, `PIGSTY_OS_VARIANT` are consistently named across all files. Data disk path `${PIGSTY_IMAGE_DIR}/${hostname}-data.raw` is constructed identically in `make_data_disk`, `vm_install`, and `integration-down.sh`.
