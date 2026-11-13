/*
    dim_facility: thin dimension view over the conformed facility hub.
    One row per conformed facility. Marts join the spine's
    hk_facility_conformed here instead of re-decoding facility identity
    from raw sats.

    facility_name = the resolution row from the most authoritative source
    system (MFR is the registry of record, then DHIS2, then the supply
    systems). facility_id = the conformed BK: the shared FAC-… id for
    facility_id-matched clusters, the normalised name for the (mBrana
    USSD) name-matched fallback.
*/
{{ config(materialized='view', schema='nhdw_vault_business') }}

WITH named AS (
    SELECT DISTINCT ON (hk_facility_conformed)
        hk_facility_conformed,
        source_name,
        source_system
    FROM {{ ref('v_facility_resolution') }}
    ORDER BY hk_facility_conformed,
             CASE source_system
                 WHEN 'MFR.location'     THEN 1
                 WHEN 'MFR.organization' THEN 2
                 WHEN 'DHIS2'            THEN 3
                 WHEN 'HCMIS'            THEN 4
                 WHEN 'mBrana.dbo'       THEN 5
                 ELSE 6 END
),
counted AS (
    SELECT hk_facility_conformed, COUNT(DISTINCT source_system) AS n_source_systems
    FROM {{ ref('v_facility_resolution') }}
    GROUP BY hk_facility_conformed
)
SELECT
    h.hk_facility_conformed,
    h.conformed_bk   AS facility_id,
    -- Stable master-entity identity: UUIDv5 over the conformed BK in a fixed
    -- per-context namespace: reproducible from the BK alone, survives
    -- rebuilds, and stays constant across snapshot releases.
    uuid_generate_v5(uuid_ns_url(),
        'nhdw://{{ var("deployment_context", "ET") }}/facility/' || h.conformed_bk)
                     AS master_entity_uid,
    n.source_name    AS facility_name,
    n.source_system  AS name_source,
    c.n_source_systems,
    -- provenance marker surfaced downstream: did more than one source system
    -- contribute to this master, or is it single-source?
    CASE WHEN c.n_source_systems > 1 THEN 'conformed'
         ELSE 'single_source' END AS mdm_state,
    h.load_ts
FROM {{ ref('hub_facility_conformed') }} h
-- Current entities only: the hub is insert-only history, so a steward MDM
-- decision that re-keys a record retires its old cluster from the current
-- resolution, and the retired hub row must not surface as a zombie dimension
-- row. Parity: with no decisions the hub and the view cover the same entity set.
JOIN (SELECT DISTINCT hk_facility_conformed
      FROM {{ ref('v_facility_resolution') }}) cur USING (hk_facility_conformed)
LEFT JOIN named   n USING (hk_facility_conformed)
LEFT JOIN counted c USING (hk_facility_conformed)
