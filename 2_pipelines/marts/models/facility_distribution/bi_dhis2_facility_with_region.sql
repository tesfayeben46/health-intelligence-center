/*
    bi_dhis2_facility_with_region: every DHIS2 facility org unit tagged
    with the region it sits under.

    Facilities are identified by their code, not by a hardcoded tree
    depth: the seeded tree is 5 levels (1 national / 2 region / 3 zone /
    4 woreda WOR-% / 5 facility FAC-%), and depth may change; the FAC-%
    code contract may not. The facility code is
    FAC-<region>-<tier>-<hash>; facility_tier is the tier token
    (HP/HC/PHC/MC/PH/GH/SH/DS), the same classification the gateway
    facility widgets use.

    DHIS2's `path` is `/L1/L2/.../Ln` where each segment is the parent_id
    at that level. For Ethiopia, level 2 is the region (Addis Ababa, Oromia,
    Tigray, ...). We split path on '/' and take element 3 (1-based, since
    the path leads with '/') as the region's org_unit code.

    Reads the current-record view (sats are insert-only; the raw sat
    grows one row per version, which would duplicate facilities here).

    Materialised as a view: small (~900 rows), and reads of this drive the
    aggregates below; recomputing on each query keeps it always-fresh
    against the vault.
*/

WITH ou AS (
    SELECT
        ou.code           AS org_unit_code,
        ou.name           AS org_unit_name,
        ou.level::int     AS org_unit_level,
        ou.path           AS org_unit_path,
        ou.parent_id      AS org_unit_parent_id,
        split_part(ou.code, '-', 3) AS facility_tier,
        -- path = '/L1id/L2id/.../Lnid', split_part is 1-based after the
        -- leading slash, so position 3 is the level-2 (region) id.
        split_part(ou.path, '/', 3) AS region_id
    FROM {{ source('nhdw_vault_business', 'v_sat_dhis2_org_unit_current') }} ou
    WHERE ou.code LIKE 'FAC-%'
),
regions AS (
    SELECT
        split_part(path, '/', 3) AS region_id,
        name                     AS region_name
    FROM {{ source('nhdw_vault_business', 'v_sat_dhis2_org_unit_current') }}
    WHERE level::int = 2
)
SELECT
    ou.org_unit_code,
    ou.org_unit_name,
    ou.org_unit_level,
    ou.org_unit_path,
    ou.org_unit_parent_id,
    ou.facility_tier,
    r.region_id,
    r.region_name
FROM ou
LEFT JOIN regions r USING (region_id)
