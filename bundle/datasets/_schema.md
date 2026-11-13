# Dataset YAML schema

Every dataset file in this folder follows the structure below. No validator
enforces it yet; reviewers check new entries against it.

```yaml
dataset:
  # Identification
  slug: <kebab-case-identifier>      # required, globally unique
  title: <human title>               # required
  family: mart-internal | mart-public | producer-inbox | producer-payload | source-replica | feature-store
                                      # required

  # Where the data lives (skip for producer-payload: the payload is in
  # transit at upload time, not yet at a physical address)
  source_system: DHIS2 | HCMIS | EthioEMR | mBrana | submitted | derived
  schema:        <db schema or null>
  table:         <table or view name or null>

  # Information governance
  data_classes: [DC.ENC, DC.CND, DC.OBS, DC.ORD, DC.STK, DC.DOC,
                 DC.SPC, DC.COV, DC.PRF, DC.PRO]  # any subset; see docs/security.md
  sensitivity:   default | HIV | mental-health | GBV | reproductive
                 | genetic | substance-use | biometric              # peak label
  subjects:      [PT, HP, FAC, PRD]                                # patient, health professional, facility, product
  contains_phi:  true | false
  pii_fields:    [list of explicit identifier columns; [] if none]

  # Access control
  owner_role:    R-* role id                                       # accountable owner
  visible_to:    [R-* role ids that may read]
  writable_by:   [R-* role ids that may write]
  k_anon_min:    integer or null                                   # for mart-public
  retention_days: integer

  # Shape
  format:        csv | parquet | fhir-bundle | json | sql-view | sql-table
  description:   <paragraph>

  # Audit trail
  last_reviewed_at: YYYY-MM-DD
  last_reviewer:    <username or role id>
```

## Consumers

No code in the repository loads these files at present (see `README.md`). A loader
should iterate `*.yaml` in lexical order, validate each entry against this
structure, and reject duplicate slugs.
