-- Seeds the synthetic demo PHI fixture nhdw_landing.demo_patient_phi
-- (defined in 01_schemas.sql). bi_staging.bi_demo_patient_observations and the
-- widgets on it (sv-facility-outliers, EMR-side agreement) read it. This data
-- has no pipeline source, so it is seeded deterministically in SQL.
--
-- Shape: about 30 EMR facilities by 12 recent months by 6 observation types,
-- patient-level rows. Three facilities are deliberate volume outliers (factor
-- 3 or 0.2) so the z-score outlier widget has signal. A set of consent
-- revocations exercises the consent gate (those patients' rows must disappear
-- downstream).
-- Idempotent: TRUNCATE + INSERT. Deterministic: setseed(0.42).

\connect nhdw_vault

TRUNCATE nhdw_landing.demo_patient_phi;
SELECT setseed(0.42);

INSERT INTO nhdw_landing.demo_patient_phi (patient_id, encounter_date, observation, facility_code)
SELECT DISTINCT ON (patient_id, encounter_date, observation)
    'PAT-' || lpad(((f.fi * 7919 + m.mi * 104729 + o.oi * 1299709 + s.n) % 900000 + 100000)::text, 6, '0')
        AS patient_id,
    (date_trunc('month', now())::date - (m.mi || ' months')::interval
        + ((abs(hashtext(f.fi::text || m.mi || o.oi || s.n)) % 28) || ' days')::interval)::date
        AS encounter_date,
    o.obs AS observation,
    f.code AS facility_code
FROM (
    -- 30 EMR facilities: FAC-<region>-HC-demoXX, 3 outliers get a volume factor
    SELECT gi AS fi,
           'FAC-' || (ARRAY['AA','OR','AM','TI','SO','SI','DD','HA','GA','BG','SW','CE'])[(gi % 12) + 1]
                  || '-HC-demo' || lpad(gi::text, 2, '0') AS code,
           CASE WHEN gi IN (3, 17) THEN 3.0 WHEN gi = 24 THEN 0.2 ELSE 1.0 END AS vol
    FROM generate_series(1, 30) gi
) f
CROSS JOIN (SELECT gi AS mi FROM generate_series(0, 11) gi) m
CROSS JOIN (
    -- per-indicator monthly base volumes per facility, tuned to the DHIS2
    -- per-reporting-facility rates so cross-feed agreement is meaningful and
    -- the outlier facilities surface as discrepancies
    SELECT gi AS oi,
           (ARRAY['ANC visit','Penta3 dose','TB screening',
                  'HIV test','Malaria RDT','Blood pressure'])[gi] AS obs,
           (ARRAY[52, 40, 25, 140, 136, 163])[gi] AS base  -- ≈ DHIS2 per-facility rates (ANC1,PENTA3,TB,HIV-TEST,MAL-RDT,HTN-SCRN)
    FROM generate_series(1, 6) gi
) o
CROSS JOIN LATERAL (
    SELECT generate_series(1, greatest(1, round(o.base * f.vol * (0.75 + random() * 0.5))::int)) AS n
) s
ORDER BY patient_id, encounter_date, observation;

-- Consent revocations: about 40 patients revoke public-health use, and the
-- consent_gate macro must exclude them.
TRUNCATE nhdw_landing.consent_revocations;
INSERT INTO nhdw_landing.consent_revocations
    (revocation_id, patient_id, purpose, revoked_at, revoked_by, source)
SELECT 'REV-' || patient_id, patient_id, 'public-health',
       now() - interval '30 days', 'demo-consent-desk', 'manual'
FROM (SELECT DISTINCT patient_id FROM nhdw_landing.demo_patient_phi
      WHERE (abs(hashtext(patient_id)) % 100) < 2
      LIMIT 40) p;
