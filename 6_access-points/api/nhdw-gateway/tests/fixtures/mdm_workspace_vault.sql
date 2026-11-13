-- Synthetic business-vault objects for tests/test_mdm_workspace.py.
--
-- The MDM workspace routes read these through Trino. In the warehouse most
-- of them are dbt views; here each is a plain table with the same name and
-- column types, filled with a small, clearly fake facility set that covers
-- every case the tests check:
--   M1  three sources agree (DHIS2, HCMIS, MFR.organization); two golden
--       revisions, the latest with coordinates and a phone number
--   M2  two sources (DHIS2, HCMIS)
--   M3  DHIS2 only;  M4  the same name from HCMIS without a facility id
--   M5  MFR.organization only, name with a leading space and lower case
--   M6  a DHIS2 admin area resolved by name
-- Hash keys are sha256 of readable text, so rows join as in the vault.

CREATE SCHEMA IF NOT EXISTS nhdw_vault_business;
SET search_path = nhdw_vault_business;

CREATE TABLE dim_facility (hk_facility_conformed bytea, facility_id text, master_entity_uid uuid,
    facility_name text, name_source text, n_source_systems bigint, mdm_state text,
    load_ts timestamp with time zone);
CREATE TABLE hub_commodity_conformed (hk_commodity_conformed bytea, conformed_bk text,
    load_ts timestamp with time zone, record_source text);
CREATE TABLE hub_facility_conformed (hk_facility_conformed bytea, conformed_bk text,
    load_ts timestamp with time zone, record_source text);
CREATE TABLE lnk_facility_identifier (hk_facility_identifier bytea, hk_facility_conformed bytea,
    identifier_namespace text, identifier_value text, load_ts timestamp with time zone,
    record_source text);
CREATE TABLE lnk_same_as_facility (hk_same_as_facility bytea, hk_source_facility bytea,
    hk_facility_conformed bytea, load_ts timestamp with time zone, record_source text);
CREATE TABLE sat_facility_canonical (hk_facility_conformed bytea, canonical_name text,
    name_source text, operational_status text, status_source text, telecom text,
    telecom_source text, latitude numeric, longitude numeric, coord_source text,
    woreda_name text, zone_name text, admin_source text, hashdiff text,
    load_ts timestamp with time zone, record_source text);
CREATE TABLE v_facility_resolution (hk_source_facility bytea, source_system text,
    source_name text, source_code text, facility_id text, norm_name text, conformed_bk text,
    match_strategy text, load_ts timestamp with time zone, hk_facility_conformed bytea,
    record_source text, hk_same_as_facility bytea);
CREATE TABLE v_sat_dhis2_org_unit_current (hk_dhis2_org_unit bytea, hashdiff bytea, name text,
    code text, short_name text, level bigint, path text, parent_id text,
    opening_date timestamp with time zone, closed_date text,
    last_updated timestamp with time zone, load_ts timestamp with time zone, record_source text);
CREATE TABLE v_sat_hcmis_facility_current (hk_hcmis_facility bytea, hashdiff bytea, name text,
    description text, environment_id text, rowguid text, load_ts timestamp with time zone,
    record_source text);
CREATE TABLE v_sat_mbrana_facility_current (hk_mbrana_facility bytea, hashdiff bytea, name text,
    description text, phone text, woreda text, town text, kebele text, ru_type text,
    ownership text, zone text, administrative_unit_id text, operational_status text,
    latitude text, longitude text, active text, in_process text, alternate_name text,
    reporting_group_id text, environment_id text, modified_date text,
    load_ts timestamp with time zone, record_source text);
CREATE TABLE v_sat_mbrana_ussd_facility_current (hk_mbrana_ussd_facility bytea, hashdiff bytea,
    institution_name text, parent_institution_id text, load_ts timestamp with time zone,
    record_source text);
CREATE TABLE v_sat_mfr_facility_current (hk_mfr_facility bytea, hashdiff bytea, name text,
    status text, mode text, operational_status text, facility_type_code text,
    facility_type_display text, physical_type_code text, physical_type_display text,
    longitude double precision, latitude double precision, altitude double precision,
    address text, address_raw text, telecom text, identifier text,
    managing_organization_reference text, part_of_reference text, part_of_display text,
    load_ts timestamp with time zone, record_source text);
CREATE TABLE v_sat_mfr_organization_current (hk_mfr_organization bytea, hashdiff bytea, name text,
    active boolean, alias text, type_code text, type_display text, telecom text, identifier text,
    part_of_reference text, part_of_display text, staff_phys bigint, staff_nurse bigint,
    staff_hew bigint, load_ts timestamp with time zone, record_source text);

-- the source records and the master each resolves to
CREATE TEMP TABLE src (k text, sys text, name text, code text, fac_id text, bk text, strategy text,
                       seen timestamptz);
INSERT INTO src VALUES
 ('dhis2:ou-1', 'DHIS2',            'Test Kebele Health Center', 'FAC-TS-HC-000001', 'FAC-TS-HC-000001', 'FAC-TS-HC-000001', 'facility_id', '2026-06-01 08:00+00'),
 ('hcmis:1',    'HCMIS',            'Test Kebele Health Center', NULL,               'FAC-TS-HC-000001', 'FAC-TS-HC-000001', 'facility_id', '2026-06-02 08:00+00'),
 ('mfr:org-1',  'MFR.organization', 'Test Kebele Health Center', NULL,               'FAC-TS-HC-000001', 'FAC-TS-HC-000001', 'facility_id', '2026-06-03 08:00+00'),
 ('dhis2:ou-2', 'DHIS2',            'Test Ridge Health Post',    'FAC-TS-HP-000002', 'FAC-TS-HP-000002', 'FAC-TS-HP-000002', 'facility_id', '2026-06-01 09:00+00'),
 ('hcmis:2',    'HCMIS',            'Test Ridge Health Post',    NULL,               'FAC-TS-HP-000002', 'FAC-TS-HP-000002', 'facility_id', '2026-06-02 09:00+00'),
 ('dhis2:ou-3', 'DHIS2',            'Test Valley Health Post',   'FAC-TS-HP-000003', 'FAC-TS-HP-000003', 'FAC-TS-HP-000003', 'facility_id', '2026-06-01 10:00+00'),
 ('hcmis:3',    'HCMIS',            'Test Valley Health Post',   NULL,               NULL,               'test valley health post', 'norm_name', '2026-06-02 10:00+00'),
 ('mfr:org-5',  'MFR.organization', ' test Lowland Pharmacy',    NULL,               'FAC-TS-PH-000005', 'FAC-TS-PH-000005', 'facility_id', '2026-06-03 11:00+00'),
 ('dhis2:ou-6', 'DHIS2',            'Test Woreda One',           'WOR-TS-1',         NULL,               'test woreda one', 'norm_name', '2026-06-01 12:00+00');

-- each source's current satellite
INSERT INTO v_sat_dhis2_org_unit_current
    (hk_dhis2_org_unit, hashdiff, name, code, short_name, level, path, parent_id, load_ts, record_source)
SELECT sha256(convert_to(k, 'UTF8')), sha256(convert_to(k || ':v1', 'UTF8')), name, code, name,
       CASE WHEN code LIKE 'FAC-%' THEN 5 ELSE 4 END, '/TS/' || k, 'ts-parent', seen, 'TEST'
FROM src WHERE sys = 'DHIS2';

INSERT INTO v_sat_hcmis_facility_current
    (hk_hcmis_facility, hashdiff, name, description, environment_id, load_ts, record_source)
SELECT sha256(convert_to(k, 'UTF8')), sha256(convert_to(k || ':v1', 'UTF8')), name,
       CASE WHEN fac_id IS NOT NULL THEN 'facility_id=' || fac_id ELSE 'no facility id' END,
       'env-ts', seen, 'TEST'
FROM src WHERE sys = 'HCMIS';

INSERT INTO v_sat_mfr_organization_current
    (hk_mfr_organization, hashdiff, name, active, type_display, telecom, identifier,
     part_of_display, load_ts, record_source)
SELECT sha256(convert_to(k, 'UTF8')), sha256(convert_to(k || ':v1', 'UTF8')), name, true,
       CASE WHEN name ILIKE '%pharmacy%' THEN 'Pharmacy' ELSE 'Health Center' END,
       CASE WHEN k = 'mfr:org-1' THEN '+251-11-000-0001' END,
       '[{"use": "official", "system": "https://mfr.example/facility-id", "value": "' || fac_id || '"}]',
       'Test Woreda One', seen, 'TEST'
FROM src WHERE sys = 'MFR.organization';

-- resolution, same-as links, hubs
INSERT INTO v_facility_resolution
SELECT sha256(convert_to(k, 'UTF8')), sys, name, code, fac_id,
       lower(regexp_replace(name, '[^a-zA-Z0-9 ]', '', 'g')), bk, strategy, seen,
       sha256(convert_to(bk, 'UTF8')), 'BV.facility_resolution',
       sha256(convert_to(sys || '|' || k || '|' || bk, 'UTF8'))
FROM src;

INSERT INTO lnk_same_as_facility
SELECT sha256(convert_to(sys || '|' || k || '|' || bk, 'UTF8')), sha256(convert_to(k, 'UTF8')),
       sha256(convert_to(bk, 'UTF8')), seen, 'BV.facility_resolution'
FROM src;

INSERT INTO hub_facility_conformed
SELECT sha256(convert_to(bk, 'UTF8')), bk, min(seen), 'BV.facility_resolution'
FROM src GROUP BY bk;

INSERT INTO dim_facility
SELECT sha256(convert_to(bk, 'UTF8')), bk, md5('TS|' || bk)::uuid,
       (array_agg(name ORDER BY sys DESC))[1], (array_agg(sys ORDER BY sys DESC))[1],
       count(DISTINCT sys),
       CASE WHEN count(DISTINCT sys) > 1 THEN 'conformed' ELSE 'single_source' END, max(seen)
FROM src GROUP BY bk;

-- golden record: one revision per master; M1 gains coordinates + phone
INSERT INTO sat_facility_canonical
    (hk_facility_conformed, canonical_name, name_source, hashdiff, load_ts, record_source)
SELECT hk_facility_conformed, facility_name, name_source, md5(facility_id || ':r1'),
       load_ts + interval '1 hour', 'BV.canonical'
FROM dim_facility;
INSERT INTO sat_facility_canonical
    (hk_facility_conformed, canonical_name, name_source, operational_status, status_source,
     telecom, telecom_source, latitude, longitude, coord_source, woreda_name, zone_name,
     admin_source, hashdiff, load_ts, record_source)
VALUES (sha256(convert_to('FAC-TS-HC-000001', 'UTF8')), 'Test Kebele Health Center',
        'MFR.organization', 'Functional', 'MFR.organization', '+251-11-000-0001',
        'MFR.organization', 9.012345, 38.765432, 'MFR.organization', 'Test Woreda One',
        'Test Zone', 'MFR.organization', md5('FAC-TS-HC-000001:r2'),
        '2026-06-10 08:00+00', 'BV.canonical');

-- identifiers
INSERT INTO lnk_facility_identifier
SELECT sha256(convert_to(k || '|id', 'UTF8')), sha256(convert_to(bk, 'UTF8')),
       CASE sys WHEN 'DHIS2' THEN 'DHIS2.uid' WHEN 'HCMIS' THEN 'HCMIS.institution_id'
                ELSE 'MFR.fhir_id' END,
       split_part(k, ':', 2), seen, 'BV.identifier'
FROM src;
INSERT INTO lnk_facility_identifier
SELECT sha256(convert_to(bk || '|nhdw', 'UTF8')), sha256(convert_to(bk, 'UTF8')),
       'NHDW.facility_id', bk, min(seen), 'BV.identifier'
FROM src WHERE bk LIKE 'FAC-%' GROUP BY bk;

-- commodities
INSERT INTO hub_commodity_conformed
SELECT sha256(convert_to(c, 'UTF8')), c, '2026-06-01 00:00+00', 'BV.commodity_resolution'
FROM unnest(array['TS-AMOXICILLIN-250', 'TS-ORS-SACHET', 'TS-ZINC-20']) AS c;

DROP TABLE src;
