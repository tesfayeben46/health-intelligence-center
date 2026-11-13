/*
    mart_supply_pipeline: the vaccine procurement pipeline, one row per open
    purchase order (status ≠ Delivered), decorated with commodity name and the
    destination hub's region. Backs rm-pipeline (a searchable/sortable PO table).

    ETA is computed live (expected_delivery_date - CURRENT_DATE) so it stays
    accurate regardless of when the source .bak was packed. region_id enables
    geography scoping (a regional user sees only their hub's orders).

    Grain: purchase_order. Reads the current-record view (insert-only sat).
*/
WITH po AS (
    SELECT
        s.po_number, s.item_id, s.environment_id, s.supplier_name,
        s.quantity, s.order_date, s.expected_delivery_date, s.status
    FROM {{ source('nhdw_vault_raw', 'hub_hcmis_purchase_order') }} h
    JOIN {{ source('nhdw_vault_business', 'v_sat_hcmis_purchase_order_current') }} s
        USING (hk_hcmis_purchase_order)
),
item AS (
    SELECT hi.item_id::int AS item_id, isat.full_item_name AS commodity_name
    FROM {{ source('nhdw_vault_raw', 'hub_hcmis_item') }} hi
    JOIN {{ source('nhdw_vault_business', 'v_sat_hcmis_item_current') }} isat
        USING (hk_hcmis_item)
),
env AS (
    SELECT he.environment_id::int AS environment_id, esat.region_id
    FROM {{ source('nhdw_vault_raw', 'hub_hcmis_environment') }} he
    JOIN {{ source('nhdw_vault_business', 'v_sat_hcmis_environment_current') }} esat
        USING (hk_hcmis_environment)
)
SELECT
    po.po_number                                         AS po_id,
    item.commodity_name,
    env.region_id,
    po.supplier_name,
    po.quantity::bigint                                  AS qty,
    po.order_date::date                                  AS order_date,
    po.expected_delivery_date::date                      AS expected_delivery_date,
    (po.expected_delivery_date::date - CURRENT_DATE)     AS eta_days,
    po.status
FROM po
LEFT JOIN item ON item.item_id = po.item_id::int
LEFT JOIN env  ON env.environment_id = po.environment_id::int
WHERE po.status <> 'Delivered'
ORDER BY eta_days
