# IDSR seeders

Ethiopia reports weekly disease surveillance (IDSR/PHEM) through DHIS2, so IDSR is not a separate replica: these two scripts write generated surveillance and population data into the DHIS2 replica (`../DHIS2`) through its REST API. The DHIS2 ingestor then pulls them like any other data set. **All values are synthetic.**

## Scripts

| Script | What it generates |
|---|---|
| `seed_idsr_dhis2.py` | Data elements `IDSR-<disease>-C` (confirmed cases) and `IDSR-<disease>-D` (deaths) for ten diseases; the weekly data set `IDSR_WEEKLY`, assigned to a fixed subset of woredas (`N_WOREDAS` per region); and 53 weeks of values ending 2026-W27. |
| `seed_population_dhis2.py` | Data element `POP-TOTAL` and yearly data set `ANNUAL_POPULATION` for every woreda, with values for 2025 and 2026. |

## How the data is generated

- **Cases.** Each disease has a seasonal curve, a regional weight and a case-fatality rate. Climate-sensitive diseases are coupled to the generated rainfall series (`bundle/seeds/sources/climate/weekly.csv`, from `../Climate/generate_climate.py`) with a per-disease lag. The `OUTBREAKS` list injects fixed outbreaks (disease, region, week, magnitude). Outbreak detectors and the climate model downstream recover these planted signals. A fixed RNG seed makes the output reproducible.
- **Population.** Each woreda gets a value between 20,000 and 300,000 derived from a hash of its code; 2026 is 2025 grown by 2.6%. These are not census figures.

## Usage

Both run automatically: `../DHIS2/scripts/seed_ethiopia.py` ends by calling `seed_idsr()`, which ends by calling `seed_population()`. To run standalone against a seeded DHIS2:

```bash
python3 0_external/sources/Climate/generate_climate.py       # rainfall CSV, if absent
python3 0_external/sources/IDSR/seed_idsr_dhis2.py --base http://127.0.0.1:8080
```

Both accept `--base`, `--user` and `--password` (defaults from `DHIS2_BASE_URL`, `DHIS2_ADMIN_USER`, `DHIS2_ADMIN_PASSWORD`). They are idempotent: UIDs are deterministic and values are upserted. The population seeder skips when `POP-TOTAL` values already exist.

## Notes

- The data set periods are opened with a full-object PUT after the metadata import; without it DHIS2 rejects the backfilled weeks with E7644.
- The ML input mart `bi_idsr.mart_ml_disease_input` reads cases and `POP-TOTAL` from the vault after ingestion.
