# NHDW deploy: Ansible

An inventory-driven playbook that provisions hosts, installs the cluster and deploys NHDW on one node or a fleet, locally or over SSH. It is the Helm-based deployment path; `bundle/scripts/bootstrap.sh` is the kustomize-based one and runs the same stages on a single machine.

## Usage

```
cd 1_infrastructure/ansible
ansible-galaxy collection install -r requirements.yml       # community.general, ansible.posix
ansible-playbook site.yml                                   # single node (inventory/single-node.yml, the default)
ansible-playbook -i inventory/fleet.yml site.yml            # fleet (copied from fleet.example.yml)
```

The control node needs `ansible-core` (the disabled `provision.yml` workflow installs `ansible-core>=2.16`) plus the collections in `requirements.yml`. Target hosts are Ubuntu 22.04 or 24.04 with sudo for the deploy user.

Moving from one node to a fleet changes only the inventory. Its `nhdw_topology: single|fleet` selects the Helm values profile, `helm/nhdw/values-single.yaml` or `values-fleet.yaml`. See `inventory/README.md`.

## Layout

| Path | Purpose |
|---|---|
| `site.yml` | The playbook: two plays, per-node provisioning, then a single run from the control plane. |
| `ansible.cfg` | Default inventory, roles path, SSH pipelining. |
| `requirements.yml` | Ansible collections the roles use. |
| `group_vars/all.yml` | Shared variables: repo-relative paths, runtime, k3s version, GPU switch, chart-owned ConfigMaps. |
| `inventory/` | `single-node.yml` and `fleet.example.yml`. |
| `roles/` | `provision`, `cni`, `images`, `deploy`, `seed`, `verify`. |

## Roles

| Role | Runs on | What it does | Uses |
|---|---|---|---|
| `provision` | every node | Selected by `nhdw_runtime`. `k3s` (default): installs base packages, kernel modules and sysctls, Docker, the k3s server or agent, kubeconfig, kubectl, helm, kustomize, the `host.k3d.internal` CoreDNS alias and, with `nhdw_gpu`, the signed nvidia module and container toolkit. `k3d`: legacy laptop form that creates a k3d cluster from pre-installed tools. | `tasks/k3s.yml`, `tasks/k3d.yml` |
| `cni` | every node | Prepares the BPF and cgroup2 mounts, imports the bundled Cilium images, installs Cilium and Hubble, and repairs stale CiliumNode addresses after a node IP change. | `bundle/charts/cilium-*.tgz`, `files/cilium-values.yaml` |
| `images` | every node | Loads pre-built image tars; when none exist (or `nhdw_rebuild_images` is set) builds the `nhdw/*` images from source and pulls the public ones. | `build-images.sh`, `cache-images.sh` |
| `deploy` | control plane, once | Creates the namespace, renders secrets and TLS, applies the external-file ConfigMaps and Secrets, deletes finished chart Jobs, adopts pre-existing objects into the release, runs `helm upgrade --install`, applies metrics-server, waits for postgres-vault and the Keycloak seed Job, reconciles the Superset metadata role, and restores packed runtime state. | `gen-secrets.sh`, `gen-tls.sh`, `adopt-into-helm.sh`, `gpu-detect.sh`, `restore-state.sh`, the Helm chart |
| `seed` | control plane, once | Checks that `host.k3d.internal` resolves in the cluster, brings up the external source replicas, runs the data seed through the pipeline and the app and security seed, installs the profile-swap worker, regenerates the OPA policy, restarts the gateway, then runs the docs, ML, projects, resource-request, model-project and graduation seeds. | `sources-up.sh`, `seed-*.sh`, `regen-opa-policy.sh` |
| `verify` | control plane, once | Waits for every Deployment to be Available, probes each ingress host over HTTPS, asserts the gateway enforces OPA, and runs the regression gate. | `verify-system.sh` |

The roles call the scripts in `bundle/scripts/` and do not reimplement seeding or the pipeline. The `seed` role follows the order of bootstrap.sh steps 6 and 7; a seed step added to bootstrap.sh must be added there too.

## Chart and overlay split

The Helm chart renders every workload, Service, RBAC object and Ingress plus the inline ConfigMaps (OPA, Trino, observability). It does not render the ConfigMaps generated from files outside the chart, or the Secrets. The `deploy` role builds the dev kustomize overlay and pipes it through `roles/deploy/files/select-external-config.py`, which keeps every Secret and every ConfigMap not listed in `nhdw_chart_owned_configmaps`, so Helm and `kubectl apply` never manage the same object.

## Configuration

| Variable | Default | Effect |
|---|---|---|
| `nhdw_runtime` | `k3s` | Provisioning path: `k3s` on the host or legacy `k3d`. |
| `nhdw_topology` | from inventory | `single` or `fleet`; picks the values profile. |
| `nhdw_host_base_inventory` | unset (falls back to `127.0.0.1.nip.io`) | Ingress host base passed to Helm as `hostBase` and probed by `verify`. |
| `nhdw_run_seed` | `true` | `false` deploys without seeding. |
| `nhdw_fresh_volumes` | `false` | Deletes every PVC in the namespace before deploying so init SQL runs from zero. Destroys all data; requires seeding. |
| `nhdw_load_images`, `nhdw_rebuild_images` | `true`, `false` | Skip image loading, or force a build from source. |
| `nhdw_gpu`, `nhdw_gpu_driver_branch` | `false`, `535-server` | GPU host setup. |
| `nhdw_node_ip` | primary IPv4 | k3s node IP on multi-NIC or private hosts. |
| `nhdw_profile`, `nhdw_profile_dir` | `et-v3`, `/opt/nhdw-profiles` | Country profile passed to the seed scripts. |

## Notes

- Every role is written to be idempotent; re-running the playbook converges an existing host.
- `helm` resolves at run time to `bundle/bin/helm` when the bundle carries it (air-gapped installs), otherwise to the system install.
- CI runs `ansible-lint` on this directory (`.github/workflows/test.yml`).
