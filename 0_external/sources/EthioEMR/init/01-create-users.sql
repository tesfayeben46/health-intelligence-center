-- EthioEMR first-boot user provisioning, written for
-- /docker-entrypoint-initdb.d. docker-compose.yml does not mount it;
-- scripts/restore.sh provisions the same users after loading the dump.

-- Read-only ingestor user (CDC / direct extract). Replication grants allow
-- Debezium or any other CDC tool to attach to the binlog later.
-- The password must match $ETHIOEMR_READER_PASSWORD (used by restore.sh, which
-- resets it with ALTER USER) and the source-creds/ETHIOEMR_READER_PASSWORD
-- Secret the in-cluster ingestor uses.
CREATE USER IF NOT EXISTS 'ingestor'@'%' IDENTIFIED BY 'changeme-reader-password';
GRANT SELECT ON openmrs.* TO 'ingestor'@'%';
GRANT REPLICATION SLAVE, REPLICATION CLIENT ON *.* TO 'ingestor'@'%';

-- OpenMRS web app's DB user: full DML, on the openmrs DB only.
-- Matches $OPENMRS_DB_PASSWORD in the source .env.
CREATE USER IF NOT EXISTS 'openmrs'@'%' IDENTIFIED BY 'changeme-openmrs-app-password';
GRANT ALL PRIVILEGES ON openmrs.* TO 'openmrs'@'%';

FLUSH PRIVILEGES;
