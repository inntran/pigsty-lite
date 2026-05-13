# node

Baseline node OS configuration: hostname, `/etc/hosts` from inventory, sysctl
tuning, journald sizing, firewalld baseline (ssh open).

## Inputs

- `node_firewalld_baseline_services` (default `[ssh]`)
- `node_sysctl` (dict of key/value pairs)
- `node_journald_max_use` (default `1G`)

## Tags

None.
