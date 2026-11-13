# Security and permission seeds

The YAML files here seed the platform's permission model and identity catalogue in `nhdw_ops`. They are loaded by `7_security/scripts/load_security_seed.py`, which `bundle/scripts/seed-app-security.sh` runs as a Kubernetes Job at boot (bootstrap step 7). The users listed are fictional sample accounts.

The dashboard catalogue (dashboards, pages, sections, widgets and their placements and filters) is not authored here. It lives next to each dashboard's code under `6_access-points/dashboards/<dashboard>/catalog/` and in a `widget.yaml` beside each widget, and `bundle/scripts/collect-catalog.py` collects it into `catalog.json` before the loader runs. When `catalog.json` is present it is authoritative for the seven catalogue tables.

## Files

| File | Loaded into | Purpose |
|---|---|---|
| `filters.yaml` | `ops_perm.filter` | Filter catalogue and how each filter's canonical values are fetched (`static`, `mart_query`, `ops_db_query`) |
| `mart_attrs.yaml` | `ops_perm.mart_attrs` | Subject, data class, granularity and default sensitivity of each mart; the gateway adds these to every OPA input. A mart missing here is denied. |
| `column_classification.yaml` | `ops_perm.column_classification` | Per-column sensitivity and mask class (`drop`, `hash`, `partial`, `generalize`, `tokenize`) applied by the gateway masking layer |
| `column_classification.openmetadata.yaml` | `ops_perm.column_classification` | Rows authored in OpenMetadata, exported by `bundle/scripts/sync-om-classification.py` (generated; commit it) |
| `identity_sources.yaml` | `ops_identity.identity_source` | Federated identity-source catalogue (Keycloak direct accounts, Fayda verification, Google, SAML federations) |
| `roles.yaml` | `ops_perm.role`, `role_capability`, `role_scope` | Role catalogue, application capabilities and each role's default data scope |
| `role_grants.yaml` | `ops_perm.role_dashboard`, `role_widget` | Which dashboards and widgets each role is granted; `"*"` expands to the whole catalogue at load time |
| `users.yaml`, `users.zm.yaml` | `ops_identity.user`, `ops_perm.user_role` | Sample users for the Ethiopian and Zambian profiles; roles granted with `source='admin_grant'` so JIT logins do not overwrite them |

The loader also reads `bundle/roles/r-*.yaml` (mounted into the Job) to populate the normalised permission store `ops_perm.permission` and `role_permission`, from which `bundle/scripts/regen-opa-policy.sh` compiles the OPA policy.

The users file is chosen by the active country profile (`layers.appsec.users` in `bundle/profiles/<key>/profile.yaml`); the host resolves it and passes it to the Job as `NHDW_USERS_SEED`.

## Load order

The loader reads the files in dependency order inside one transaction: catalogue tables, `filters`, `mart_attrs`, `column_classification`, `identity_sources`, `roles` (then `role_scope` and the permission store from `bundle/roles/`), `role_grants`, users, and finally the platform service-account token when `SERVICE_PAT` is set.

## Adding entries

- **Mart**: add a `mart_attrs.yaml` row before granting any widget that reads it, and classify sensitive columns in `column_classification.yaml`. Do not classify dimension or join columns.
- **Filter**: add a `filters.yaml` row with the right `source`; a role's allowed values are the catalogue values intersected with `role_scope ∩ user_scope`.
- **Role**: add the row to `roles.yaml`, a grant block to `role_grants.yaml` and, for data access, a role file in `bundle/roles/` (the OPA policy is generated from it).
- **User**: add the row to the profile's users file; the loader upserts on `kc_sub`. Keycloak accounts are seeded separately by the Keycloak seed job.

## Idempotency and provenance

Every upsert uses `ON CONFLICT ... DO UPDATE`, and grants are replaced per role, so re-running the loader converges the database to the YAML. Rows carry a `managed_by` provenance: rows declared in these files are stamped `seed`, and a seed row removed from a file is deleted on the next load (widgets are soft-orphaned instead, so their grants survive a revival). Rows created at runtime (`runtime`), by stewards in the console (`steward`) or by the OpenMetadata sync (`openmetadata`) are left untouched.

## See also

- [`docs/security.md`](../../../../docs/security.md): roles, OPA, column masking and identity
- [`bundle/roles/README.md`](../../../roles/README.md): the per-role data-permission files
