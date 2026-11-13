/*
    mart_dq_incidents: the durable DQ incident record on the governed mart
    path (bi_governance), one row per flag with its measured lifecycle.

    Source: the read-only FDW seam over ops_governance (40_dq_read.sql):
    the same numbers the admin DQ page reads through the gateway,
    queryable via Trino/bi_reader (Superset, SQL editor, exports) like
    every other governed number. The gateway remains the writer; this mart
    is read-only reporting.

    Latencies are NULL until measured (no ack => no hours_to_first_ack) and
    never zero. Grain: incident flag.
*/
SELECT
    f.id::text                                          AS flag_id,
    f.raised_at,
    f.updated_at,
    f.resolved_at,
    f.raised_by,
    f.source,
    f.table_name,
    f.error_class,
    f.severity,
    f.status,
    f.description,
    (SELECT count(*) FROM {{ source('vault_governance', 'dq_flag_event') }} e
      WHERE e.flag_id = f.id)                           AS events,
    (SELECT count(*) FROM {{ source('vault_governance', 'dq_ack') }} a
      WHERE a.flag_id = f.id)                           AS acks,
    ROUND(EXTRACT(epoch FROM (
        (SELECT min(a.acked_at) FROM {{ source('vault_governance', 'dq_ack') }} a
          WHERE a.flag_id = f.id) - f.raised_at)) / 3600, 1) AS hours_to_first_ack,
    ROUND(EXTRACT(epoch FROM (f.resolved_at - f.raised_at)) / 3600, 1)
                                                        AS hours_to_resolve
FROM {{ source('vault_governance', 'dq_flag') }} f
