# R-CONS-ANA: Internal Data Analyst.
#
# JupyterHub kernel and ad-hoc CLI access. Read-only DB credentials for the
# business vault and marts (bi_reader); no raw PHI.

path "database/creds/postgres-vault-bi-reader" {
  capabilities = ["read"]
}
path "database/creds/clickhouse-marts-reader" {
  capabilities = ["read"]
}

# Service-side MLflow tracking URL + token.
path "secret/data/services/mlflow" {
  capabilities = ["read"]
}

# Self-token introspection.
path "auth/token/lookup-self" {
  capabilities = ["read"]
}
path "auth/token/renew-self" {
  capabilities = ["update"]
}
