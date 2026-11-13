# EthioEMR schema reference

The replica uses the OpenMRS data model as restored from `../sample-data/clean_openmrs.sql`; that dump is the authoritative DDL. To extract the DDL or list the tables once the dump is available:

```bash
grep -E "^(CREATE TABLE|ALTER TABLE|--|/\*|DROP TABLE)" ../sample-data/clean_openmrs.sql > openmrs_schema.sql
sed -n 's/^CREATE TABLE `\([a-z_]*\)`.*/\1/p' ../sample-data/clean_openmrs.sql | sort -u
```

## Table groups

- **OpenMRS core**: `patient`, `person`, `person_name`, `person_address`, `person_attribute`, `encounter`, `encounter_provider`, `encounter_type`, `encounter_role`, `encounter_diagnosis`, `obs`, `visit`, `visit_type`, `visit_attribute`, `concept`, `concept_name`, `concept_class`, `concept_datatype`, `location`, `location_attribute`, `provider`, `provider_attribute`, `users`, `role`, `privilege`, `form`, `field`, `form_field`, `program`, `patient_program`, `patient_state`, `drug`, `drug_ingredient`, `drug_order`, `orders`, `order_type`, `order_set`, `conditions`.
- **Bahmni clinical modules**: `appointment_service`, `appointment_service_type`, `appointment_speciality`, `patient_appointment*`, `bed*`, `allergy`, `allergy_reaction`.
- **FHIR2 module configuration**: `fhir_concept_source`, `fhir_patient_identifier_system`, `fhir_diagnostic_report*`, `fhir_encounter_class_map`, `fhir_observation_category_map`, `fhir_contact_point_map`, `fhir_reference`, `fhir_task*`.
- **Address hierarchy**: `address_hierarchy_level`, `address_hierarchy_entry`, `address_hierarchy_address_to_entry_map`.
- **Replica marker**: `synth_meta` (added by `../scripts/synthesize.py`: synthesizer version and country profile).

## Key relationships

```
person ── patient (1:0..1)
person ── person_name, person_address, person_attribute (1:many)
patient ── patient_identifier (1:many)
patient ── visit (1:many)
visit  ── encounter (1:many)
encounter ── obs (1:many)
encounter ── encounter_provider (1:many)
orders ── drug_order (1:0..1)
obs ── concept (many:1)
concept ── concept_name (1:many; multilingual)
patient ── patient_program (1:many)
```

## Tables the ingestor reads

The EthioEMR ingestor (`2_pipelines/ingestor/EthioEMR`) reads `person`, `person_name`, `patient`, `patient_identifier`, `encounter`, `visit`, `obs` and `location`, with `concept_name`, `concept_numeric` and `patient_identifier_type` as reference, and maps them to FHIR-shaped records itself: Patient from `patient`, `person`, `person_name` and `patient_identifier`; Encounter from `encounter` and `visit`; Observation from `obs` and `concept_name`. The OpenMRS FHIR2 module tables are present in the schema, but no OpenMRS application runs to serve them.
