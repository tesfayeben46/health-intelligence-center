# NHDW Helm chart

Helm packaging of the NHDW application tier. It renders the same workloads as
the kustomize tree (`1_infrastructure/k8s/base` plus `overlays/dev`), so that
single-node and fleet installs differ only by values file. The Ansible deploy
role and the Argo CD Application install this chart; `bundle/scripts/bootstrap.sh`
applies the kustomize tree instead. The two trees are kept in step by hand.

## Layout

| Path | Contents |
|---|---|
| `Chart.yaml` | Chart metadata (`nhdw`, appVersion `1.0.0`). |
| `values.yaml` | Defaults: image repository and tag, `hostBase`, Keycloak and Fayda bases, Alertmanager email switch, OpenMetadata toggle and tags, storage class, SeaweedFS sizing, per-component replicas and resources, country `profile`. |
| `values-single.yaml` | Single-node profile: one replica each, `local-path` storage. Pairs with `ansible/inventory/single-node.yml`. |
| `values-fleet.yaml` | Fleet profile: more replicas, resource requests, `nhdw-ssd` storage class. Pairs with `ansible/inventory/fleet.example.yml`. |
| `templates/` | One template per workload group: storage (postgres-vault, SeaweedFS, ClickHouse, Trino), security (OPA, Keycloak and its seed Job), gateway with RBAC and HPA, dashboards, BI and tools (Superset, dbt-docs, tool gates, Headlamp, JupyterHub, MLflow), metadata plane (OpenMetadata, OpenSearch), Airflow, the observability stack, and the Ingress. |
| `templates/_helpers.tpl` | `nhdw.image` (image ref from `image.repository`, component name and `image.tag`) and `nhdw.kcPublicBase` (Keycloak public URL derived from `hostBase`). |

## Usage

```bash
helm lint .
helm template nhdw . -f values-single.yaml                    # render only
helm upgrade --install nhdw . -n nhdw -f values-single.yaml   # single node
helm upgrade --install nhdw . -n nhdw -f values-fleet.yaml    # fleet
```

The namespace must already exist (the deploy role creates it), and the
external ConfigMaps and Secrets below must be present first.

## Configuration

- Images: `image.repository` and `image.tag`. Locally loaded images use
  repository `nhdw`; a registry install sets e.g. `registry.example.com/nhdw`.
- Hostnames: `hostBase` drives every Ingress host and OIDC redirect;
  `keycloak.publicBase` overrides the derived Keycloak URL.
- Storage: `storageClass`, plus `seaweedfs.*` for the object store's volume
  size, volume count limit and filer database.
- Scale: `components.<name>.replicas` and `.resources`.
- `alertmanager.email`: default `false`, which renders a null receiver so a
  fresh install sends no email even when SMTP credentials exist.
- `openmetadata.enabled`: set `false` on constrained machines.

## Objects the chart does not render

Helm's `.Files.Get` cannot read files outside the chart, so ConfigMaps built
from files elsewhere in the repo are referenced by name and created by the
deploy role, from `kustomize build` of the dev overlay filtered by
`ansible/roles/deploy/files/select-external-config.py`. The base sets
`disableNameSuffixHash: true`, so these names are stable.

| ConfigMap | Source |
|---|---|
| `postgres-vault-init` | `3_storage/postgres/init/*` |
| `opa-policies` | `7_security/opa/policies/*.rego` |
| `keycloak-realm-configs` | `7_security/keycloak/realm-configs/*.json` |
| `keycloak-seed-script` | `7_security/keycloak/scripts/setup-keycloak.py` |
| `keycloak-users-seed` | `bundle/seed-pack/app/security/users.yaml` |
| `clickhouse-users-d` | `3_storage/clickhouse/config/users.d/*.xml` |
| `workspace-profiles` | `6_access-points/jupyterhub/profiles.yaml` |

Inline ConfigMaps ship with the chart and are copied from the kustomize base
with only `metadata.namespace` templated: `opa-config`, `trino-catalog`,
`trino-etc`, `prometheus-config`, `loki-config`, `promtail-config`, and
`openmetadata-env` (defined in `templates/openmetadata.yaml`).
Alertmanager renders its config at pod start from `smtp-secrets` in an
initContainer.

Secrets are also external: the deploy role applies them from the dev
overlay's `secrets.yaml` (rendered from `secrets.yaml.template`), or a secret
store provides them. The chart references, among others, `postgres-vault-secrets`,
`keycloak-secrets`, `keycloak-idp-secrets`, `nhdw-gateway-secrets`,
`seaweedfs-secrets`, `minio-secrets` (historical name; SeaweedFS S3
credentials), `clickhouse-secrets`, `clickhouse-named-collections`,
`mlflow-secrets`, `superset-secrets`, `airflow-secrets`, `airflow-oidc-secrets`,
`jupyterhub-secrets`, `openmetadata-secrets`, `smtp-secrets`,
`alert-eval-secrets`, `dq-detect-secrets`, `app-signing-secrets`,
`demo-user-secrets`, `demo-control-secrets`, `headlamp-token` and the
`*-gate-secrets` for each tool gate.

Cluster-level objects outside the chart: `metrics-server` (needed by the
gateway HPA) and the wildcard TLS Secret `nhdw-wildcard-tls`
(`bundle/scripts/gen-tls.sh`). The one-shot pipeline Jobs
(`k8s/base/dbt-jobs.yaml`, `ingestor-jobs.yaml`, `mdm-jobs.yaml`) are run by
the seed step, not by the chart.

## Checking parity with kustomize

```bash
kustomize build --load-restrictor LoadRestrictionsNone 1_infrastructure/k8s/overlays/dev > ks.yaml
helm template nhdw 1_infrastructure/helm/nhdw -f 1_infrastructure/helm/nhdw/values-single.yaml > hm.yaml
```

Compare by (kind, namespace, name). Objects present only in the kustomize
output should be the external ConfigMaps and Secrets listed above.
