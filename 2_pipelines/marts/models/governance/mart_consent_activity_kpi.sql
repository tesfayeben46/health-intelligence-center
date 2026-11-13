/*
    mart_consent_activity_kpi: Governance KPI strip (bi_governance).
    Three rows (label, value) covering current consent state:
      - "active consents"   distinct gated patients whose data the
        consent gate currently serves, i.e. patients in the gated PHI
        population not suppressed by a blanket or purpose=public-health
        revocation (the exact gate bi_demo_patient_observations applies,
        via the same consent_gate() macro, so the KPI can never drift
        from the gate)
      - "revoked (24h)"     revocations in the trailing 24 hours
      - "pending review"    revocations with `source = 'pending'` (review queue)

    Gated population: nhdw_landing.demo_patient_phi (the generated fixture
    every consent-gated mart reads). Swapping in another PHI source changes
    only the population source; the consumer contract (3 label/value rows)
    is unchanged.

    No dashboard renders this mart; it is the data contract for an
    internal-BI Governance page (a gateway fetcher and widget bind to it).
*/
{{ config(materialized='table') }}

WITH counts AS (
    SELECT
        (SELECT COUNT(DISTINCT g.patient_id)
           FROM {{ consent_gate(source('nhdw_landing', 'demo_patient_phi'),
                                patient_id_col='patient_id',
                                purpose='public-health') }} g) AS active_consents,
        (SELECT COUNT(*)
           FROM {{ source('nhdw_landing', 'consent_revocations') }}
          WHERE revoked_at >= (now() - interval '24 hours')) AS revoked_24h,
        (SELECT COUNT(*)
           FROM {{ source('nhdw_landing', 'consent_revocations') }}
          WHERE source = 'pending')                          AS pending_review
)
SELECT 'active consents'::text AS label, active_consents::bigint AS value FROM counts
UNION ALL
SELECT 'revoked (24h)'::text,           revoked_24h::bigint           FROM counts
UNION ALL
SELECT 'pending review'::text,          pending_review::bigint         FROM counts
