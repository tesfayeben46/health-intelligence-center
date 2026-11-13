# Postgres (`postgres-vault`)

One Postgres cluster holds the Data Vault, the marts and the platform's operational state. Transformation code lives in `2_pipelines/`; this directory holds the database initialisation.

| Database | Contents |
|---|---|
| `nhdw_vault` (`POSTGRES_DB`) | landing area, raw and business vault, the `bi_*` mart schemas, reference vocabulary, read-only foreign tables over `nhdw_ops` |
| `nhdw_ops` | control plane: identity cache, workspace, governance, exports, artifacts, audit, permissions, ML run state, MDM and plan decision logs |
| `nhdw_grafana`, `nhdw_mlflow`, `nhdw_airflow`, `nhdw_superset`, `nhdw_openmetadata`, `seaweedfs_filer` | one database per platform service; each is owned by the admin user (`POSTGRES_USER`) and the service's role gets full privileges on it and owns its `public` schema |

The marts are Postgres schemas, not ClickHouse tables: the dbt marts project (`2_pipelines/marts/`, dbt-postgres) builds into `bi_*` schemas in `nhdw_vault`, and Trino's `marts` catalog reads them as `bi_reader`. ClickHouse is used only for staging (`3_storage/clickhouse/README.md`).

## Initialisation

`init/` is mounted at `/docker-entrypoint-initdb.d` through the `postgres-vault-init` ConfigMap (`1_infrastructure/k8s/base/kustomization.yaml`), which lists every file except `18_security_seed.sh` and `29_project_review.sh`. The Postgres entrypoint runs the files once, in filename order, on an empty data directory; a failing file stops the sequence.

| Files | Creates |
|---|---|
| `00_users.sh`, `00_users.sql.template` | Roles `landing_writer`, `vault_writer`, `bi_reader` (passwords substituted from env). Roles only; the grants come in 01 once the schemas exist. |
| `01_schemas.sql` | In `nhdw_vault`: `nhdw_landing` (persistent staging area copied from ClickHouse by `2_pipelines/vault/bridge/`), `nhdw_vault_raw`, `nhdw_vault_business` (AutomateDV), the mart schemas `bi_facility`, `bi_supply`, `bi_epi`, `bi_public` and `bi_staging` (marts awaiting governance approval; `bi_reader` has USAGE on it, and the marts project's post-hook grants it SELECT on every model, including those in `bi_staging`), `fhir_resources` with the `fhir_writer` role for HAPI FHIR, the consent-revocation gate, the PHI fixture table, the submission inbox, and the default privileges that make every new mart readable by `bi_reader`. |
| `03_program_demo.sql` | `bi_programme` views and the programme demo tables they read; the demo rows are loaded from `bundle/seed-pack/app/program-demo/`. |
| `04_ops_db.sh`, `05_ops_schemas.sql`, `06_ops_users.sql.template` | The `nhdw_ops` database, its six schemas (`ops_identity`, `ops_workspace`, `ops_governance`, `ops_export`, `ops_artifact`, `ops_audit`) and the roles `ops_writer`, `ops_gateway`, `ops_reader`. `ops_audit` is append-only. |
| `07_marts_schemas_extras.sql` | Further mart schemas: `bi_hmis`, `bi_idsr`, `bi_service`, `bi_quality`, `bi_governance`, `bi_mdm`. |
| `08`, `09`, `20`, `21`, `23_seaweedfs_filer_db.sh`, `31`, `37` | Service databases and roles: Grafana, MLflow, the `keycloak` role and `keycloak_metadata` schema, Airflow, the SeaweedFS filer (with its `filemeta` table in `COLLATE "C"`), Superset, OpenMetadata. Each service runs its own migrations. |
| `10_mfr_landing.sql` | Landing tables for Master Facility Registry FHIR resources. Uses DROP + CREATE so a re-run applies the current columns (and empties the tables). |
| `11_ml_predictions.sh`, `27_ml_monitoring.sh` | `ops_ml`: run log, flat forecast store, deployment registry, drift and performance tables. |
| `12`, `13`, `14`, `16`, `22`, `26`, `28`, `29_project_review.sh`, `33` | Workspace and governance additions in `nhdw_ops`: resource requests, artifact validation and promotion log, public access-request columns, saved-query schedules, projects, project scoping of artifacts, dataset-to-project links, project review, group invitations and join requests. `29_project_review.sh` is not in the Kubernetes ConfigMap; `bundle/scripts/seed-app-security.sh` applies it on every seed. |
| `17_perm_schemas.sql`, `19`, `23_perm_admin_grants.sql`, `25`, `32` | `ops_perm`: the dashboard / filter / widget permission model, page layout catalog, `mart_attrs` and `column_classification` used to build OPA input and masking, roles and grants, API tokens, role-level data scope, and the normalized permission store that `7_security/opa/scripts/compile.py --from-db` reads. |
| `18_security_seed.sh` | Runs `7_security/scripts/load_security_seed.py`. Not in the Kubernetes ConfigMap; there the seeding step runs the loader as a Job. |
| `24_public_article.sql`, `29_alert_routing.sql` | Public newsroom articles and admin-managed alert routing rules, each with seeded defaults that never overwrite runtime edits. |
| `30_demo_patient_phi.sql` | A generated (synthetic) patient-level fixture for the consent gate and the outlier widgets; see `docs/data-provenance.md`. |
| `34_ref_vocab.sql` | `nhdw_ref`: OMOP-style vocabularies, concepts and the source-to-concept crosswalk the vault resolution views join. Loaded by `bundle/scripts/seed-ref-vocab.sh`. |
| `35`, `39`, `44`, `45` | MDM decision tier in `ops_governance`: match candidates, the append-only bitemporal decision log, master-data releases, maker-checker proposals, the matcher's rule registry and run log. |
| `38_governance.sql` | DQ thresholds, flag fingerprints and the DQ incident history. |
| `42_pct_plan.sql` | `ops_plan.plan_decision`: append-only, bitemporal decision log for supply plan lines. |
| `36`, `40`, `41`, `43` | Read paths into `nhdw_vault`: a `postgres_fdw` server `nhdw_ops_ro` (created in 36) connecting as `ops_reader` over the local socket, with foreign tables in `nhdw_vault_governance` (MDM decisions, DQ record), `nhdw_vault_ml` (predictions and performance) and `nhdw_vault_plan` (plan decisions). The vault and mart builds read `nhdw_ops` state read-only through these. |
| `99_init_complete.sql` | Sentinel table `public.initdb_sentinel`. It exists only if every earlier file succeeded; the boot's wait-for-postgres step requires it. |

Ordering constraints worth knowing: grants on vault schemas are in 01 because 00 runs before the schemas exist; `ops_perm.role_scope` is granted in 25, not 17, because it is created there; the read-path files 40, 41 and 43 depend on the foreign server and user mappings created in 36. Two files share the prefix 23 and two share 29; filename order still decides.

## Re-running on an existing cluster

initdb never runs again on a populated data directory, so later changes are written as idempotent numbered files (`CREATE ... IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`, guarded `DO` blocks, `ON CONFLICT`) that can also be applied by hand to a running cluster. `bundle/scripts/seed-app-security.sh` re-applies two sets on every seed so that a `pg_restore --clean` of an older dump does not lose columns or grants:

- SQL: `17_perm_schemas.sql`, `23_perm_admin_grants.sql`, `25_perm_scope_migration.sql`, `32_permission_store.sql`, `38_governance.sql`;
- shell: `22_project.sh`, `26_artifact_project.sh`, `28_dataset_project_link.sh`, `29_project_review.sh`, `27_ml_monitoring.sh`, `33_workshop_group_flows.sh`.

`10_mfr_landing.sql` is the exception to additive re-runs: it drops and recreates its landing tables.

## Roles

| Role | Used by | Access |
|---|---|---|
| `vault_admin` | bootstrap and admin scripts | superuser |
| `landing_writer` | ClickHouse-to-Postgres bridge | DDL/DML on `nhdw_landing` |
| `vault_writer` | dbt vault and marts builds (AutomateDV) | DDL/DML on the vault schemas, owns the `bi_*` schemas, SELECT on landing |
| `bi_reader` | Trino `marts` catalog and BI clients | SELECT on the business vault and the `bi_*` schemas, including the dbt-built models in `bi_staging` |
| `ops_writer`, `ops_gateway`, `ops_reader` | services, the data gateway, read-only clients (Trino `ops` catalog, the foreign tables) | see `06_ops_users.sql.template` |
| per-service writers | Grafana, MLflow, Airflow, Superset, OpenMetadata, Keycloak, SeaweedFS filer | own database or schema only |

Passwords come from the `postgres-vault-secrets` Secret in Kubernetes (`.env` for a local container; template in `.env.example`).

## Scripts

`scripts/verify.sh` is a smoke test for a local container named `postgres-vault`: it checks the admin login, the `nhdw_*` vault schemas, `landing_writer` DDL, `vault_writer` reads, and that `bi_reader` is denied on `nhdw_landing`. Its schema check still expects the original three `nhdw_*` schemas; the current init creates seven (`nhdw_landing`, `nhdw_ref`, `nhdw_vault_raw`, `nhdw_vault_business`, `nhdw_vault_governance`, `nhdw_vault_ml`, `nhdw_vault_plan`), so that check fails on a fresh init.
