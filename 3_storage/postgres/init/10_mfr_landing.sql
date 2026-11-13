-- Landing tables for MFR (Master Facility Registry) FHIR R4 Organization,
-- Location and Device resources. Columns mirror the
-- 2_pipelines/ingestor/MFR/src/pipelines/facilities.py flatten shape.
--
-- Loaded by 2_pipelines/ingestor/MFR (dlt) or by the direct loader at
-- 6_access-points/api/nhdw-gateway/.../direct_load_mfr.py.
-- Idempotent.

-- DROP + CREATE (not IF NOT EXISTS): bundle/scripts/seed-realistic.sh re-runs
-- this DDL to re-apply the current schema, so a column added here must reach
-- an existing landing table (the direct-load CSV then \copies into it by
-- position). Re-running empties the tables.
DROP TABLE IF EXISTS nhdw_landing.stg_mfr__organizations CASCADE;
CREATE TABLE nhdw_landing.stg_mfr__organizations (
    fhir_id            text,
    identifier          text,            -- JSON array as text
    name                text,
    alias               text,            -- JSON array as text
    active              boolean,
    type_code           text,
    type_display        text,
    telecom             text,            -- JSON array as text
    part_of_reference   text,
    part_of_display     text,
    staff_phys          bigint,          -- facility cadre staffing (physician)
    staff_nurse         bigint,          -- nurse / midwife
    staff_hew           bigint,          -- health extension worker
    raw_resource        text,            -- full FHIR JSON
    load_ts             timestamptz NOT NULL DEFAULT now(),
    source_record_hash  numeric(20,0)
);
COMMENT ON TABLE nhdw_landing.stg_mfr__organizations IS
    'Landing for MFR FHIR Organization resources. Admin-tree levels (country/region/zone/woreda) + facility-owner orgs.';

DROP TABLE IF EXISTS nhdw_landing.stg_mfr__facilities CASCADE;
CREATE TABLE nhdw_landing.stg_mfr__facilities (
    fhir_id                          text,
    identifier                        text,
    name                              text,
    status                            text,
    mode                              text,
    operational_status                text,
    facility_type_code                text,
    facility_type_display             text,
    physical_type_code                text,
    physical_type_display             text,
    longitude                         double precision,
    latitude                          double precision,
    altitude                          double precision,
    address                           text,
    address_raw                       text,
    telecom                           text,
    managing_organization_reference   text,
    part_of_reference                 text,
    part_of_display                   text,
    raw_resource                      text,
    load_ts                           timestamptz NOT NULL DEFAULT now(),
    source_record_hash                numeric(20,0)
);
COMMENT ON TABLE nhdw_landing.stg_mfr__facilities IS
    'Landing for MFR FHIR Location resources (Ethiopian health facilities).';

DROP TABLE IF EXISTS nhdw_landing.stg_mfr__devices CASCADE;
CREATE TABLE nhdw_landing.stg_mfr__devices (
    device_id           text,            -- Device identifier (BK)
    equipment_type      text,            -- Device.type text
    status              text,            -- FHIR Device.status (active/inactive/entered-in-error)
    owner_facility_id   text,            -- Device.owner -> facility (FAC-…, canonical)
    load_ts             timestamptz NOT NULL DEFAULT now(),
    source_record_hash  numeric(20,0)
);
COMMENT ON TABLE nhdw_landing.stg_mfr__devices IS
    'Landing for MFR FHIR Device resources (facility equipment inventory).';

GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE
    ON nhdw_landing.stg_mfr__organizations, nhdw_landing.stg_mfr__facilities, nhdw_landing.stg_mfr__devices
    TO landing_writer, vault_admin;
GRANT SELECT
    ON nhdw_landing.stg_mfr__organizations, nhdw_landing.stg_mfr__facilities, nhdw_landing.stg_mfr__devices
    TO vault_writer, bi_reader;
