# OpenBao

OpenBao is not deployed by the default manifests; this directory holds its policies and operating scripts. Running components read their credentials from Kubernetes Secrets (template: `1_infrastructure/k8s/overlays/dev/secrets.yaml.template`). The intended path to production is OpenBao behind an operator such as External Secrets that syncs into the same Secret names, so components need no change.

OpenBao is the open-source fork of HashiCorp Vault (MPL-2.0; same API and CLI, with `bao` as the binary). In this design it is both the secrets store and the issuer of short-lived database credentials.

| Feature | Use |
|---|---|
| KV v2 at `secret/` | Service secrets that otherwise sit in `.env` files, under `secret/<domain>/<name>` |
| Database secrets engine | Short-TTL Postgres roles on `postgres-vault`: `postgres-vault-writer` (inherits `vault_writer`, 1 h / max 4 h), `postgres-vault-bi-reader` (inherits `bi_reader`, 4 h / max 8 h), `postgres-vault-landing-writer` (inherits `landing_writer`, 1 h / max 4 h) |
| AppRole auth | Machine-to-machine auth for services without OIDC |
| JWT auth (`auth/jwt-keycloak`) | Per-user tokens from Keycloak JWTs, bound on the flat `roles` claim emitted by the `nhdw-flat-roles` client scope |

ClickHouse has no OpenBao database plugin (OpenBao 2.4.3), so its credentials are static secrets in KV, rotated by script.

## Layout

```
7_security/openbao/
├── .env.example     admin connection details and data paths for a standalone instance
├── policies/        HCL policies, loaded under their file names (role-*.hcl)
└── scripts/         bootstrap, verification, seeding, rotation, certificates
```

## Scripts

| Script | Purpose |
|---|---|
| `bootstrap.sh` | Run inside the OpenBao container. Initialises (Shamir 5/3, dev posture: keys and root token on disk), unseals, mounts `secret/` (kv v2) and `database/`, configures the `postgres-vault` connection and the three roles above, loads every policy, enables AppRole, then seeds in-container secrets into KV. Idempotent: an initialised instance is unsealed with the persisted keys and policies are reloaded. No audit device is enabled (audit is config-driven in OpenBao 2.x). |
| `verify.sh` | Checks unsealed status, the `secret/` and `database/` mounts, the core policies, and the database roles when Postgres is configured. |
| `seed-secrets-to-kv.sh` | Copies bootstrap secrets from `.env` files (or exported env vars) into KV with a `seeded_at` stamp. Never overwrites an existing entry. |
| `migrate-from-env.sh` | Writes selected `.env` values to `secret/data/services/<name>`, overwriting existing entries. |
| `kv-get.sh` | Prints a KV v2 secret, or one field of it. |
| `apply-flat-roles-binding.sh` | Creates the `auth/jwt-keycloak` roles that map Keycloak realm roles to policies (15 min tokens, 60 min max). Expects the JWT auth method to be mounted already. |
| `rotate-postgres-vault-admin.sh` | Rotates the `vault_admin` password in Postgres, KV and the database engine's root config, then connects with the new value. |
| `rotate-postgres-fhir-writer.sh` | Rotates the static `fhir_writer` password (HAPI FHIR keeps one stable identity per boot). |
| `rotate-clickhouse-staging-writer.sh` | Rotates the ClickHouse `staging_writer` password and stores it in `secret/clickhouse/staging-writer`. |
| `rotate-keycloak-admin.sh` | Rotates the Keycloak master-realm admin password and stores it in `secret/services/keycloak`. |
| `issue-server-cert.sh` | Issues a server certificate from an OpenBao PKI intermediate CA into a directory (`cert.pem`, `key.pem`, `fullchain.pem`, `ca.pem`, `issued.json`). Requires a PKI mount, which `bootstrap.sh` does not create. |

The scripts default to `http://127.0.0.1:8200`. Most read `BAO_ADDR`/`BAO_TOKEN`; the rotation and certificate scripts read `OPENBAO_ADDR`/`OPENBAO_TOKEN`.

## Policies

| Policy | Attached to | Grants |
|---|---|---|
| `role-r-ops-eng` | R-OPS-ENG | read `secret/data/services/*`; writer and ClickHouse staging-writer DB credentials; health and mount listing |
| `role-r-ops-adm` | R-OPS-ADM | manage mounts, auth methods and ACL policies; read audit config; explicit deny on PHI secrets and reader DB credentials |
| `role-r-ops-sec` | R-OPS-SEC | full audit control; read-only policies; break-glass grant entries; control-group authorisation; explicit deny on PHI secrets and all DB credentials |
| `role-r-cons-ana` | R-CONS-ANA | bi-reader and ClickHouse marts-reader credentials; MLflow service secret |
| `role-r-cons-sci` | R-CONS-SCI | as R-CONS-ANA plus ClickHouse staging-reader credentials and a per-user scratch object-store credential |
| `role-r-prod-submit` | R-PROD-SUBMIT | the submission-portal service secret |
| `role-r-cons-ana-jwt` | JWT roles r-cons-ana, r-cons-sci, r-cons-res | bi-reader credentials for the user's own session |
| `role-r-stew-jwt` | JWT role r-stew | bi-reader credentials; read `secret/data/datasets/*` |
| `role-r-internal-bi` | Internal BI dashboard; JWT role r-dec | bi-reader credentials |
| `role-r-public-dashboard` | Public dashboard | bi-reader credentials |
| `role-r-submission-portal` | Submission portal; JWT role r-prod-submit | landing-writer credentials |
| `role-r-jupyterhub-spawn` | JupyterHub at notebook spawn; JWT role r-cons-sci | bi-reader and writer credentials |
| `role-r-airflow-vault-pipeline` | Airflow scheduler | landing-writer and writer credentials; read the ClickHouse staging-writer secret |

Some policies name database roles (`clickhouse-marts-reader`, `clickhouse-staging-reader`, `clickhouse-staging-writer`) that `bootstrap.sh` does not create, because there is no ClickHouse plugin.

## Example: issue a short-lived credential

```bash
export BAO_ADDR=http://127.0.0.1:8200 BAO_TOKEN=...
bao read database/creds/postgres-vault-bi-reader
```

The response holds a username, password and lease (4 h for bi-reader). Use it within the lease and do not persist it.

## Development posture and production

| Aspect | This setup | Production |
|---|---|---|
| Storage | File backend (`/data/security/openbao`) | Integrated Raft (HA) |
| Listener | TLS off | TLS on |
| Unseal | Shamir 5/3, keys and root token on disk | Key shares held by separate R-OPS-SEC officers, or auto-unseal via KMS/transit |
| Root token | Persisted for bootstrap | Revoked after the first admin policy is issued |
| Audit | Not enabled | File audit device shipped to the audit log store |
