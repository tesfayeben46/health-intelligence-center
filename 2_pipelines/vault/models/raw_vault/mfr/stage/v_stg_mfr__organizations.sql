/*
    v_stg_mfr__organizations: staging view for MFR FHIR Organization
    resources. In the seed every admin-tree level (country / region /
    zone / woreda) and every facility-owner is an Organization, so this
    table is the de-facto facility-master plus admin hierarchy.

    The shared canonical `facility_id` (and `region_id`, `zone_id`,
    `woreda_id`, `country_id` for the admin levels) is carried inside
    the `identifier` JSON array under one of the `SYS_*` systems set by
    the seeder. The conformed-bridge (business vault) extracts those.

    parent_fhir_id strips the "Organization/" prefix from
    part_of_reference (NULL when absent, so the parent-side hash key is
    NULL and the AutomateDV link macro drops the row from
    lnk_mfr_org_partof).
*/
{{ config(materialized='view') }}
WITH src AS (
    SELECT *,
           NULLIF(REGEXP_REPLACE(COALESCE(part_of_reference, ''), '^.*/', ''), '') AS parent_fhir_id
    FROM {{ source('landing', 'stg_mfr__organizations') }}
)
SELECT *,
       'MFR.organizations' AS record_source,
       {{ automate_dv.hash(columns=['fhir_id'], alias='hk_mfr_organization') }},
       {{ automate_dv.hash(columns=['parent_fhir_id'], alias='hk_mfr_organization_parent') }},
       {{ automate_dv.hash(columns=['fhir_id','parent_fhir_id'], alias='hk_mfr_org_partof') }},
       {{ automate_dv.hash(columns=sat_hashdiff('mfr_organization'), alias='hd_mfr_organization', is_hashdiff=true) }}
FROM src
