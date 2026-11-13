# R-CONS-SCI: Data Scientist / ML Engineer.
#
# R-CONS-ANA access plus a per-user object-store scratch credential and MLflow
# service secrets. Cannot promote models to Production (a steward and
# domain-owner action on a separate path).

path "database/creds/postgres-vault-bi-reader" {
  capabilities = ["read"]
}
path "database/creds/clickhouse-marts-reader" {
  capabilities = ["read"]
}
path "database/creds/clickhouse-staging-reader" {
  capabilities = ["read"]
}

# Object-store scratch credential (path name is historical). The per-user
# prefix is enforced by the object store's identity, not here. The kv path
# returns an AWS-style access key and secret.
path "secret/data/minio-scratch/{{identity.entity.name}}" {
  capabilities = ["read"]
}

# MLflow + serving endpoints.
path "secret/data/services/mlflow" {
  capabilities = ["read"]
}

# Self-token introspection + renew.
path "auth/token/lookup-self" {
  capabilities = ["read"]
}
path "auth/token/renew-self" {
  capabilities = ["update"]
}
