# Monitoring: Grafana dashboard definitions

This directory holds Grafana provisioning files and dashboard JSON. Grafana is
not deployed by the default manifests: neither `1_infrastructure/k8s/` nor the
Helm chart contains a Grafana workload. These files are dashboard definitions
for an operator who runs Grafana against the platform's Prometheus and Loki.

The deployed observability stack (Prometheus, Loki, Alertmanager, Promtail)
lives in `1_infrastructure/k8s/base/observability/` and in the Helm template
`1_infrastructure/helm/nhdw/templates/observability.yaml`. Prometheus's scrape
configuration and alert rules are in the `prometheus-config` ConfigMap in
`observability/prometheus.yaml`.

## Layout

| Path | Contents |
|---|---|
| `grafana/provisioning/datasources/datasources.yml` | Prometheus (`http://prometheus:9090`, default) and Loki (`http://loki:3100`) datasources. Both hostnames match the in-cluster Services in namespace `nhdw`. |
| `grafana/provisioning/dashboards/dashboards.yml` | File provider loading dashboards from `/var/lib/grafana/dashboards` into folder `NHDW`. |
| `grafana/dashboards/audit-events.json` | **Audit Events**: FHIR AuditEvent log stream from Loki. Events per minute by `app`, stat tiles (events in window, distinct users, apps emitting) and the raw event stream. |
| `grafana/dashboards/ewars-pilot-synthetic.json` | **EWARS Pilot (SYNTHETIC)**: alarm signal, alarm probability and woreda-by-week heatmap read from the `ewars` schema in PostgreSQL (`ewars_predictions`, `pilot_woreda_crosswalk`, `synthetic_outbreaks`). No init script or seeder in this repository creates that schema, so the dashboard shows no data on a fresh deployment. |
| `.env.example` | Bootstrap admin credentials for a Grafana instance. |

## Usage

Mount `grafana/provisioning/` at `/etc/grafana/provisioning/` and
`grafana/dashboards/` at `/var/lib/grafana/dashboards/` in a Grafana container
running in the `nhdw` namespace (or anywhere the `prometheus` and `loki`
hostnames resolve to those Services).

## Notes

- The dashboards reference datasources by UID (`loki` for Audit Events, a
  PostgreSQL datasource for EWARS). The provisioning file does not set UIDs and
  defines no PostgreSQL datasource, so these must be aligned when Grafana is
  deployed.
- The PostgreSQL init scripts create a Grafana database
  (`3_storage/postgres/init/08_grafana_db.sh`, password key
  `GRAFANA_DB_PASSWORD` in `postgres-vault-secrets`) for use as Grafana's
  backing store.
- Pod-level metrics: Prometheus's generic `kubernetes-pods` job scrapes any pod
  annotated `prometheus.io/scrape`, so a new exporter sidecar needs no
  Prometheus configuration change.
