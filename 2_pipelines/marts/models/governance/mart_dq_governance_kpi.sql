/*
    mart_dq_governance_kpi: governance KPIs distilled from the durable
    incident history, long format: (kpi_code, dim_a, dim_b, value_num).

    Mirrors the gateway's GET /api/dq/kpi semantics so the two surfaces can
    never tell different stories; definitions live in
    docs/data-quality-plane.md. The steward KPIs (backlog, decisions) stay
    on the API surface only: their stores belong to the MDM workstream and
    its read interface exposes only the decision log. NULL-valued latency KPIs are emitted as
    NULL rows (unmeasured ≠ zero). Grain: kpi_code by dims.
*/
WITH flags AS (SELECT * FROM {{ ref('mart_dq_incidents') }})

SELECT 'open_total'::text AS kpi_code, NULL::text AS dim_a, NULL::text AS dim_b,
       count(*)::numeric AS value_num
FROM flags WHERE status = 'open'

UNION ALL
SELECT 'open_by_class', error_class, severity, count(*)::numeric
FROM flags WHERE status = 'open' GROUP BY 2, 3

UNION ALL
SELECT 'raised_weekly', to_char(date_trunc('week', ts), 'YYYY-MM-DD'), NULL, count(*)::numeric
FROM {{ source('vault_governance', 'dq_flag_event') }}
WHERE to_status = 'open' GROUP BY 2

UNION ALL
SELECT 'avg_hours_to_ack', NULL, NULL, avg(hours_to_first_ack)
FROM flags

UNION ALL
SELECT 'avg_hours_to_resolve', NULL, NULL, avg(hours_to_resolve)
FROM flags

UNION ALL
SELECT 'resolved_total', NULL, NULL,
       count(*) FILTER (WHERE resolved_at IS NOT NULL)::numeric
FROM flags

UNION ALL
SELECT 'reopened_fingerprints', NULL, NULL, count(*)::numeric
FROM (SELECT 1 FROM {{ source('vault_governance', 'dq_flag') }}
      WHERE fingerprint IS NOT NULL
      GROUP BY fingerprint HAVING count(*) > 1) r

