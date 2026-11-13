# Deployment and operations

## Local bring-up (k3d)

`./bootstrap.sh` wraps `bundle/scripts/bootstrap.sh`, the canonical local bring-up:

```bash
./bootstrap.sh                          # full bring-up
./bootstrap.sh --rebuild-images         # rebuild service images from source first
./bootstrap.sh teardown                 # remove the cluster, keep source volumes
./bootstrap.sh teardown --wipe-sources  # also remove source data
```

A boot, in order:

1. creates the k3d cluster and installs Cilium;
2. loads cached image tars or builds the images;
3. generates secrets (`gen-secrets.sh`) and TLS (`gen-tls.sh`) and applies the kustomize overlay;
4. waits for Postgres and Keycloak, restores packed state if present, and builds the reference vocabulary;
5. starts the source replicas and seeds them (`sources-up.sh`);
6. runs ingestion and the dbt pipeline (`seed-realistic.sh`);
7. seeds the application state: identities, roles and the compiled OPA policy, documentation, ML models and predictions, workshop projects, model reviews, the OpenMetadata catalogue and a first data-quality run;
8. verifies workloads and endpoints (`verify-system.sh`).

The pipeline and application seed steps (6 onwards) stop the boot on failure. Source seeding (step 5) and the MFR landing load warn and continue: the replicas that depend on restore artifacts (HCMIS, mBrana, EthioEMR) need those files in `0_external/sources/*/sample-data/`, and the MFR marts need the landing CSVs in `bundle/seeds/sources/mfr/`; without them those sources and marts stay empty. See [data-provenance.md](data-provenance.md).

Prerequisites: Linux x86_64, Docker, k3d, and internet access on first run for image builds and tool downloads. An NVIDIA GPU is optional and only used by the assistant model.

## Hosted deployment (k3s)

`1_infrastructure/ansible/site.yml` deploys onto k3s, on one node or a fleet, from an inventory:

```bash
cd 1_infrastructure/ansible
ansible-playbook site.yml                                   # inventory/single-node.yml
ansible-playbook -i inventory/fleet.example.yml site.yml    # fleet
```

| Role | Does |
|---|---|
| `provision` | installs k3s (server and agents from the inventory), container runtime prerequisites, GPU support where present |
| `cni` | Cilium and Hubble |
| `images` | loads pre-built image tars |
| `deploy` | ConfigMaps and secrets, `helm upgrade --install` of `1_infrastructure/helm/nhdw`, metrics-server |
| `seed` | source replicas, pipeline, application and security seed, in bootstrap order |
| `verify` | workloads ready, every dashboard reachable, OPA enforcing |

The inventory sets the host base (`nhdw_host_base_inventory`) and the topology, which selects `values-single.yaml` or `values-fleet.yaml`. `1_infrastructure/gitops/argocd` holds an Argo CD application for the same chart.

The workflows in `.github/disabled-workflows/` automate both paths from a self-hosted runner on the target host (provision through Ansible, redeploy changed images on push). They are not active in this repository; their README lists what they need.

## Configuration that must agree

Single sign-on depends on one host base being used consistently by the ingress hosts, the Keycloak client redirect URIs, the token issuer and the gateway's accepted issuers. Locally that is `127.0.0.1.nip.io`. For a hosted deployment set it once in the inventory (Helm value `hostBase`) and pass the same value when the Keycloak seed runs (`render-keycloak-seed-job.py --hostbase`).

## Secrets

`bundle/scripts/gen-secrets.sh` renders `1_infrastructure/k8s/overlays/dev/secrets.yaml.template` into a gitignored `secrets.yaml`: `__G_*__` tokens become fresh random values, and `__REAL:NAME|default__` tokens take the environment value, else the value already in the existing file, else the default. External credentials (SMTP relay, Google OAuth client) are supplied through the environment.

## Packing and moving

`bundle/scripts/pack.sh` exports a running system's state into the bundle so a later boot reproduces it: each source replica in its native restore format, non-derivable application state (operational databases, the model registry, stored artifacts) under `bundle/state/`, and the served docs. By default pipeline-derived tiers are not packed and a boot recomputes them from the sources. `pack.sh --with-derived` also packs a derived snapshot, which `bootstrap.sh --fast` restores instead of running the pipeline.

## Observability

Prometheus scrapes the platform and Kubernetes metrics, Alertmanager routes alerts (e-mail when enabled), Loki with Promtail collects logs, and Hubble records network flows. The admin console's health and topology page draws the live service graph from these. Operator tools are reached through single sign-on and restricted to operator roles.
