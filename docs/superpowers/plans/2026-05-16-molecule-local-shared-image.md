# Molecule Local Shared Image Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic, local-only shared Molecule base image workflow that is reused across runs and rebuilt only when `tests/molecule/Containerfile` changes.

**Architecture:** Add one image builder/ensurer shell script plus a `make molecule-image` entry point that computes a Containerfile hash, reuses/builds `localhost/molecule-base:<hash>`, and refreshes `localhost/molecule-base:latest`. Standardize all Molecule scenario files to consume the shared local tag so every scenario uses the same base image source.

**Tech Stack:** GNU Make, Bash, Podman, Molecule (podman driver), ripgrep

---

## File structure and responsibilities

- Create: `bin/molecule_image.sh`  
  One responsibility: ensure hash-based local image exists and refresh `:latest` alias.
- Modify: `Makefile`  
  Add `molecule-image` target and wire `test-role` to call it before `molecule test`.
- Modify:
  - `tests/molecule/cluster_ops/molecule/default/molecule.yml`
  - `tests/molecule/grafana/molecule/default/molecule.yml`
  - `tests/molecule/monitoring_agents/molecule/default/molecule.yml`
  - `tests/molecule/monitoring_server/molecule/default/molecule.yml`
  - `tests/molecule/nginx_proxy/molecule/default/molecule.yml`  
  Replace `docker.io/oraclelinux:10` references with `localhost/molecule-base:latest`.
- Optional docs touch (only if help text changed): `README.md` quick testing note.

### Task 1: Add local image ensure script

**Files:**

- Create: `bin/molecule_image.sh`
- Test: command-line checks from repository root

- [ ] **Step 1: Write the failing test**

```bash
test -x bin/molecule_image.sh
```

Expected: non-zero exit code (`bin/molecule_image.sh` does not exist/executable yet).

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
test -x bin/molecule_image.sh; echo $?
```

Expected: `1`

- [ ] **Step 3: Write minimal implementation**

```bash
#!/usr/bin/env bash
set -euo pipefail

containerfile="${1:-tests/molecule/Containerfile}"
image_repo="${2:-localhost/molecule-base}"

if ! command -v podman >/dev/null 2>&1; then
  echo "ERROR: podman is required but not found in PATH" >&2
  exit 1
fi

if [ ! -f "$containerfile" ]; then
  echo "ERROR: containerfile not found: $containerfile" >&2
  exit 1
fi

hash="$(sha256sum "$containerfile" | awk '{print $1}')"
hashed_tag="${image_repo}:${hash}"
latest_tag="${image_repo}:latest"

if ! podman image exists "$hashed_tag"; then
  podman build -t "$hashed_tag" -f "$containerfile" .
fi

podman tag "$hashed_tag" "$latest_tag"
echo "Using image: $hashed_tag"
```

Then make it executable:

```bash
chmod +x bin/molecule_image.sh
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
test -x bin/molecule_image.sh; echo $?
```

Expected: `0`

- [ ] **Step 5: Commit**

```bash
git add bin/molecule_image.sh
git commit -m "feat(molecule): add local hash-based image ensure script"
```

### Task 2: Add Make target and wire Molecule role testing

**Files:**

- Modify: `Makefile`
- Test: `make` target checks

- [ ] **Step 1: Write the failing test**

```bash
make molecule-image
```

Expected: failure like `No rule to make target 'molecule-image'`.

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
make molecule-image; echo $?
```

Expected: non-zero exit code.

- [ ] **Step 3: Write minimal implementation**

Add/update these exact parts in `Makefile`:

```make
.PHONY: help init configure plan deploy clean test-role molecule-image switchover failover minor-upgrade scale-add-replica scale-remove-replica
```

```make
 @echo "  make molecule-image Build/reuse local shared Molecule base image"
```

```make
molecule-image:
 ./bin/molecule_image.sh tests/molecule/Containerfile localhost/molecule-base
```

```make
test-role: molecule-image
 @if [ -z "$(ROLE)" ]; then echo "Usage: make test-role ROLE=<name>"; exit 2; fi
 cd tests/molecule/$(ROLE) && molecule test
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
make molecule-image
```

Expected:

- command exits `0`
- output includes `Using image: localhost/molecule-base:`
- `podman image exists localhost/molecule-base:latest` succeeds

Then verify help text:

```bash
make help | rg "molecule-image"
```

Expected: one line describing `make molecule-image`.

- [ ] **Step 5: Commit**

```bash
git add Makefile
git commit -m "feat(molecule): add make target for shared local image"
```

### Task 3: Standardize all scenario image references

**Files:**

- Modify:
  - `tests/molecule/cluster_ops/molecule/default/molecule.yml`
  - `tests/molecule/grafana/molecule/default/molecule.yml`
  - `tests/molecule/monitoring_agents/molecule/default/molecule.yml`
  - `tests/molecule/monitoring_server/molecule/default/molecule.yml`
  - `tests/molecule/nginx_proxy/molecule/default/molecule.yml`

- [ ] **Step 1: Write the failing test**

```bash
rg -n "image:\\s+docker\\.io/oraclelinux:10" tests/molecule/**/molecule.yml
```

Expected: matches in the five files above (and two entries in monitoring_agents).

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
rg -n "image:\\s+docker\\.io/oraclelinux:10" tests/molecule/**/molecule.yml | wc -l
```

Expected: count greater than `0`.

- [ ] **Step 3: Write minimal implementation**

In each listed file, replace:

```yaml
image: docker.io/oraclelinux:10
```

with:

```yaml
image: localhost/molecule-base:latest
```

No other keys change.

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
rg -n "image:\\s+docker\\.io/oraclelinux:10" tests/molecule/**/molecule.yml
```

Expected: no output.

Then confirm all scenarios are standardized:

```bash
rg -n "image:\\s+localhost/molecule-base:latest" tests/molecule/**/molecule.yml | wc -l
```

Expected: count increases from baseline and includes all scenario files.

- [ ] **Step 5: Commit**

```bash
git add tests/molecule/*/molecule/*/molecule.yml
git commit -m "test(molecule): standardize scenario images to local shared base"
```

### Task 4: End-to-end behavior verification

**Files:**

- No new files required
- Test commands only

- [ ] **Step 1: Write the failing test**

Use a strict check that enforces “no rebuild on unchanged Containerfile” by capturing image IDs before/after:

```bash
before_id="$(podman image inspect localhost/molecule-base:latest --format '{{.Id}}' 2>/dev/null || true)"
test -n "$before_id"
```

Expected: may fail if image is absent (acceptable precondition failure).

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
before_id="$(podman image inspect localhost/molecule-base:latest --format '{{.Id}}' 2>/dev/null || true)"; test -n "$before_id"; echo $?
```

Expected: `1` on first clean machine.

- [ ] **Step 3: Write minimal implementation**

Prime and verify reuse:

```bash
make molecule-image
id1="$(podman image inspect localhost/molecule-base:latest --format '{{.Id}}')"
make molecule-image
id2="$(podman image inspect localhost/molecule-base:latest --format '{{.Id}}')"
test "$id1" = "$id2"
```

Then run one fast representative scenario:

```bash
make test-role ROLE=preflight
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
echo "$id1"
echo "$id2"
test "$id1" = "$id2"; echo $?
```

Expected:

- IDs are identical when Containerfile is unchanged
- exit code `0`
- representative Molecule role completes successfully

- [ ] **Step 5: Commit**

```bash
git add Makefile bin/molecule_image.sh tests/molecule/*/molecule/*/molecule.yml
git commit -m "feat(molecule): enable reusable local shared image workflow"
```

## Spec coverage check

- **Local-only sharable image across runs:** covered by Task 1 + Task 2.
- **One image for all known scenarios:** covered by Task 3.
- **Easy deterministic rebuild:** covered by hash tagging in Task 1 and reuse checks in Task 4.
- **CI compatibility retained:** no CI workflow changes planned; behavior remains compatible.

## Placeholder scan

- No `TODO`, `TBD`, or “implement later” placeholders remain.
- All code-edit steps include concrete code blocks.
- All verification steps include exact commands and expected outcomes.

## Type/signature consistency check

- Image repository naming is consistent: `localhost/molecule-base`.
- Canonical scenario image tag is consistent: `localhost/molecule-base:latest`.
- Make target naming is consistent: `molecule-image`.
