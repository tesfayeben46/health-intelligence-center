# Dataset registry

Each YAML file here describes one dataset: a named surface that a role group reads from or writes to, with its location, governance attributes (data classes, subjects, PHI and PII fields, sensitivity), access lists, retention and format. The structure is documented in [`_schema.md`](_schema.md).

The registry is descriptive. No loader in the repository reads these files at present; the enforced access model lives in `bundle/roles/` (OPA data permissions), `bundle/seed-pack/app/security/mart_attrs.yaml` (mart attributes) and `column_classification.yaml` (column masking). Keep the entries consistent with those sources when either changes.

## Entries

| Slug | Family | Location |
|---|---|---|
| `facility-distribution-internal` | mart-internal | `bi_facility.bi_facility_distribution` |
| `facility-distribution-public` | mart-public | `bi_public.bi_facility_distribution_public` |
| `facility-with-region-base` | mart-internal | `bi_facility.bi_dhis2_facility_with_region` |
| `submission-inbox` | producer-inbox | `nhdw_landing.submission_inbox` |
| `hmis-monthly` | producer-payload | submitted through the submission flow |
| `epi-vaccinations` | producer-payload | submitted through the submission flow |
| `stock-availability` | producer-payload | submitted through the submission flow |

## Conventions

- One file per dataset, named `<slug>.yaml`; the slug is kebab-case and globally unique.
- `family` classifies the dataset: `mart-internal` (internal marts), `mart-public` (`bi_public`, k-anonymised), `producer-inbox` (landing tables for submissions), `producer-payload` (what producers upload), `source-replica` (the replicas under `0_external/sources/`), `feature-store` (ML feature surfaces).
- Update `last_reviewed_at` and `last_reviewer` on every change. A change to `sensitivity` or `contains_phi` should be approved by a data steward (R-STEW).
