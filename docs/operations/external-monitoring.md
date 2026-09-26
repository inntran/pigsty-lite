# External monitoring

pigsty-lite supports three monitoring modes:

- `self_hosted`: deploys the built-in VictoriaMetrics, VictoriaLogs, vmalert,
  Alertmanager, Grafana, and nginx proxy stack on the `monitor` host.
- `external_push`: keeps local agents on every node and pushes metrics/logs to
  an external VictoriaMetrics/VictoriaLogs-compatible service.
- `external_pull`: exposes an authenticated `exporter_exporter` endpoint on
  every node so an external scraper can pull exporter metrics.

## Push mode

`external_push` requires a metrics remote-write endpoint and a logs ingest
endpoint:

```yaml
monitoring:
  mode: external_push
  external_push:
    metrics_url: https://vm.example.com/api/v1/write
    logs_url: https://vl.example.com/insert/jsonline
    auth:
      username: pigsty
      bearer: false
    tls_skip_verify: false
```

Any service that accepts Prometheus remote-write for metrics and
VictoriaLogs JSON line ingestion for logs can be used. Basic-auth passwords
and bearer tokens are stored in the Ansible vault as
`vault_monitoring_external_password` and
`vault_monitoring_external_bearer_token`.

## Pull mode

`external_pull` opens one `exporter_exporter` endpoint per host, on port
`9999` by default. The endpoint proxies modules to exporters bound only to
loopback. Node metrics are available on every host; PostgreSQL, pgBouncer,
and pgBackRest metrics are available only on PostgreSQL hosts.

```yaml
monitoring:
  mode: external_pull
  external_pull:
    metrics_port: 9999
    source_cidrs: ["10.0.0.0/8"]
    tls: true
```

Scrape `/proxy` and select the exporter with the `module` query parameter.
For example, a Prometheus- or vmagent-style scraper can use:

```yaml
scrape_configs:
  - job_name: pigsty-node
    scheme: https
    metrics_path: /proxy
    params:
      module: [node]
    authorization:
      type: Bearer
      credentials: "<vault_monitoring_pull_token>"
    tls_config:
      ca_file: /path/to/pki/ca/ca.crt
    static_configs:
      - targets: ["node-1.example.com:9999"]
```

Use `module: [postgres]`, `module: [pgbouncer]`, or
`module: [pgbackrest]` in separate jobs for PostgreSQL hosts. The bearer
token is stored in the vault as `vault_monitoring_pull_token`; send it as
`Authorization: Bearer ...`. A missing or incorrect token returns `401`,
and a module not configured on that host returns `404`.

TLS is enabled by default, using the host's pigsty-lite CA-signed
certificate. The scraper must trust the CA (`pki/ca/ca.crt`). Set
`tls: false` only if the endpoint should use HTTP. The role opens the
endpoint port in firewalld only to the configured
`monitoring.external_pull.source_cidrs`. The token is stored in
`/etc/pigsty/monitoring/exporter_exporter.token` with mode `0640`, owned by
`root:exporter_exporter`; it is read from that file, not passed on the
command line.

## Upgrading from the nginx frontend

Before running `./configure`, remove `monitoring.external_pull.auth` from
the response file. The response schema rejects that block because the
endpoint now uses a vault bearer token. `./configure` copies an existing
`vault_monitoring_pull_password` value to `vault_monitoring_pull_token`;
the old key remains in the vault, and the old password becomes the bearer
token.

Existing response files retain their explicit `metrics_port` value, commonly
`9965`. Change it to `9999` if desired. When the configured port changes,
the role disables the legacy `9965` firewalld rule for the configured source
CIDRs. On deployment, the role removes
`/etc/nginx/conf.d/pigsty-metrics.conf` and
`/etc/nginx/pigsty-metrics.htpasswd`, then reloads nginx if it is running.
It does not remove nginx, which is still used by `roles/nginx_proxy`.

Update the external scraper too: replace `basic_auth` with bearer
`authorization`, and replace `/metrics/<module>` with `metrics_path:
/proxy` plus `params: {module: [<module>]}`. Configure its TLS trust for the
pigsty-lite CA. Regenerate the operator scrape notes from the updated
response file with `./configure -s -f <response-file>`.

## Generated snippet

Silent configure (`./configure -s -f <response-file>`) writes
`responses/external-monitoring.md` when the validated response file uses
`external_push` or `external_pull`. The interactive wizard writes only
`responses/site.rsp.yml`; it does not generate the monitoring notes. The file
contains deployment-specific target hostnames, ports, paths, and vault-key
placeholders. To regenerate it after changing monitoring settings, run silent
configure with the updated response file:
`./configure -s -f <response-file>`.
