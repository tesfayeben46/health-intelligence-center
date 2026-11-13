/*
    HCMIS stocking environment (Dim.Environment): the EPSS hubs. Each hub
    carries its region_id, embedded by the source generator in Description as
    `hub_id={code} | region_id={code}`. This is the geographic key that lets the
    supply marts cut stock by region (the fact grain is item by environment by 
    mode by date; environment -> hub -> region_id). NK: environment_id.
*/
{{ config(materialized='view') }}

WITH src AS (
    SELECT
        environment_id,
        name,
        environment_code,
        environment_group_code,
        description,
        -- region_id is embedded in the seeded Description as ISO `region_id=ET-OR`;
        -- the rest of the warehouse keys regions by the 2-letter code (OR), so
        -- strip the `ET-` prefix to line up with mart_facility_capacity / IDSR.
        extract(description, 'region_id=ET-([A-Za-z]+)') AS region_id
    FROM {{ source('raw', 'hcmiswdm__dim_environment') }}
),
hashed AS (
    SELECT *, {{ source_record_hash(['environment_id','name','environment_code','environment_group_code','description','region_id']) }} AS source_record_hash
    FROM src
),
deduped AS (
    SELECT *, row_number() OVER (
        PARTITION BY environment_id
        ORDER BY source_record_hash
    ) AS rn
    FROM hashed
)
SELECT
    environment_id, name, environment_code, environment_group_code,
    region_id, description,
    now64() AS load_ts,
    source_record_hash
FROM deduped WHERE rn = 1
