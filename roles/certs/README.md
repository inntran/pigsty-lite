# certs

Issues per-host certificates signed by the pigsty-lite CA. The CA must already
exist on the control node (`roles/ca`).

## Flow

1. Ensure `/etc/pki/pigsty-lite/` exists on target.
2. Distribute `ca.crt` to target.
3. Generate host key on target.
4. Generate CSR on target; fetch back to control.
5. Sign CSR with the CA on control node.
6. Copy signed cert back to target.

## Inputs

- `certs_pki_dir` (default `/etc/pki/pigsty-lite`)
- `certs_validity_days` (default 730)
- `certs_subject_alternative_names` (auto-built; override only if needed)

## Idempotency

`community.crypto.openssl_privatekey`, `openssl_csr`, and `x509_certificate`
are idempotent by default. Renewal logic for certs within
`cert_renewal_window_days` is added in a later sub-plan; P0 leaves renewal to
manual re-run.
