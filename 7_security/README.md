# Security

Identity (Keycloak), data authorization (OPA), the security seed loaders that fill the permission model in Postgres, and supporting material for secrets management, audit logging and backup.

| Directory | Contents |
|---|---|
| `keycloak/` | Realm imports, the `nhdw` login theme, the realm setup script and a browser login test |
| `opa/` | Rego policies, the role-policy compiler and the policy test suite |
| `scripts/` | Security seed loaders, their contract test, and the host backup script |
| `openbao/` | OpenBao policies and scripts (not deployed by the default manifests) |
| `audit/` | Standalone Loki configuration for the FHIR AuditEvent log stream |

## Keycloak

**Image.** `keycloak/Dockerfile` is upstream `quay.io/keycloak/keycloak:26.0.7` with the `nhdw` theme copied into `/opt/keycloak/themes`. The Kubernetes deployment runs `start-dev --import-realm`.

**Realms** (`keycloak/realm-configs/`, packaged as the `keycloak-realm-configs` ConfigMap and imported at startup):

| Realm | Users | Clients in the import |
|---|---|---|
| `moh-staff` | staff, analysts, stewards, producers (R-DEC, R-CONS-*, R-STEW, R-PROD-*) | `internal-bi`, `workshop`, `jupyterhub`, `submission-portal`, `ops-hub`, `cloudbeaver` (the last three serve applications that are not in this repository) |
| `privileged-ops` | operators, administrators, security officers (R-OPS-ENG, R-OPS-ADM, R-OPS-SEC) | `admin`, `ops-hub`, `cloudbeaver`, `openbao` (the last three serve applications that are not in this repository) |
| `ehia-saml-mock` | a mock external identity provider (EHIA) used for SAML federation | one SAML SP client per brokered realm |

The realm JSON files fix the structure; hostnames and secrets are applied by the setup script.

**Setup script.** `keycloak/scripts/setup-keycloak.py` drives the Admin REST API and is idempotent. In Kubernetes it runs as the `keycloak-seed` Job (`1_infrastructure/k8s/base/keycloak-seed-job.yaml`). It:

- creates realm roles the seed users need, including `R-SUPER` and the zero-grant `R-OPS-NONE`;
- imports users from `bundle/seed-pack/app/security/users.yaml` (or the active country profile's users file) with their exact `kc_sub` UUIDs, sets passwords from `SEED_PASSWORD` and assigns roles; users marked `mfa: true` must enrol TOTP;
- creates the dashboard clients as public PKCE clients, the confidential tool clients (dbt-docs, Airflow, JupyterHub, Superset, OpenMetadata) and the tool-gate clients, with redirect URIs derived from `PUBLIC_HOST_BASE` and the `DASH_BASE_*` variables;
- configures the Google (built-in social provider, skipped when no real client ID is set) and EHIA (SAML) brokers. In `moh-staff` brokered users land on read-only roles; in `privileged-ops` they land on `R-OPS-NONE`, which OPA denies, so no external login yields operator access. Each broker stamps an `idp_trust` attribute (Google low, EHIA high) that a protocol mapper puts into the token for the OPA trust guard;
- copies the `profile`, `email` and `nhdw-flat-roles` client scopes into `privileged-ops`, sets `loginTheme=nhdw` and display names, and points realm SMTP at the relay from `smtp-secrets`.

Reseeding without `PUBLIC_HOST_BASE` and the `DASH_BASE_*` variables rewrites redirect URIs to the localhost defaults.

**Theme.** `keycloak/themes/nhdw/login/` extends `base` with its own `template.ftl` and `login.ftl` markup, a single stylesheet (`resources/css/brand.v3.css`), images and message overrides; it does not use PatternFly.

**Test.** `keycloak/tests/test_login_flows.mjs` is a Playwright test against the nip.io hostnames covering password login, the Google redirect, EHIA SAML federation and the admin login page.

## OPA

**Policies** (`opa/policies/`, packaged as the `opa-policies` ConfigMap and served by `openpolicyagent/opa`):

- `common.rego` (hand-written) is the decision entry point `data.nhdw.authz.decide`. It routes `input.user.role` to the per-role decision (the seeded role codes `R-PD-HIV` and `R-RHB-OROMIA` route to R-DEC; other R-PD-* or R-RHB-* codes need their own line in `common.rego`; R-SUPER is a wildcard allow), denies unknown roles, applies the trust guard (a low-trust identity is denied sensitive verbs such as write, export and approve, and any raw-granularity data, whatever its role), and merges two obligations into the result: a `row_filter` built from the caller's scope in `input.context`, and `masked_columns`, the classified columns the role may not see in clear.
- `role_r_*.rego` are generated, one per role, and must not be edited by hand. Each defines `allow` rules from the role's permission tuples (subject, verbs, granularity, purpose of use, data class, sensitivity) and the role's `reveal_sensitivity` set.
- `authz_test.rego` is the `opa test` suite. It mirrors the gateway's startup self-test matrix and is not shipped to the runtime.

**Compiler.** `opa/scripts/compile.py` emits the `role_r_*.rego` files from `bundle/roles/*.yaml`, or with `--from-db` from the normalized `ops_perm.permission` / `ops_perm.role_permission` tables. `--out-dir` writes elsewhere. `bundle/scripts/regen-opa-policy.sh` runs the DB-sourced compile and deploys the result only if `opa test` passes.

```bash
python3 7_security/opa/scripts/compile.py
opa test 7_security/opa/policies
```

## Security seed loaders

| Script | What it loads |
|---|---|
| `scripts/load_security_seed.py` | The permission model in `nhdw_ops`: dashboards, pages, sections, widgets and placements (from `catalog.json` when present), filters, `mart_attrs`, `column_classification`, identity sources, roles, role scope, the normalized permission store from `bundle/roles/`, role grants, users with their roles and data scope, and an optional service-account token (`SERVICE_PAT`). One transaction; idempotent. Rows carry `managed_by`: seed rows follow the YAML (including removals), runtime rows are never touched, and retired widgets are soft-orphaned so their grants survive. |
| `scripts/load_public_articles.py` | Public newsroom articles into `ops_governance.public_article` with the same seed/runtime contract. |
| `scripts/test_column_classification_loader.py` | Contract test for the `column_classification` reconciliation against a disposable Postgres (`TEST_DSN`), using the DDL extracted from `3_storage/postgres/init/17_perm_schemas.sql`. |

Both loaders run as Jobs from `bundle/scripts/seed-app-security.sh`; locally `3_storage/postgres/init/18_security_seed.sh` runs the security seed loader. The seed YAML lives in `bundle/seed-pack/app/security/`; its user records are fictional sample accounts.

`scripts/backup.sh` writes a timestamped archive with a sha256 manifest: a Postgres dump, the ClickHouse staging DDL, an S3 mirror of the object store (`3_storage/seaweedfs/ops/backup-objstore.sh`), the OpenHIM Mongo dump, Keycloak realm exports and the OpenBao file backend. Except for the object store, it works against local Docker containers.

## OpenBao and audit

`openbao/` contains policies and scripts for OpenBao as the secrets store and short-lived database credential issuer. The default manifests do not deploy it; running components use Kubernetes Secrets. See `openbao/README.md`.

`audit/loki-config.yaml` is a single-node Loki configuration for the FHIR AuditEvent lines the access points emit. The Kubernetes deployment uses its own Loki configuration in `1_infrastructure/k8s/base/observability/loki.yaml`.
