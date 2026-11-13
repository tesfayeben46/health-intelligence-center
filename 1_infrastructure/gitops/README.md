# GitOps (Argo CD)

Optional continuous reconciliation of the application tier after the initial
bootstrap. Argo CD syncs the same Helm chart the Ansible deploy role installs
(`1_infrastructure/helm/nhdw`), so a merge to the tracked revision becomes a
declarative upgrade and drift in the cluster is corrected automatically. It
does not replace the bootstrap.

## Layout

| File | Contents |
|---|---|
| `argocd/appproject.yaml` | `AppProject` `nhdw`: the allowed source repository and the `nhdw` destination namespace. |
| `argocd/nhdw-application.yaml` | `Application` `nhdw`: chart path, values file, automated sync with self-heal and prune, `CreateNamespace` and `ServerSideApply`, retry backoff, and an `ignoreDifferences` on the gateway's `spec.replicas`. |

## Boundary

| Reconciled by Argo CD (rendered by the chart) | Not reconciled (bootstrap prerequisites) |
|---|---|
| Deployments, StatefulSets, Services, RBAC, Ingress | The external-file ConfigMaps and all Secrets (deploy role or a secret store) |
| Inline ConfigMaps (OPA, Trino, observability config) | Seed data (a one-time load) |
| The gateway HPA | The cluster and CNI (provision and cni roles) |

The chart never renders the external ConfigMaps or Secrets, so Argo CD never
modifies them. See the chart README for the full list.

## Flow

```
  Ansible bootstrap (once)                 Argo CD (continuous)
  ────────────────────────────             ──────────────────────────────
  provision -> cni -> images                 watches the tracked revision
  deploy: configMaps + secrets             reconciles 1_infrastructure/helm/nhdw
          + helm install (first sync)  ──▶  self-heals drift, applies chart upgrades
  seed: source -> pipeline -> app
  verify
```

## Install

1. Install Argo CD into the `argocd` namespace (not included here).
2. In `argocd/appproject.yaml` and `argocd/nhdw-application.yaml`, set
   `repoURL` and `sourceRepos` to the repository, and for production pin
   `targetRevision` to an immutable tag or commit instead of `main`.
3. Apply:
   ```bash
   kubectl apply -f argocd/appproject.yaml
   kubectl apply -f argocd/nhdw-application.yaml
   ```
4. For a fleet install, change the Application's `helm.valueFiles` to
   `values-fleet.yaml`.

## Notes

- `prune: true` deletes objects removed from the chart; review it before
  enabling on a shared cluster.
- `ignoreDifferences` on the gateway Deployment's replicas leaves that field to
  the HPA.
- Pinning `targetRevision` to a tag, together with versioned image tags, makes
  every sync an explicit, reviewable change.
