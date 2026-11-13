-- Schemas inside the nhdw_vault database. Three for the
-- staging -> raw vault -> business vault pipeline, four bi_* schemas for the
-- marts that 2_pipelines/marts/ (dbt) populates:
--
--   nhdw_landing         Persistent staging area: Postgres mirror of the
--                        ClickHouse stg_* views.
--                        Bridge job (2_pipelines/vault/bridge/) does
--                        TRUNCATE + COPY per source on each run.
--   nhdw_vault_raw       AutomateDV-managed hubs / links / sats, 1:1 with
--                        source business keys.
--   nhdw_vault_business  Conformed hubs, same-as links, derived sats.
--
--   bi_facility          Facility-domain marts (counts, distribution).
--   bi_supply            Supply-chain marts (stock availability, stock-outs).
--   bi_epi               EPI / vaccine-coverage marts.
--   bi_public            k-anonymised projections fed to the Public
--                        Dashboard. All views here enforce k >= 5 via
--                        HAVING in the model SQL.
--
-- The dbt marts project (vault_writer role) materialises models into the
-- bi_* schemas. The bi_reader role gets USAGE + SELECT on whatever lands
-- there via ALTER DEFAULT PRIVILEGES below, so a fresh `dbt build` is
-- immediately readable by R-CONS-ANA / R-DEC dashboards and by the
-- public-dashboard service account, without re-granting per-model.
--
-- Idempotent.

-- pg_trgm and fuzzystrmatch: business-vault fuzzy-name matching (same-as links).
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS fuzzystrmatch;

CREATE SCHEMA IF NOT EXISTS nhdw_landing
    AUTHORIZATION vault_admin;
COMMENT ON SCHEMA nhdw_landing IS
    'Persistent Staging Area. PG-side copy of nhdw_staging.stg_* from ClickHouse, refreshed by the bridge job on each vault run.';

CREATE SCHEMA IF NOT EXISTS nhdw_vault_raw
    AUTHORIZATION vault_admin;
COMMENT ON SCHEMA nhdw_vault_raw IS
    'Raw Data Vault: hubs, links and satellites keyed on source business keys. AutomateDV-managed.';

CREATE SCHEMA IF NOT EXISTS nhdw_vault_business
    AUTHORIZATION vault_admin;
COMMENT ON SCHEMA nhdw_vault_business IS
    'Business Vault: conformed hubs, same-as links, derived satellites. AutomateDV-managed.';

-- ---------- bi_* marts schemas (populated by 2_pipelines/marts/ dbt) ----

CREATE SCHEMA IF NOT EXISTS bi_facility AUTHORIZATION vault_writer;
COMMENT ON SCHEMA bi_facility IS
    'Marts: facility-domain (counts, distribution). Populated by 2_pipelines/marts/.';

CREATE SCHEMA IF NOT EXISTS bi_supply   AUTHORIZATION vault_writer;
COMMENT ON SCHEMA bi_supply IS
    'Marts: supply-chain (stock availability, stock-outs). Populated by 2_pipelines/marts/.';

CREATE SCHEMA IF NOT EXISTS bi_epi      AUTHORIZATION vault_writer;
COMMENT ON SCHEMA bi_epi IS
    'Marts: EPI / vaccine coverage. Populated by 2_pipelines/marts/.';

CREATE SCHEMA IF NOT EXISTS bi_public   AUTHORIZATION vault_writer;
COMMENT ON SCHEMA bi_public IS
    'Marts: k-anonymised projections (k>=5) consumed by the Public Dashboard. Populated by 2_pipelines/marts/.';

-- Staging schema for marts pending governance approval.
-- Models tagged `+schema: bi_staging` in dbt_project.yml land here.
-- bi_reader gets USAGE and no SELECT default privileges here, but the
-- marts project's post-hook grants SELECT on every model it builds,
-- including the ones in this schema. Promotion copies the view
-- definition into the bi_<domain> schema after governance approval.
CREATE SCHEMA IF NOT EXISTS bi_staging AUTHORIZATION vault_writer;
COMMENT ON SCHEMA bi_staging IS
    'Marts pending governance approval. Populated by 2_pipelines/marts/; promoted to bi_<domain> by 5_orchestrator/dags/marts_promote.py.';
GRANT USAGE ON SCHEMA bi_staging TO bi_reader;
-- No ALTER DEFAULT PRIVILEGES for bi_reader on bi_staging; readability of
-- dbt-built models comes from the marts post-hook described above.

GRANT USAGE ON SCHEMA bi_facility, bi_supply, bi_epi, bi_public TO bi_reader;

ALTER DEFAULT PRIVILEGES FOR ROLE vault_writer IN SCHEMA bi_facility
    GRANT SELECT ON TABLES TO bi_reader;
ALTER DEFAULT PRIVILEGES FOR ROLE vault_writer IN SCHEMA bi_supply
    GRANT SELECT ON TABLES TO bi_reader;
ALTER DEFAULT PRIVILEGES FOR ROLE vault_writer IN SCHEMA bi_epi
    GRANT SELECT ON TABLES TO bi_reader;
ALTER DEFAULT PRIVILEGES FOR ROLE vault_writer IN SCHEMA bi_public
    GRANT SELECT ON TABLES TO bi_reader;

-- vault_writer's marts dbt build runs under an OpenBao-issued short-TTL
-- child of vault_writer. The dbt post-hook transfers ownership of every
-- new view to vault_admin so the object outlives the lease, which needs
-- vault_writer to be a member of vault_admin. (Membership conveys the
-- right to *transfer* ownership; it does not give vault_writer any
-- runtime privileges it wouldn't already have through its own grants,
-- because SET ROLE vault_admin is never invoked at query time.)
GRANT vault_admin TO vault_writer;

-- fhir_resources: HAPI FHIR backing schema.
-- HAPI manages its own DDL via Hibernate on first boot; this file only
-- creates the empty schema and the fhir_writer role HAPI connects as.
-- vault_admin keeps ownership so HAPI's runtime role cannot create objects
-- elsewhere. bi_reader has USAGE for the consent extract view over
-- fhir_resources.HFJ_RESOURCE.
CREATE SCHEMA IF NOT EXISTS fhir_resources AUTHORIZATION vault_admin;
COMMENT ON SCHEMA fhir_resources IS
    'HAPI FHIR JPA backing schema. Owned by vault_admin; fhir_writer has full DDL/DML.';

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'fhir_writer') THEN
        CREATE ROLE fhir_writer NOINHERIT LOGIN PASSWORD 'PLACEHOLDER-CYCLE-AFTER-FIRST-BOOT';
    END IF;
END$$;

GRANT USAGE, CREATE ON SCHEMA fhir_resources TO fhir_writer;
ALTER DEFAULT PRIVILEGES FOR ROLE fhir_writer IN SCHEMA fhir_resources
    GRANT SELECT, INSERT, UPDATE, DELETE, REFERENCES, TRIGGER ON TABLES TO fhir_writer;
ALTER DEFAULT PRIVILEGES FOR ROLE fhir_writer IN SCHEMA fhir_resources
    GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO fhir_writer;
GRANT USAGE ON SCHEMA fhir_resources TO bi_reader;

-- Consent revocations: the runtime gate every PHI mart must anti-join
-- against. One row revokes one patient's data for the listed purpose
-- (NULL = blanket revocation). Append-only. Rows come from direct inserts
-- or from HAPI FHIR Consent resources (see `source`).
CREATE TABLE IF NOT EXISTS nhdw_landing.consent_revocations (
    revocation_id   text        PRIMARY KEY,
    patient_id      text        NOT NULL,            -- Fayda FIN
    purpose         text,                            -- HL7 ActReason or NULL = all
    revoked_at      timestamptz NOT NULL DEFAULT now(),
    revoked_by      text        NOT NULL,            -- KC preferred_username
    note            text,
    -- source = 'manual' (direct inserts) or 'fhir-consent' (pulled from
    -- HAPI Consent resources by a consent extractor).
    source          text        NOT NULL DEFAULT 'manual',
    -- Stable handle to the source FHIR resource (Consent/N) so the
    -- extractor can upsert idempotently.
    fhir_consent_id text
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_consent_rev_fhir_id
    ON nhdw_landing.consent_revocations (fhir_consent_id)
    WHERE fhir_consent_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_consent_rev_patient
    ON nhdw_landing.consent_revocations (patient_id);
COMMENT ON TABLE nhdw_landing.consent_revocations IS
    'PHI consent gate. Marts touching patient-level data must anti-join against this table. Intended to be superseded by FHIR Consent.';
-- vault_writer + landing-writer can INSERT (record consent decisions);
-- bi_reader needs SELECT to anti-join from marts.
GRANT INSERT, SELECT ON nhdw_landing.consent_revocations TO vault_writer;
GRANT SELECT ON nhdw_landing.consent_revocations TO bi_reader;

-- Fixture: a minimal patient-level table standing in for PHI sources
-- (EthioEMR observations, FHIR Patient/Observation). It exists only to
-- exercise the consent gate and has no ingest flow.
CREATE TABLE IF NOT EXISTS nhdw_landing.demo_patient_phi (
    patient_id      text        NOT NULL,
    encounter_date  date        NOT NULL,
    observation     text        NOT NULL,
    facility_code   text,
    PRIMARY KEY (patient_id, encounter_date, observation)
);
COMMENT ON TABLE nhdw_landing.demo_patient_phi IS
    'Fixture: demo PHI for the consent gate test. Remove when real PHI sources are connected.';
GRANT SELECT, INSERT ON nhdw_landing.demo_patient_phi TO vault_writer;
GRANT SELECT ON nhdw_landing.demo_patient_phi TO bi_reader;

CREATE TABLE IF NOT EXISTS nhdw_landing.submission_inbox (
    submission_id            text        PRIMARY KEY,
    dataset_slug             text        NOT NULL,
    filename                 text        NOT NULL,
    size_bytes               bigint      NOT NULL CHECK (size_bytes >= 0),
    content_sha256           text,
    submitted_by             text        NOT NULL,
    submitted_at             timestamptz NOT NULL DEFAULT now(),
    status                   text        NOT NULL DEFAULT 'ACCEPTED-PENDING-INGESTION',
    realm                    text        NOT NULL DEFAULT 'moh-staff',
    role                     text        NOT NULL,
    -- OpenHIM transaction id returned by the channel POST.
    openhim_transaction_id   text,
    openhim_response_code    int
);

CREATE INDEX IF NOT EXISTS ix_submission_inbox_user_time
    ON nhdw_landing.submission_inbox (submitted_by, submitted_at DESC);

CREATE INDEX IF NOT EXISTS ix_submission_inbox_dataset_time
    ON nhdw_landing.submission_inbox (dataset_slug, submitted_at DESC);

-- submission_inbox: producer-side registry of "what was submitted, by
-- whom, when, status" for the Submission Portal. It lives in nhdw_landing
-- with the rest of the landing surface so the same landing-writer role
-- serves it. It does not hold the file bytes; this table stays the
-- authoritative submission record.

COMMENT ON TABLE nhdw_landing.submission_inbox IS
    'Producer-side registry of every submitted file. It does not hold the file bytes; this table is the canonical record.';

-- vault_writer (and its OpenBao children, e.g. postgres-vault-landing-writer)
-- needs INSERT to record submissions and SELECT to surface "my history"
-- back to each producer.
GRANT SELECT, INSERT ON nhdw_landing.submission_inbox TO vault_writer;

-- ---------- service-user schema grants -------------------------------------
-- Roles are created in 00_users.sh; the grants live here because they need
-- the schemas created above. Idempotent.

-- landing_writer: full DDL/DML on nhdw_landing only.
GRANT USAGE, CREATE ON SCHEMA nhdw_landing TO landing_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA nhdw_landing
    GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON TABLES TO landing_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA nhdw_landing
    GRANT USAGE, SELECT ON SEQUENCES TO landing_writer;

-- vault_writer: DDL/DML on the two vault schemas, SELECT on landing.
GRANT USAGE ON SCHEMA nhdw_landing TO vault_writer;
GRANT USAGE, CREATE ON SCHEMA nhdw_vault_raw, nhdw_vault_business TO vault_writer;
ALTER DEFAULT PRIVILEGES FOR USER vault_admin IN SCHEMA nhdw_landing
    GRANT SELECT ON TABLES TO vault_writer;
ALTER DEFAULT PRIVILEGES FOR USER landing_writer IN SCHEMA nhdw_landing
    GRANT SELECT ON TABLES TO vault_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA nhdw_vault_raw
    GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON TABLES TO vault_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA nhdw_vault_business
    GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON TABLES TO vault_writer;

-- bi_reader: read-only on the business vault.
-- dbt (the vault project) creates every nhdw_vault_business table + view as
-- vault_writer, so the default-privilege grant must be scoped FOR USER
-- vault_writer. A bare ALTER DEFAULT PRIVILEGES run by vault_admin covers
-- only vault_admin-created objects and would leave bi_reader without SELECT
-- on the links/resolution views (used by the Admin cross-source
-- reconciliation widget). Same FOR USER pattern as vault_writer above.
GRANT USAGE ON SCHEMA nhdw_vault_business TO bi_reader;
ALTER DEFAULT PRIVILEGES FOR USER vault_writer IN SCHEMA nhdw_vault_business
    GRANT SELECT ON TABLES TO bi_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA nhdw_vault_business TO bi_reader;
