-- Programme sample data: synthetic rows for the per-programme BI detail components.
-- The schema (tables, the bi_programme / bi_facility views, grants) lives with the
-- system in 3_storage/postgres/init/03_program_demo.sql; this file is the swappable
-- data (seed pack). Idempotent TRUNCATE + INSERT; requires those tables to exist.

TRUNCATE TABLE bi_facility.programme_metrics;

INSERT INTO bi_facility.programme_metrics(region_code, program_code, period_month, metric, value)
VALUES
  -- HIV cascade by region (latest quarter)
  ('OR','HIV','2026-Q1','diagnosed', 226400), ('OR','HIV','2026-Q1','on_treatment', 203800), ('OR','HIV','2026-Q1','suppressed', 181000),
  ('AM','HIV','2026-Q1','diagnosed', 154700), ('AM','HIV','2026-Q1','on_treatment', 138900), ('AM','HIV','2026-Q1','suppressed', 124100),
  ('AA','HIV','2026-Q1','diagnosed', 71500),  ('AA','HIV','2026-Q1','on_treatment', 67200),  ('AA','HIV','2026-Q1','suppressed', 60800),
  ('SO','HIV','2026-Q1','diagnosed', 38200),  ('SO','HIV','2026-Q1','on_treatment', 32600),  ('SO','HIV','2026-Q1','suppressed', 27400),
  ('TI','HIV','2026-Q1','diagnosed', 47900),  ('TI','HIV','2026-Q1','on_treatment', 42100),  ('TI','HIV','2026-Q1','suppressed', 36700),
  ('SI','HIV','2026-Q1','diagnosed', 41200),  ('SI','HIV','2026-Q1','on_treatment', 37800),  ('SI','HIV','2026-Q1','suppressed', 33100),
  ('SE','HIV','2026-Q1','diagnosed', 32600),  ('SE','HIV','2026-Q1','on_treatment', 28700),  ('SE','HIV','2026-Q1','suppressed', 25300),

  -- HIV incidence trend (per region per quarter, last 4 quarters)
  ('OR','HIV','2025-Q2','new_diagnoses', 14200),
  ('OR','HIV','2025-Q3','new_diagnoses', 15100),
  ('OR','HIV','2025-Q4','new_diagnoses', 14700),
  ('OR','HIV','2026-Q1','new_diagnoses', 15300),
  ('AM','HIV','2025-Q2','new_diagnoses', 9100),
  ('AM','HIV','2025-Q3','new_diagnoses', 9700),
  ('AM','HIV','2025-Q4','new_diagnoses', 9900),
  ('AM','HIV','2026-Q1','new_diagnoses', 10400),

  -- TB cascade
  ('OR','TB','2026-Q1','notified', 24600), ('OR','TB','2026-Q1','on_treatment', 22100), ('OR','TB','2026-Q1','treatment_success', 19800),
  ('AM','TB','2026-Q1','notified', 16200), ('AM','TB','2026-Q1','on_treatment', 14700), ('AM','TB','2026-Q1','treatment_success', 13200),
  ('AA','TB','2026-Q1','notified', 6100),  ('AA','TB','2026-Q1','on_treatment', 5800),  ('AA','TB','2026-Q1','treatment_success', 5400),
  ('SO','TB','2026-Q1','notified', 4900),  ('SO','TB','2026-Q1','on_treatment', 4300),  ('SO','TB','2026-Q1','treatment_success', 3700),
  ('TI','TB','2026-Q1','notified', 5800),  ('TI','TB','2026-Q1','on_treatment', 5200),  ('TI','TB','2026-Q1','treatment_success', 4600),
  ('SI','TB','2026-Q1','notified', 4400),  ('SI','TB','2026-Q1','on_treatment', 3900),  ('SI','TB','2026-Q1','treatment_success', 3500),

  ('OR','TB','2026-Q1','mdr_cases', 412),
  ('AM','TB','2026-Q1','mdr_cases', 287),
  ('AA','TB','2026-Q1','mdr_cases', 96),
  ('SO','TB','2026-Q1','mdr_cases', 134),

  -- Malaria
  ('OR','MALARIA','2026-Q1','suspected', 412600), ('OR','MALARIA','2026-Q1','tested', 388200), ('OR','MALARIA','2026-Q1','confirmed', 87400),
  ('AM','MALARIA','2026-Q1','suspected', 278100), ('AM','MALARIA','2026-Q1','tested', 261400), ('AM','MALARIA','2026-Q1','confirmed', 52300),
  ('GA','MALARIA','2026-Q1','suspected', 81200),  ('GA','MALARIA','2026-Q1','tested', 76900),  ('GA','MALARIA','2026-Q1','confirmed', 31700),
  ('BE','MALARIA','2026-Q1','suspected', 64300),  ('BE','MALARIA','2026-Q1','tested', 60800),  ('BE','MALARIA','2026-Q1','confirmed', 24100),
  ('AF','MALARIA','2026-Q1','suspected', 47200),  ('AF','MALARIA','2026-Q1','tested', 44600),  ('AF','MALARIA','2026-Q1','confirmed', 18900),
  ('SO','MALARIA','2026-Q1','suspected', 81900),  ('SO','MALARIA','2026-Q1','tested', 76200),  ('SO','MALARIA','2026-Q1','confirmed', 34800),

  -- malaria seasonality (monthly count of confirmed)
  ('OR','MALARIA','2025-10','confirmed', 32100),
  ('OR','MALARIA','2025-11','confirmed', 36400),
  ('OR','MALARIA','2025-12','confirmed', 41200),
  ('OR','MALARIA','2026-01','confirmed', 29800),
  ('OR','MALARIA','2026-02','confirmed', 28100),
  ('OR','MALARIA','2026-03','confirmed', 29500),

  -- EPI (immunization coverage %)
  ('OR','EPI','2026-Q1','dpt3_coverage_pct',   89), ('OR','EPI','2026-Q1','measles_coverage_pct', 85), ('OR','EPI','2026-Q1','bcg_coverage_pct',     91),
  ('AM','EPI','2026-Q1','dpt3_coverage_pct',   86), ('AM','EPI','2026-Q1','measles_coverage_pct', 82), ('AM','EPI','2026-Q1','bcg_coverage_pct',     89),
  ('AA','EPI','2026-Q1','dpt3_coverage_pct',   94), ('AA','EPI','2026-Q1','measles_coverage_pct', 93), ('AA','EPI','2026-Q1','bcg_coverage_pct',     96),
  ('SO','EPI','2026-Q1','dpt3_coverage_pct',   71), ('SO','EPI','2026-Q1','measles_coverage_pct', 68), ('SO','EPI','2026-Q1','bcg_coverage_pct',     74),
  ('AF','EPI','2026-Q1','dpt3_coverage_pct',   74), ('AF','EPI','2026-Q1','measles_coverage_pct', 70), ('AF','EPI','2026-Q1','bcg_coverage_pct',     78),
  ('TI','EPI','2026-Q1','dpt3_coverage_pct',   83), ('TI','EPI','2026-Q1','measles_coverage_pct', 79), ('TI','EPI','2026-Q1','bcg_coverage_pct',     86),
  ('GA','EPI','2026-Q1','dpt3_coverage_pct',   77), ('GA','EPI','2026-Q1','measles_coverage_pct', 72), ('GA','EPI','2026-Q1','bcg_coverage_pct',     81),
  ('SI','EPI','2026-Q1','dpt3_coverage_pct',   88), ('SI','EPI','2026-Q1','measles_coverage_pct', 84), ('SI','EPI','2026-Q1','bcg_coverage_pct',     90),

  -- MNCH
  ('OR','MNCH','2026-Q1','anc4_coverage_pct',         62), ('OR','MNCH','2026-Q1','skilled_birth_pct',   58), ('OR','MNCH','2026-Q1','postnatal_pct',  46),
  ('AM','MNCH','2026-Q1','anc4_coverage_pct',         57), ('AM','MNCH','2026-Q1','skilled_birth_pct',   52), ('AM','MNCH','2026-Q1','postnatal_pct',  41),
  ('AA','MNCH','2026-Q1','anc4_coverage_pct',         79), ('AA','MNCH','2026-Q1','skilled_birth_pct',   84), ('AA','MNCH','2026-Q1','postnatal_pct',  68),
  ('SO','MNCH','2026-Q1','anc4_coverage_pct',         34), ('SO','MNCH','2026-Q1','skilled_birth_pct',   28), ('SO','MNCH','2026-Q1','postnatal_pct',  21),
  ('TI','MNCH','2026-Q1','anc4_coverage_pct',         58), ('TI','MNCH','2026-Q1','skilled_birth_pct',   51), ('TI','MNCH','2026-Q1','postnatal_pct',  39),
  ('AF','MNCH','2026-Q1','anc4_coverage_pct',         38), ('AF','MNCH','2026-Q1','skilled_birth_pct',   31), ('AF','MNCH','2026-Q1','postnatal_pct',  24),
  ('SI','MNCH','2026-Q1','anc4_coverage_pct',         61), ('SI','MNCH','2026-Q1','skilled_birth_pct',   56), ('SI','MNCH','2026-Q1','postnatal_pct',  44);
TRUNCATE bi_facility.submission_funnel;
INSERT INTO bi_facility.submission_funnel VALUES
  ('2026-06-08', 'HMIS',  'accepted', 14302),
  ('2026-06-08', 'HMIS',  'rejected',   318),
  ('2026-06-08', 'Stock', 'accepted',  9281),
  ('2026-06-08', 'Stock', 'rejected',    71),
  ('2026-06-08', 'EPI',   'accepted',   612),
  ('2026-06-08', 'EPI',   'rejected',    38);
TRUNCATE bi_facility.stock_status;
INSERT INTO bi_facility.stock_status VALUES
  ('Adama Hospital',        'OR', 'HIV',     'TDF/3TC/DTG',      12,  200, 'critical'),
  ('Hawassa Health Centre', 'SI', 'TB',      'RHZE',              4,   60, 'critical'),
  ('Tigray General',        'TI', 'MALARIA', 'Artemether-LU',    88,  100, 'warning'),
  ('Gambela Referral',      'GA', 'HIV',     'Cotrimoxazole',    41,  120, 'warning'),
  ('Bahir Dar PHCU',        'AM', 'HIV',     'Cotrimoxazole',   165,  120, 'ok'),
  ('Jimma Hospital',        'OR', 'TB',      'RHZE',             29,   60, 'critical'),
  ('Dire Dawa General',     'DD', 'MALARIA', 'Artemether-LU',    72,   80, 'warning'),
  ('Mekele General',        'TI', 'EPI',     'Measles MR',      210,  150, 'ok');
