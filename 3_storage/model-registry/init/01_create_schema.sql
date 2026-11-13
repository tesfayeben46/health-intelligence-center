-- MLflow tracking and model registry backing store (local setup).
--
-- MLflow needs:
--   1. A relational database for experiments, runs and registered models
--   2. An artifact store (the object store)
--
-- (1) lives in postgres-vault as a separate schema and user, avoiding a
-- second Postgres. The Kubernetes deployment uses the nhdw_mlflow database
-- from 3_storage/postgres/init/09_mlflow_db.sh instead.
--
-- Idempotent.

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mlflow') THEN
        CREATE ROLE mlflow WITH LOGIN PASSWORD :'mlflow_password';
    ELSE
        ALTER ROLE mlflow WITH PASSWORD :'mlflow_password';
    END IF;
END $$;

CREATE SCHEMA IF NOT EXISTS mlflow_metadata AUTHORIZATION mlflow;
GRANT ALL PRIVILEGES ON SCHEMA mlflow_metadata TO mlflow;
ALTER DEFAULT PRIVILEGES FOR USER mlflow IN SCHEMA mlflow_metadata
    GRANT SELECT ON TABLES TO bi_reader;
