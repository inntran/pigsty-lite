# Day-2 monitoring

The monitoring stack is declarative. Most changes go through the
response file's `monitoring:` block, then `make deploy`. To target only
the monitoring step, pass `--tags monitoring`.

## Add an Alertmanager receiver

```yaml
monitoring:
  alertmanager:
    receivers:
      - name: default
        type: slack
        webhook: !vault | ...
      - name: pager            # added
        type: pagerduty
        url: !vault | ...
```

`make deploy` re-renders `/etc/alertmanager/alertmanager.yml` and
restarts Alertmanager.

## Change retention

```yaml
monitoring:
  vmsingle_retention: 180d    # was 90d
  vlsingle_retention: 60d     # was 30d
```

`make deploy` updates the vmsingle/vlsingle service args. Note that
shrinking retention does not immediately reclaim disk - VictoriaMetrics
expires data lazily.

## Change the scrape interval

```yaml
monitoring:
  scrape_interval: 30s        # default is 15s
```

`make deploy` re-renders every node's vmagent scrape config.

## Add an alert rule

Alert rules live in `roles/monitoring_server/` and are shipped to
`/etc/vmalert/rules/` on the monitor host. To add a rule group, drop a
new `*.yml` file into the rules directory via a small custom play, or
extend the starter rule block in `roles/monitoring_server/tasks/main.yml`.
vmalert picks up rule files matching `*.yml` and reloads on the
evaluation interval.

## Add a Grafana dashboard

Drop a dashboard JSON into `roles/grafana/files/dashboards/` and
`make deploy --tags monitoring`. The file provider picks up new
dashboards within 30 seconds; no Grafana restart needed.

## Common gotchas

- **Exporter package missing on RHEL 10**: `pgbackrest_exporter` in
  particular may not have a clean RPM - see
  `roles/monitoring_agents/tasks/_exporters.yml` for the install
  source. If an exporter unit is failed, `journalctl -u <exporter>`
  shows why.
- **Metrics not arriving at the monitor**: check the monitor host's
  firewalld - `victoriametrics` (8428) and `victorialogs` (9428) must
  be open to the postgres source group. `vmagent`'s disk buffer fills
  if the monitor is unreachable; it drains when connectivity returns.
- **Grafana 404 behind the proxy**: the nginx proxy strips `/grafana/`
  before forwarding to Grafana. The `grafana` role sets `root_url` to end in
  `/grafana/` while keeping `serve_from_sub_path` false; a hand-edited
  `grafana.ini` reverts on the next deploy.
- **AVC denial from nginx**: nginx proxying to loopback upstreams needs
  the `httpd_can_network_connect` SELinux boolean - the `nginx_proxy`
  role sets it. If you see an AVC, confirm the boolean is on.
