# Climate source replica

A small HTTP service in the shape of a public climate data API (NASA POWER / CHIRPS style), serving weekly rainfall, temperature and humidity per Ethiopian region as CSV. The Climate ingestor (`2_pipelines/ingestor/Climate`) pulls from it into the lake. **The series is synthetic**, produced by the generator in this directory.

## Layout

```
generate_climate.py   writes bundle/seeds/sources/climate/weekly.csv
api/app.py            FastAPI service that loads the CSV at start-up and serves it
api/Dockerfile        python:3.12-slim image (nhdw/climate-api:1.0.0)
docker-compose.yml    container climate-api on port 8095, CSV directory mounted read-only
```

## Data

`generate_climate.py` emits one row per region and ISO week for the 53 weeks ending 2026-W27 (14 regions), with columns `iso_week, week_start, region_id, rainfall_mm, temp_celsius, humidity_pct, source_record_hash`. Each region has a base rainfall, temperature and lowland factor; rainfall follows the kiremt main rains and belg short rains with random noise. A fixed seed makes the output identical on every run.

The IDSR seeder (`../IDSR/seed_idsr_dhis2.py`) reads the same CSV to couple climate-sensitive case counts to rainfall with a lag, so a rainfall relationship found downstream is one the generators placed there.

## Usage

```bash
python3 0_external/sources/Climate/generate_climate.py
docker compose -f 0_external/sources/Climate/docker-compose.yml up -d --build
curl 'http://127.0.0.1:8095/api/v1/climate/weekly?region=OR&limit=5'
```

`bundle/scripts/sources-up.sh up climate` generates the CSV when it is missing and starts the service. With a non-default country profile and no CSV it skips the source, since the region profiles are Ethiopian.

## API

| Endpoint | Description |
|---|---|
| `GET /healthz` | Status and loaded row count |
| `GET /api/v1/climate/weekly` | CSV, ordered by `(week_start, region_id)`. Filters: `region`, `start`, `end` (on `week_start`), `updated_since` (incremental cursor, `week_start >` the given date), `offset`, `limit` (default 10,000). Response headers `X-Total-Count` and `X-Returned-Count`. |

The service reads the CSV once at start-up; restart it after regenerating.
