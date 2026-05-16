# ca

Generates a self-signed CA on the control node and distributes it to all nodes.
Idempotent: if `ca.key` and `ca.crt` exist and match the requested inputs, they
are not regenerated.

## Inputs

- `ca_dir` (default `pki/ca/` at repo root)
- `ca_dist_dir` (default `/etc/pki/pigsty`, derived from `pigsty_pki_dir`)
- `ca_common_name` (default `pigsty-lite CA - <cluster_name>`)
- `ca_valid_days` (default 3650)
- `ca_key_size` (default 4096)

## Outputs on disk

**Control node:**
- `{{ ca_dir }}/ca.key` (0600)
- `{{ ca_dir }}/ca.crt` (0644)
- `{{ ca_dir }}/ca.csr` (0640)

**All nodes:**
- `{{ ca_dist_dir }}/ca.key` (0600)
- `{{ ca_dist_dir }}/ca.crt` (0644)
