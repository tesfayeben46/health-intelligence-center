-- eLMIS is the only source in the warehouse with a measured reporting
-- timeliness: every other source lands as a bulk snapshot, so its
-- timeliness_pct is NULL by design. That makes this one row easy to lose
-- silently: mart_data_quality reaches it through mart_elmis_hist_timeliness,
-- which in turn inner-joins the reporting_deadlines seed on source_key. Rename
-- or drop that seed row and the whole source disappears from the quality mart
-- instead of erroring.
--
-- Two other things worth catching here: the public projection must not carry
-- eLMIS (a foreign archive does not belong on a national transparency
-- scorecard), and the measured percentage must stay a percentage.
--
-- Returns rows when the test FAILS.

SELECT 'eLMIS timeliness missing from mart_data_quality' AS failure
WHERE (SELECT count(*) FROM {{ ref('mart_data_quality') }}
       WHERE source = 'eLMIS-hist' AND timeliness_pct IS NOT NULL) = 0

UNION ALL

SELECT 'eLMIS leaked into the public transparency scorecard' AS failure
WHERE (SELECT count(*) FROM {{ ref('mart_public_data_quality') }}
       WHERE source = 'eLMIS-hist') > 0

UNION ALL

SELECT 'measured timeliness outside [0,100]' AS failure
WHERE (SELECT count(*) FROM {{ ref('mart_data_quality') }}
       WHERE timeliness_pct IS NOT NULL
         AND (timeliness_pct < 0 OR timeliness_pct > 100)) > 0
