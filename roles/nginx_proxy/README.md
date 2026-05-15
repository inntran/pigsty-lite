# nginx_proxy

The single public inbound for monitoring UIs. Targets the `monitor`
group (one host). Terminates TLS and reverse-proxies `/grafana/`,
`/alertmanager/`, and `/vmalert/` to their loopback-bound backends.

## Inputs (from response file, via group_vars)

| Variable | Meaning | Default |
|---|---|---|
| `nginx_proxy_tls_mode` | `ca_signed` \| `byo` \| `http` | `ca_signed` |

## What this role owns

- nginx on `network_any_address:80,443` (firewalled to `operator_cidrs`).
- `/etc/nginx/conf.d/pigsty-lite.conf` - the reverse-proxy server block.
- The `http` + `https` firewalld openings.

## What this role does NOT own

- The backends - Grafana / Alertmanager / vmalert are owned by their
  own roles and bind loopback-only.
- Certificate issuance - `ca_signed` mode reuses the P0 `certs` role's
  per-host cert; `byo` mode uses an operator-supplied cert.

## Ordering

`_assert` -> `_install` -> `_tls` -> `_config` -> `_firewall` ->
`_service`.

## Idempotence

Second run is zero-change: package present, config content-templated
and nginx configuration-validated, firewalld rules content-compared.

## Tags

- `nginx_proxy` - full role
- `nginx_proxy,config` - re-render the server block only
- `nginx_proxy,firewall` - firewalld only
- `nginx_proxy,service` - restart nginx only
