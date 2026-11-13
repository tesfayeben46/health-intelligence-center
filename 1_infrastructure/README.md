# Infrastructure

Declarative definition of the platform on Kubernetes, plus the automation that
provisions a host and deploys it. All workloads run in one namespace, `nhdw`.

## Layout

| Path | Contents |
|---|---|
| `k8s/base/` | Kustomize base: every workload manifest (storage, security, gateway, BI, metadata plane, Airflow, observability), the HPA, and the configMapGenerators that package files from elsewhere in the repo (OPA policies, Keycloak realms, PostgreSQL init scripts, JupyterHub profiles). Also holds `metrics-server.yaml` and the one-shot pipeline Job manifests, which are not in the base resource list; the Keycloak seed Job (`keycloak-seed-job.yaml`) is. |
| `k8s/overlays/dev/` | The overlay a single-host install applies: base plus Secrets (rendered from `secrets.yaml.template`), Ingress routes, the registry-to-local `images:` remap and environment patches. |
| `k8s/jobs/` | Kustomization over the one-shot pipeline Jobs (`dbt-jobs.yaml`, `ingestor-jobs.yaml`, `mdm-jobs.yaml`) with the same image remap; applied one Job at a time by the seed scripts. |
| `helm/nhdw/` | Helm chart rendering the same workloads, Services, RBAC and Ingress, with `values-single.yaml` and `values-fleet.yaml` for the two topologies. See its README. |
| `ansible/` | Playbook and roles that provision a host (k3d or k3s), install the CNI, import images, deploy the chart, seed data and verify. See its README. |
| `gitops/` | Argo CD `AppProject` and `Application` that sync the Helm chart. See its README. |

## Which path uses which

| Entry point | Renders with | Notes |
|---|---|---|
| `bundle/scripts/bootstrap.sh` | Kustomize (`k8s/overlays/dev`) | `kustomize build --load-restrictor=LoadRestrictionsNone k8s/overlays/dev \| kubectl apply -f -`, then `metrics-server.yaml`. |
| `ansible/site.yml` | Helm (`helm/nhdw`) | `helm upgrade --install` with the topology's values file. Secrets and the file-backed ConfigMaps still come from `kustomize build` of the dev overlay, filtered to the objects the chart does not own. |
| `gitops/argocd/` | Helm (`helm/nhdw`) | Argo CD syncs the chart; Secrets and file-backed ConfigMaps must already exist in the cluster. |

The kustomize tree and the Helm chart define the same workloads. They are kept
in step by hand: a change to a manifest in `k8s/base/` needs the matching
change in `helm/nhdw/templates/`, and the reverse.

## Images

Manifests reference images as `nhdw-registry:5500/nhdw/<name>:<tag>`. The dev
overlay and `k8s/jobs/` remap those to the locally built `nhdw/<name>:1.0.0`
images with `imagePullPolicy: IfNotPresent`. Some images are referenced from
environment variables (for example `ML_RUNNER_IMAGE` on Airflow and the
gateway) because a kustomize `images:` remap cannot reach them; the dev overlay
patches those variables separately.

## Notes

- `--load-restrictor=LoadRestrictionsNone` is required: the configMapGenerators
  read files outside the kustomize root (`3_storage/`, `6_access-points/`,
  `7_security/`, `bundle/seed-pack/`).
- PostgreSQL init scripts run in filename order on a fresh volume only, so a
  new init file must also be listed in the `postgres-vault-init` generator.
- Host bring-up scripts (bootstrap, source replicas, seeding, image build and
  cache) live in `bundle/scripts/`; they apply this tree but are not part of it.
