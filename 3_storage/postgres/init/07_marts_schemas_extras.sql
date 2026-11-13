-- Additional BI mart schemas: HMIS coverage, IDSR disease surveillance,
-- service performance, data quality, governance and MDM. bi_facility,
-- bi_supply, bi_epi, bi_public and bi_staging are created in 01_schemas.sql.
--
-- Idempotent.

CREATE SCHEMA IF NOT EXISTS bi_hmis    AUTHORIZATION vault_writer;
COMMENT ON SCHEMA bi_hmis IS
    'Marts: HMIS indicator coverage (region/woreda by month by indicator).';

CREATE SCHEMA IF NOT EXISTS bi_idsr    AUTHORIZATION vault_writer;
COMMENT ON SCHEMA bi_idsr IS
    'Marts: IDSR disease surveillance (week by woreda by disease) + climate-driver cross-tabs.';

CREATE SCHEMA IF NOT EXISTS bi_service AUTHORIZATION vault_writer;
COMMENT ON SCHEMA bi_service IS
    'Marts: Service performance: HSTP-II attainment, programme cascades (HIV/TB/MNCH/NCD).';

CREATE SCHEMA IF NOT EXISTS bi_quality AUTHORIZATION vault_writer;
COMMENT ON SCHEMA bi_quality IS
    'Marts: Data quality / completeness / timeliness per facility by source.';

CREATE SCHEMA IF NOT EXISTS bi_governance AUTHORIZATION vault_writer;
COMMENT ON SCHEMA bi_governance IS
    'Marts: Governance: consent activity KPIs (consent_gate over the PHI fixture).';

GRANT USAGE ON SCHEMA bi_hmis, bi_idsr, bi_service, bi_quality, bi_governance TO bi_reader;

ALTER DEFAULT PRIVILEGES FOR ROLE vault_writer IN SCHEMA bi_hmis
    GRANT SELECT ON TABLES TO bi_reader;
ALTER DEFAULT PRIVILEGES FOR ROLE vault_writer IN SCHEMA bi_idsr
    GRANT SELECT ON TABLES TO bi_reader;
ALTER DEFAULT PRIVILEGES FOR ROLE vault_writer IN SCHEMA bi_service
    GRANT SELECT ON TABLES TO bi_reader;
ALTER DEFAULT PRIVILEGES FOR ROLE vault_writer IN SCHEMA bi_quality
    GRANT SELECT ON TABLES TO bi_reader;
ALTER DEFAULT PRIVILEGES FOR ROLE vault_writer IN SCHEMA bi_governance
    GRANT SELECT ON TABLES TO bi_reader;

-- bi_mdm: MDM golden records and discovered relationships.
CREATE SCHEMA IF NOT EXISTS bi_mdm AUTHORIZATION vault_writer;
COMMENT ON SCHEMA bi_mdm IS
  'MDM golden records + discovered relationships: steward-reviewed candidates, never silent same-as links.';
GRANT USAGE ON SCHEMA bi_hmis, bi_idsr, bi_service, bi_quality, bi_mdm TO bi_reader;
ALTER DEFAULT PRIVILEGES FOR ROLE vault_writer IN SCHEMA bi_mdm
    GRANT SELECT ON TABLES TO bi_reader;
