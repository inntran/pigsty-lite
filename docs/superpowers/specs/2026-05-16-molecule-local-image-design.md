# Molecule local shared base image design

## Problem

Local Molecule runs should reuse a mid-stage container image across runs (similar to CI reuse) without depending on GitHub Actions, while remaining easy to rebuild when the base definition changes.

## Goals

- Keep image sharing local-only.
- Use one image family that satisfies all known Molecule scenarios.
- Rebuild automatically when `tests/molecule/Containerfile` changes.
- Keep developer workflow simple and deterministic.

## Non-goals

- Introducing a local registry service.
- Introducing per-role or per-scenario image variants.
- Changing GitHub Actions workflow behavior unless required for compatibility.

## Chosen approach

Use a hash-tagged local base image with a stable alias:

- Immutable tag: `localhost/molecule-base:<containerfile-hash>`
- Stable alias: `localhost/molecule-base:latest`

A local helper target computes the hash from `tests/molecule/Containerfile` and:

1. Reuses the hash-tagged image if it already exists locally.
2. Builds it if missing.
3. Retags it to `localhost/molecule-base:latest` for Molecule configs.

This provides deterministic rebuild semantics and fast repeated runs.

## Design details

### 1. Build/reuse entry point

Add `make molecule-image`:

- Input: `tests/molecule/Containerfile`
- Output image tags:
  - `localhost/molecule-base:<hash>`
  - `localhost/molecule-base:latest`
- Behavior:
  - Fail fast if Podman is unavailable.
  - If hash-tagged image exists, skip build.
  - If absent, build once and tag.
  - Always refresh the `:latest` alias to the resolved hash image.

Optional extension: support `REBUILD=1` to force rebuilding even when hash image exists.

### 2. Molecule image standardization

Standardize scenario `platforms[].image` values to:

`localhost/molecule-base:latest`

This removes drift where some scenarios still use upstream base images directly.

### 3. Invocation flow

Primary path:

1. Developer runs Molecule-related command.
2. `molecule-image` ensure step runs first.
3. Molecule uses shared local base via `localhost/molecule-base:latest`.

The same base image is reused by all known scenarios.

### 4. Error handling

- Podman not installed/unavailable: stop with actionable error.
- Image build fails: stop immediately.
- Do not fall back silently to a different image source.

## Testing strategy

1. First run: image missing => build expected.
2. Second run without Containerfile change: reuse expected (no rebuild).
3. Modify `tests/molecule/Containerfile`: new hash => rebuild expected.
4. Run representative Molecule scenarios to confirm all required packages are present in the shared base image.

## CI compatibility

Current GitHub Actions Molecule workflow already builds and caches `molecule-base:latest` keyed by Containerfile hash. This design remains compatible with existing CI behavior.

## Risks and mitigations

- **Risk:** Containerfile lacks dependency needed by some scenario.
  - **Mitigation:** keep package set union-complete for all known scenarios; validate through representative scenario runs.
- **Risk:** stale `:latest` alias confusion.
  - **Mitigation:** alias refreshed by `molecule-image` ensure step on each invocation.
