-- Synthetic matcher suggestions for the MDM test suites (ops store).
--
-- Seven pending candidates on seven DIFFERENT source records, each pointing
-- at a master of tests/fixtures/mdm_workspace_vault.sql. The left key is the
-- hex of the source hub hash, exactly as the matcher writes it, so the
-- workspace comparison finds every record and the maker-checker suite has
-- the distinct records it needs.
INSERT INTO ops_governance.mdm_match_candidate
    (deployment_context, entity_class_code, left_source_system_code, left_source_record_key,
     right_source_system_code, right_source_record_key, suggested_master_bk, score, match_rule,
     match_features, status, matcher_run_uid, matcher_version)
SELECT 'ET', 'facility', sys, encode(sha256(convert_to(k, 'UTF8')), 'hex'),
       'NHDW.conformed', bk, bk, score, rule,
       jsonb_build_object('source_name', name), 'pending',
       '00000000-0000-4000-8000-0000000000c1', '0.1.0'
FROM (VALUES
  ('hcmis:3',    'HCMIS',            'Test Valley Health Post',   'FAC-TS-HP-000003', 1.00, 'name_exact'),
  ('hcmis:2',    'HCMIS',            'Test Ridge Health Post',    'FAC-TS-HP-000003', 1.00, 'name_ambiguous'),
  ('dhis2:ou-3', 'DHIS2',            'Test Valley Health Post',   'FAC-TS-HP-000002', 0.88, 'name_probabilistic'),
  ('hcmis:1',    'HCMIS',            'Test Kebele Health Center', 'FAC-TS-HP-000002', 0.87, 'name_probabilistic'),
  ('dhis2:ou-6', 'DHIS2',            'Test Woreda One',           'FAC-TS-HC-000001', 0.86, 'name_probabilistic'),
  ('mfr:org-5',  'MFR.organization', ' test Lowland Pharmacy',    'FAC-TS-HP-000002', 0.86, 'name_probabilistic'),
  ('dhis2:ou-2', 'DHIS2',            'Test Ridge Health Post',    'FAC-TS-HC-000001', 0.85, 'name_probabilistic')
) AS c(k, sys, name, bk, score, rule);
