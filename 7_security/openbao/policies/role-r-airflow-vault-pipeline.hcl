# OpenBao policy: airflow-vault-pipeline
#
# Attached to the long-lived token Airflow uses to mint short-TTL Postgres
# credentials for the staging-bridge and vault dbt builds. The token is held
# by the Airflow scheduler only and is not exposed to the DAG runtime image
# until a task starts.
#
# Two DB engine roles are accessible:
#   postgres-vault-landing-writer  INSERT/UPDATE on nhdw_landing (bridge)
#   postgres-vault-writer          INSERT/UPDATE on nhdw_vault_* (dbt vault)
#
# Policy templates keyed on the DAG identity would narrow this further, so a
# compromised DAG token could not pivot.

path "database/creds/postgres-vault-landing-writer" {
  capabilities = ["read"]
}

path "database/creds/postgres-vault-writer" {
  capabilities = ["read"]
}

# The ClickHouse staging_writer credential lives in KV because OpenBao 2.4.3
# has no clickhouse-database-plugin. Read-only is enough: rotation is done by
# rotate-clickhouse-staging-writer.sh, not by the DAG.
path "secret/data/clickhouse/staging-writer" {
  capabilities = ["read"]
}
