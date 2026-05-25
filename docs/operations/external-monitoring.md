# External monitoring

pigsty-lite supports three monitoring modes:

- `self_hosted`: deploys the built-in VictoriaMetrics, VictoriaLogs, vmalert,
  Alertmanager, Grafana, and nginx proxy stack on the `monitor` host.
- `external_push`: keeps local agents on every node and pushes metrics/logs to
  an external VictoriaMetrics/VictoriaLogs-compatible service.
- `external_pull`: exposes an authenticated nginx metrics frontend on every
  node so an external scraper can pull exporter metrics.

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

`external_pull` opens one nginx HTTP frontend per node. The frontend is
basic-auth protected, optionally TLS-enabled, and source-restricted by CIDR.

```yaml
monitoring:
  mode: external_pull
  external_pull:
    metrics_port: 9965
    auth:
      username: pigsty
    source_cidrs: ["10.0.0.0/8"]
    tls: true
```

The external scraper uses these paths:

```text
/metrics/node
/metrics/postgres
/metrics/pgbouncer
/metrics/pgbackrest
```

`/metrics/node` exists on every host. The postgres, pgbouncer, and pgBackRest
paths exist on postgres hosts. The basic-auth password is stored in the vault
as `vault_monitoring_pull_password`.

## Generated snippet

Interactive `./configure` writes `responses/external-monitoring.md` whenever
`monitoring.mode` is external. That file contains deployment-specific target
hostnames, ports, paths, and vault-key placeholders. Regenerate it after
changing response-file monitoring settings.
