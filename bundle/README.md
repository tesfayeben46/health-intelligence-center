# bundle

Everything needed to bring the platform up on a single host and seed it: the bring-up and seeding scripts, the country profiles, the seed data and seed packs, the ML packages, the role definitions, and the host-run operator console. The manifests, images and services themselves live in the numbered top-level directories; `bundle/` is what turns them into a running, populated deployment.

All data seeded from here is demonstration data (see [`docs/data-provenance.md`](../docs/data-provenance.md)).

## Layout

| Path | Contents |
|---|---|
| `scripts/` | Bring-up, seeding, packing, CI redeploy and verification scripts (see below) |
| `profiles/` | Country profiles `et-v3` (Ethiopia, default), `zm-v1` (Zambia), `gh-v1` (Ghana, presentation only): branding, copy decks, geography, widget catalogue overrides, and pointers to each country's users and entity model. See [`docs/profile-swappability.md`](../docs/profile-swappability.md). |
| `seed-pack/` | Application seed data with a versioned manifest: security model and sample users, newsroom articles, programme sample figures, workshop groups, ML packages, sample community projects ([README](seed-pack/README.md)) |
| `seeds/` | Data-layer seeds: the shared demonstration entity model and its generator (`entity-model/`), the reference vocabulary generator (`ref-vocab/`), source seed CSVs (`sources/`, not in git) and the operator-console scenarios (`demo/`) |
| `roles/` | Per-role data permissions, the source of the OPA policy ([README](roles/README.md)) |
| `datasets/` | Descriptive dataset registry ([README](datasets/README.md)) |
| `demo-console/` | The operator console: a stdlib-only Python server plus a React SPA, run on the host (`demo-console/run.sh`, port 8500). It lists sample pages, scenarios and credentials, and drives rebuild, teardown and profile switches. |
| `systemd/` | `nhdw-profile-swap.service`, the host worker that executes profile switches requested through the console |
| `charts/` | Bundled Helm chart for the Cilium CNI, installed offline at cluster creation |
| `docs/` | Copy of the served operator documentation, refreshed by `scripts/sync-docs.sh` and published by `scripts/seed-docs.sh` |
| `source-replica-conventions.md` | Conventions for the source replicas under `0_external/sources/` |

Generated or release-only directories are not in git: `images/` (image tars for offline boot), `bin/` (helm and mc binaries), `state/` (runtime state exported by `pack.sh`) and `tls/` (the local CA).

## Bring-up order

`./bootstrap.sh` at the repository root runs `scripts/bootstrap.sh`, which calls these scripts in order:

| Step | Script | What it does |
|---|---|---|
| 3 | `build-images.sh`, `cache-images.sh` | Load the bundled image tars into k3d (or build `nhdw/*` and pull public images when no tars are present) |
| 4 | `gen-secrets.sh` | Render the dev overlay's `secrets.yaml` from the template with random values, never overwriting an existing file |
| 4 | `gen-tls.sh` | Issue a locally trusted wildcard certificate for the ingress hostnames |
| 4 | `gpu-detect.sh` | Choose the assistant model from the weights the external ollama serves (14b, else 3b) and set it on the gateway |
| 5.5 | `restore-state.sh` | Restore packed runtime state from `state/`, if present |
| 5.7 | `seed-ref-vocab.sh` | Load the `nhdw_ref` vocabulary, required before the vault build |
| 6 | `sources-up.sh` | Bring up and seed the external source replicas, Fayda and the LLM |
| 6 | `seed-realistic.sh` | Run the ingestors and the dbt pipeline (lake, ClickHouse, bridge, vault, marts); skipped by `--fast` when a derived snapshot was restored |
| 7 | `seed-app-security.sh` | Load the permission model, users, articles, workshop groups and programme figures into `nhdw_ops` |
| 7.1 | `regen-opa-policy.sh` | Compile the OPA policy from the permission store, gate it with `opa test`, publish and reload |
| 7.05 | `seed-docs.sh` | Publish the operator docs to the `nhdw-docs` object-store bucket |
| 7.5 | `seed-ml.sh` | Train the platform ML packages, register them in MLflow and write first predictions |
| 7.55 | `seed-projects.sh` | Create sample workshop projects and generated datasets through the gateway API |
| 7.57 | `seed-resource-requests.sh` | Create a pending and an approved compute resource request through the gateway API |
| 7.58 | `seed-model-projects.sh` | Adopt each platform model as an approved workshop project |
| 7.6 | `seed-graduation.sh` | Drive the three sample community projects through the review flow |
| 7.8 | `seed-openmetadata.sh` | Seed the OpenMetadata catalogue (non-fatal) |
| 7.9 | `verify-system.sh` | Fast regression gate over the platform contracts (`SKIP_VERIFY=1` bypasses it) |

Between steps 7.1 and 7.05 the gateway is restarted so its OPA self-test runs against the seeded catalogue; step 7.85 runs the data-quality detector once. Every seeder is idempotent, so bootstrap can be re-run on a warm cluster.

Four data inputs are distributed separately rather than committed: the HCMIS, mBrana and EthioEMR restore artifacts (`0_external/sources/*/sample-data/`) and the MFR landing CSVs (`seeds/sources/mfr/`). The step 6 scripts print a warning and continue without them; the affected sources and marts stay empty. See `docs/data-provenance.md`.

```bash
./bootstrap.sh                       # full rebuild: sources -> ingestors -> pipeline -> seeds
./bootstrap.sh --fast                # restore the packed derived snapshot instead of re-running the pipeline
./bootstrap.sh --rebuild-images      # rebuild the nhdw/* images from source first
./bootstrap.sh teardown              # delete the cluster and stop the source replicas (volumes kept)
./bootstrap.sh teardown --wipe-sources   # also delete the source volumes (factory reset)
```

## Other scripts

| Script | Purpose |
|---|---|
| `ci-redeploy.sh` | Change-driven redeploy of a running k3s host, called by the deploy workflow |
| `predeploy-check.sh` | Read-only pre-flight before a live deploy |
| `adopt-into-helm.sh` | Let the Helm chart take ownership of resources already on the cluster |
| `pack.sh` | Export live runtime state into `state/` (and optionally a release archive) |
| `fetch-system-images.sh` | Fetch the k3s system images an offline node needs |
| `hydrate-data.sh`, `publish-data-assets.sh` | Fetch, or publish to a release, the source dumps that are not in git |
| `export-source-seeds.sh` | Export landing tables as the source seed CSVs |
| `profile-swap-worker.sh` | Host worker behind the console's profile switch (run by the systemd unit) |
| `build-profile.py` | Compile a profile's `reference.json` from its entity model |
| `collect-catalog.py` | Collect the per-dashboard catalogue files into `catalog.json` for the security loader |
| `gen-exposures.py` | Derive dbt exposures from the gateway's widget registry |
| `check-dbt-sql.py` | EXPLAIN dbt models against the live warehouse before a deploy |
| `render-keycloak-seed-job.py` | Render the Keycloak seed Job for a deployment's host base |
| `sync-om-classification.py`, `sync-om-dashboards.py` | Synchronise column classifications and dashboard structure with OpenMetadata |
| `run-dbt-from-host.sh` | Run a pipeline image on the host against the cluster through port-forwards |
| `test-coldboot-initdb.sh`, `test-country-guard.sh`, `test-rebuild-guards.sh`, `test_gen_exposures.py` | Tests for database initialisation, the country guard, rebuild guards and the exposure parser |
| `seed-pack-engine/` | Seed-pack manifest validator (used by `seed-app-security.sh`) and engine |
| `lib/objstore.sh`, `lib-country-guard.sh`, `resolve-users-seed.sh` | Shared helpers: object-store access, packed-dump country trust, users-file resolution |

See [`docs/deployment.md`](../docs/deployment.md) for prerequisites, hosting and operations.
