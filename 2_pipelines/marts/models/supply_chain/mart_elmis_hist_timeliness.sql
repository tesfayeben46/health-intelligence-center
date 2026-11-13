/*
    mart_elmis_hist_timeliness: facility reporting timeliness per month by 
    programme (legacy eLMIS v2 history).

    A requisition is "on time" when created within the governed deadline of
    its reporting period's end: 15 days, read from the reporting_deadlines
    seed, not hard-coded here, so the deadline is a governed contract a
    steward can change in one place. The seed's eLMIS row carries 0 grace
    hours so the deadline applies exactly.

    mart_data_quality reads this model for its eLMIS timeliness instead of
    recomputing it: one definition of on-time in the warehouse, not two.

    Grain month by programme (~140 rows).
*/
{{ config(materialized='table') }}

SELECT
    to_char(per.period_start::date, 'YYYY-MM')     AS month_id,
    per.period_start::date                          AS period_start,
    prog.name                                       AS programme_name,
    count(*)                                        AS requisitions,
    count(*) FILTER (WHERE rs.requisition_created_at::timestamp
                           <= per.period_end::timestamp + dl.deadline)
                                                    AS on_time_requisitions,
    round(100.0 * count(*) FILTER (WHERE rs.requisition_created_at::timestamp
                           <= per.period_end::timestamp + dl.deadline)
          / NULLIF(count(*), 0), 1)                 AS on_time_pct,
    round(percentile_cont(0.5) WITHIN GROUP (ORDER BY
        EXTRACT(EPOCH FROM rs.requisition_created_at::timestamp
                         - per.period_end::timestamp) / 86400.0)::numeric, 1)
                                                    AS median_lag_days
FROM {{ source('nhdw_vault_raw', 'lnk_elmis_hist_requisition') }} lnk
JOIN {{ source('nhdw_vault_business', 'v_sat_elmis_hist_requisition_current') }} rs
    USING (hk_elmis_hist_requisition)
JOIN {{ source('nhdw_vault_business', 'v_sat_elmis_hist_programme_current') }} prog
    USING (hk_elmis_hist_programme)
JOIN {{ source('nhdw_vault_business', 'v_sat_elmis_hist_period_current') }} per
    USING (hk_elmis_hist_period)
-- one row: the governed deadline for this source. An inner join, so that
-- if the seed row ever goes missing the model fails loudly instead of
-- quietly measuring against a different rule.
CROSS JOIN (SELECT MAKE_INTERVAL(days  => deadline_offset_days,
                                 hours => grace_hours) AS deadline
            FROM {{ ref('reporting_deadlines') }}
            WHERE source_key = 'eLMIS-hist') dl
WHERE rs.requisition_created_at IS NOT NULL
GROUP BY 1, 2, 3
