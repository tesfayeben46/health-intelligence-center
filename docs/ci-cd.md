# Continuous integration and deployment

## Tests (`.github/workflows/test.yml`)

Runs on GitHub-hosted runners for every branch push except `main` and for pull requests. It needs no secrets and never contacts a deployment.

| Job | Checks |
|---|---|
| `policy` | `opa test` over the compiled policies |
| `data-units` | MDM matcher regression suite against Postgres with `pg_trgm`; security-seed loader provenance contract; reference-vocabulary build; widget-to-mart lineage contract; country guard; rebuild guards; forecast-model contract |
| `gateway` | the application imports cleanly; masking primitives |
| `mdm` | MDM workspace reads and maker-checker approvals against Postgres 16 and Trino 476 with synthetic fixtures |
| `admin-ui` | `tsc` and `vite build` for the admin console |
| `scripts` | every bundle script parses; workflow files are valid YAML; `ansible-lint` on the provisioning playbook; the database initialises from zero |

## Deployment workflows (`.github/disabled-workflows/`)

Two workflows automate deployment from a self-hosted runner on the target host. They are kept outside `.github/workflows/` so they do not run from this repository.

| Workflow | Trigger | Does |
|---|---|---|
| `deploy.yml` | push, or manual dispatch with a path scope | Rebuilds only the changed images and imports them into k3s, applies changed Postgres init DDL, reloads changed reference vocabulary, regenerates the OPA policy behind an `opa test` gate, reruns the dbt chain from the earliest affected stage through the marts, reruns the MDM matcher when needed, restarts affected workloads after their policy, and verifies |
| `provision.yml` | manual dispatch | From bare hosts: runs the Ansible playbook (`single-node` or `fleet` inventory) to install k3s, deploy the Helm chart and run the full seed |

The redeploy logic is `bundle/scripts/ci-redeploy.sh`, which can also be run by hand (`ci-redeploy.sh all`).

**Runner on the host.** The image set is about 14 GB and images are imported into containerd instead of pulled, so building on the host avoids a registry round trip. A multi-host fleet would add a registry push to `build-images.sh` and a pull-through cache.

**What a push redeploys.** The application tier (changed images, catalogue and role seeds, OPA policy) and the data tier (init DDL, vocabulary, dbt chain, MDM). It does not reload source dumps, rerun ingestors, add new databases (initialised at first boot only) or distribute new secret keys; those happen at provisioning. A run fails on any failed build, DDL apply, reseed, policy regeneration, rollout or pipeline Job.

**Manifests are checked, not applied.** The committed overlay uses the local host base; a hosted deployment's host base comes from provisioning. `deploy.yml` therefore verifies that every Deployment and StatefulSet the manifests declare exists on the cluster, and fails naming any that do not. Manifest changes are applied with `provision.yml`.

**Recovering a failed deploy.** A push diffs only its own commits, so re-apply a failed run's changes with a scoped dispatch (`scope="<paths>"`) or `scope=all`.

### Enabling them

1. Register a self-hosted runner on the target host with the labels `nhdw` (deploy) and `nhdw-control` (provision). It runs as the user that owns the kubeconfig and is in the `docker` group, with passwordless sudo for `k3s ctr`.
2. Set repository variables `NHDW_HOSTBASE` and `NHDW_SECRETS_FILE` (path to the host's rendered `secrets.yaml`), and secrets `NHDW_KUBECONFIG_PATH`, `NHDW_SECRETS_YAML` (base64 of `secrets.yaml`, required by `provision.yml`) and `NHDW_SSH_KEY` (fleet provisioning only).
3. Protect the deploying branches.
4. Move the files into `.github/workflows/`.

Source seed data and LLM weights are not in git. `provision.yml` runs `bundle/scripts/hydrate-data.sh`, which writes `secrets.yaml` from `NHDW_SECRETS_YAML` and downloads the HCMIS and mBrana backups from a GitHub release of the repository named by `GH_REPO` (`DATA_RELEASE`, default `data-v1`); the other restore artifacts must already be on the host, and the LLM weights are pulled at bring-up.
