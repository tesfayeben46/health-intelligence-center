/*
    lnk_facility_identifier: every identifier a conformed facility is known by,
    across identifier namespaces.

    The question an MDM layer must answer is "who is this?" when a caller
    arrives holding one system's id. The conformed hub answers the reverse
    (what is the master key), and lnk_same_as_facility bridges hash keys;
    neither looks a master up by a human-held identifier such as an
    HCMIS institution_id printed on a requisition or a DHIS2 uid pasted from a
    URL. This link closes that: one row per
        (master, namespace, identifier value)
    so any source identifier resolves to exactly one master.

    Namespaces (the vocabulary of identifier systems; keep in step with the
    source hubs' natural keys):
        NHDW.facility_id      the conformed FAC-… slug (the shared canonical id)
        DHIS2.uid             org-unit uid          (hub_dhis2_org_unit.id)
        HCMIS.institution_id  supply-system integer (hub_hcmis_facility)
        MBRANA.id             USSD master integer   (hub_mbrana_facility)
        MFR.fhir_id           registry resource id  (hub_mfr_*.fhir_id)

    The HCMIS and mBrana integer spaces overlap, which is exactly why the
    namespace is part of the key: '412' means
    nothing on its own, 'HCMIS.institution_id:412' is unambiguous.

    Insert-only like every link: an identifier is never deleted, so a
    retired-but-remembered id still resolves (the audit question "what did this
    id mean last year?" stays answerable).
*/
{{ config(
    materialized='incremental',
    on_schema_change='append_new_columns',
    schema='nhdw_vault_business'
) }}

WITH members AS (
    SELECT
        r.hk_facility_conformed,
        r.conformed_bk,
        r.hk_source_facility,
        r.source_system
    FROM {{ ref('v_facility_resolution') }} r
),
-- 1. the conformed identifier itself (only for slug-keyed masters: a
--    name-keyed master has no canonical id to publish)
canonical AS (
    SELECT DISTINCT
        hk_facility_conformed,
        'NHDW.facility_id' AS identifier_namespace,
        conformed_bk       AS identifier_value
    FROM members
    WHERE conformed_bk LIKE 'FAC-%'
),
-- 2. each source's native identifier, read from that source's hub business key
dhis2 AS (
    SELECT DISTINCT m.hk_facility_conformed,
           'DHIS2.uid' AS identifier_namespace, h.id AS identifier_value
    FROM members m
    JOIN {{ ref('hub_dhis2_org_unit') }} h ON h.hk_dhis2_org_unit = m.hk_source_facility
    WHERE m.source_system = 'DHIS2'
),
hcmis AS (
    SELECT DISTINCT m.hk_facility_conformed,
           'HCMIS.institution_id' AS identifier_namespace,
           h.institution_id::text AS identifier_value
    FROM members m
    JOIN {{ ref('hub_hcmis_facility') }} h ON h.hk_hcmis_facility = m.hk_source_facility
    WHERE m.source_system = 'HCMIS'
),
mbrana AS (
    SELECT DISTINCT m.hk_facility_conformed,
           'MBRANA.id' AS identifier_namespace, h.id::text AS identifier_value
    FROM members m
    JOIN {{ ref('hub_mbrana_facility') }} h ON h.hk_mbrana_facility = m.hk_source_facility
    WHERE m.source_system = 'mBrana.dbo'
),
mfr_loc AS (
    SELECT DISTINCT m.hk_facility_conformed,
           'MFR.fhir_id' AS identifier_namespace, h.fhir_id AS identifier_value
    FROM members m
    JOIN {{ ref('hub_mfr_facility') }} h ON h.hk_mfr_facility = m.hk_source_facility
    WHERE m.source_system = 'MFR.location'
),
mfr_org AS (
    SELECT DISTINCT m.hk_facility_conformed,
           'MFR.fhir_id' AS identifier_namespace, h.fhir_id AS identifier_value
    FROM members m
    JOIN {{ ref('hub_mfr_organization') }} h ON h.hk_mfr_organization = m.hk_source_facility
    WHERE m.source_system = 'MFR.organization'
),
unioned AS (
    SELECT * FROM canonical
    UNION SELECT * FROM dhis2
    UNION SELECT * FROM hcmis
    UNION SELECT * FROM mbrana
    UNION SELECT * FROM mfr_loc
    UNION SELECT * FROM mfr_org
),
keyed AS (
    SELECT
        u.hk_facility_conformed,
        u.identifier_namespace,
        u.identifier_value,
        -- link PK over (master, namespace, value): the namespace is a hash
        -- input for the same reason it is part of the logical key: the
        -- integer id spaces of HCMIS and mBrana overlap.
        {{ automate_dv.hash(columns=['hk_facility_conformed_hex',
                                     'identifier_namespace',
                                     'identifier_value'],
                            alias='hk_facility_identifier') }},
        CURRENT_TIMESTAMP AS load_ts,
        'BV.facility_identifier' AS record_source
    FROM (
        SELECT *, encode(hk_facility_conformed, 'hex') AS hk_facility_conformed_hex
        FROM unioned
        WHERE COALESCE(identifier_value, '') <> ''
    ) u
)
SELECT
    hk_facility_identifier,
    hk_facility_conformed,
    identifier_namespace,
    identifier_value,
    load_ts,
    record_source
FROM keyed
{% if is_incremental() %}
WHERE hk_facility_identifier NOT IN (SELECT hk_facility_identifier FROM {{ this }})
{% endif %}
