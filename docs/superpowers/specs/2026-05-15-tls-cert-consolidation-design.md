# TLS Certificate Consolidation to `/etc/pki/pigsty`

**Date:** 2026-05-15  
**Status:** Design approved  
**Scope:** Centralize all TLS certificates and keys to a single unified directory across all hosts

## Problem Statement

Currently, TLS certificates are scattered across multiple paths:
- Control node: `pki/ca/` (local filesystem)
- Target nodes: `/etc/pki/pigsty-lite/` (default, but variable)
- Various roles use different path variables: `certs_pki_dir`, `etcd_pki_dir`, `ca_dir`

This fragmentation makes it harder to:
- Locate certificates quickly
- Understand security boundaries
- Manage certificate permissions consistently
- Reason about CA key distribution

## Proposed Solution

**Consolidate all TLS certificates and keys to a single directory:** `/etc/pki/pigsty/`

### Changes

#### 1. New Single-Source-of-Truth Variable

Create `pigsty_pki_dir` in `group_vars/all.yml`:
```yaml
pigsty_pki_dir: /etc/pki/pigsty
```

All roles that previously used:
- `certs_pki_dir` → becomes `{{ pigsty_pki_dir }}`
- `etcd_pki_dir` → becomes `{{ pigsty_pki_dir }}`
- `ca_dir` → becomes `{{ pigsty_pki_dir }}` (for distributed cert/key)

#### 2. CA Key & Cert Distribution

**Control node (localhost):**
- Generate CA cert + key in `pki/ca/` (local, for signing)
- Also copy to `{{ playbook_dir | dirname }}/pki/pigsty/ca.crt` and `ca.key`

**All target nodes:**
- Create directory `/etc/pki/pigsty/` with permissions `0755`
- Distribute CA cert: `ca.crt` (readable by all services)
- Distribute CA key: `ca.key` (readable only by ansible runner or cert renewal process)

#### 3. Certificate Layout

After consolidation, all nodes have:
```
/etc/pki/pigsty/
├── ca.crt                    # Public CA cert (readable by all)
├── ca.key                    # CA private key (readable by renewal process)
├── hostname1.crt             # Per-host cert (flattened)
├── hostname1.key             # Per-host key (flattened)
├── hostname2.crt
├── hostname2.key
└── ...
```

**Permissions:**
- `ca.crt`: `0644` (world-readable, needed for TLS validation)
- `ca.key`: `0600` (readable only by the ansible process or designated cert renewal user)
- `*.crt` (host certs): `0644`
- `*.key` (host keys): `0600`

#### 4. Files to Update

**Defaults (variables):**
- `group_vars/all.yml` — add `pigsty_pki_dir: /etc/pki/pigsty`
- `roles/ca/defaults/main.yml` — point `ca_dir` to `{{ pigsty_pki_dir }}`
- `roles/certs/defaults/main.yml` — point `certs_pki_dir` to `{{ pigsty_pki_dir }}`
- All other role defaults that reference cert paths

**Tasks:**
- `roles/ca/tasks/main.yml` — ensure `/etc/pki/pigsty/` exists; distribute CA cert + key
- `roles/certs/tasks/main.yml` — generate/sign certs to `{{ pigsty_pki_dir }}/{{ hostname }}.crt`
- `roles/etcd/tasks/main.yml` — read from `{{ pigsty_pki_dir }}/`
- `roles/patroni/tasks/main.yml`, `roles/haproxy/tasks/main.yml`, etc.
- `roles/monitoring_agents/tasks/main.yml` — read CA cert from `{{ pigsty_pki_dir }}/ca.crt`

**Documentation:**
- `roles/ca/README.md` — update to reflect `/etc/pki/pigsty/`
- `roles/certs/README.md` — update paths and flow
- `docs/superpowers/specs/2026-05-12-pigsty-lite-design.md` — update architecture section
- `docs/superpowers/plans/*` — update all references

**Tests & Verification:**
- `tests/molecule/certs/molecule/default/molecule.yml` — update paths
- `tests/molecule/*/molecule/*/verify.yml` — validate cert locations
- All molecule.yml fixtures that reference cert paths

#### 5. Backward Compatibility

- Old default `/etc/pki/pigsty-lite/` will not be used
- Variable `certs_pki_dir` will become an alias to `pigsty_pki_dir` for backward compatibility
- Operators can override via `pigsty_pki_dir: /custom/path` if needed

#### 6. CA Operating Manual

A separate operating manual (`docs/operations/ca-operating-manual.md`) covers:
- CA generation and initialization
- Certificate signing workflow
- Manual certificate renewal
- Troubleshooting certificate validation failures
- Backup and recovery of CA keys

## Implementation Plan

1. Add `pigsty_pki_dir` to `group_vars/all.yml`
2. Update role defaults (ca, certs, etcd, patroni, haproxy, nginx_proxy, monitoring_agents, etc.)
3. Update role tasks (ensure paths, create directories, adjust references)
4. Update documentation and README files
5. Update molecule tests to use new paths
6. Update spec and plan documents
7. Test end-to-end with a clean cluster deployment
8. Commit and document CA operating manual

## Success Criteria

✅ All certificates reside in `/etc/pki/pigsty/` on all nodes  
✅ CA cert distributed to all nodes for TLS validation  
✅ CA key accessible for certificate renewal operations  
✅ All roles reference `pigsty_pki_dir` consistently  
✅ Documentation reflects new structure  
✅ Tests pass with new paths  
✅ No breaking changes to deployment workflow  
