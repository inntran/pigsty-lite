# haproxy

Local HAProxy on every postgres node. Three TCP frontends:

| Frontend | Port | Backend                          | Purpose                           |
|----------|------|----------------------------------|-----------------------------------|
| default  | 5432 | All members; HEALTH=`/leader`    | Generic; clients use this         |
| primary  | 5433 | All members; HEALTH=`/leader`    | Explicit RW (same as default)     |
| replica  | 5434 | All members; HEALTH=`/replica`   | Explicit RO; load-balanced        |

Health checks talk to Patroni REST (`/leader` returns 200 on the leader,
503 elsewhere; `/replica` returns 200 on a running replica). HAProxy
uses TLS for the health checks (Patroni REST requires it).

## Why both 5432 and 5433 go to the leader

Spec §3.1: "5432 → default = primary, 5433 → rw = primary only, 5434 → ro".
Apps that don't care about RW/RO use 5432. Apps that want to be explicit
use 5433 (RW) or 5434 (RO). Same backend health rule for default and
primary so an upgrade to "split RW/RO" doesn't require a config change
in the app.

## Backend target

`haproxy_backend_target: pgbouncer` (default) routes through the local
pgBouncer on 6432. Set to `postgres` to bypass pooling entirely (clients
hit PG on 5432 directly). The dynamic default in
`haproxy_backend_port` picks the right port.

## Stats

HTTP stats listen on `127.0.0.1:7000` (loopback only). Nginx_proxy (P5)
exposes them at `/haproxy-stats/` if you want a UI. Default credentials
in `defaults/main.yml`; override in the response file.

## SELinux

HAProxy on RHEL/Rocky/Alma must connect to arbitrary TCP ports across
hosts (the per-node Patroni REST on 8008). Enable
`haproxy_connect_any` SELinux boolean; the role does this for you.

## Reload not restart

Config changes trigger `systemctl reload`. HAProxy supports zero-drop
reload via socat / runtime API; the systemd unit handles the dance.
