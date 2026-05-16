# ca

Generates a self-signed CA on the control node. Idempotent: if `ca.key` and
`ca.crt` exist and match the requested inputs, they are not regenerated.

Distribution to cluster nodes is the `certs` role's job — it reads the CA
material from `{{ certs_ca_dir_on_control }}` (set by the caller to point at
the control-node `ca_dir`) and copies `ca.crt` plus signs each host's
per-node cert from it.

## Inputs

- `ca_dir` (default `pki/ca/` at repo root) — where the CA material lives on the control node
- `ca_common_name` (default `pigsty-lite CA - <cluster_name>`)
- `ca_valid_days` (default 3650)
- `ca_key_size` (default 4096)

## Outputs on disk (control node only)

- `{{ ca_dir }}/ca.key` (0600)
- `{{ ca_dir }}/ca.crt` (0644)
- `{{ ca_dir }}/ca.csr` (0640)
