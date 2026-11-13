# Data quality: detector, thresholds and incidents

The data-quality detector raises incidents from signals the pipeline already produces, deduplicates them, keeps their full history and summarises them as governance KPIs. Incidents are shown in the admin console; there is no e-mail notification.

## Components

| Component | Location | Notes |
|---|---|---|
| Threshold store | `ops_governance.dq_threshold` (`3_storage/postgres/init/38_governance.sql`) | SLA hours and UI bands; CHECK constraints; seed rows marked `managed_by='seed'`; consumers fall back to coded defaults |
| Incident record | `ops_governance.dq_flag` (`fingerprint`, `updated_at`, `resolved_at`) and the append-only `dq_flag_event` | one open flag per finding; every transition recorded; triggers maintain `updated_at` |
| Detector | `POST /api/dq/detect` (`6_access-points/api/nhdw-gateway/app/routes/dq.py`) | service token (`dq-detect-secrets`) or admin bearer; audit-logged; classes `dbt_test` (fk_orphan and not_null in `key->>'test_class'`), `stale_ingest`, `volume_outlier`, `mdm_backlog` |
| Queue and KPIs | `GET /api/dq/flags` (paged), `GET /api/dq/kpi` | read by the admin data-quality page |
| Schedule | `5_orchestrator/dags/dq_detection.py` | daily at 06:15, one active run, one retry |
| Deadlines | `reporting_deadlines` seed, joined into `mart_data_quality` | cadence, offset and grace; `timeliness_pct` is NULL because the loads carry no per-report arrival time |
| Bring-up | first detection run at boot (non-fatal), checks in `verify-system.sh`, `seed-app-security.sh` applies `38_governance.sql` on existing clusters | |

## Governance KPIs (`GET /api/dq/kpi`)

- **open_by**: open incidents by class, severity and source.
- **raised_weekly**: newly raised incidents per ISO week, from `dq_flag_event`.
- **avg_hours_to_ack**: mean hours from raise to first acknowledgement (`dq_ack`); null until an acknowledgement exists.
- **avg_hours_to_resolve**: mean hours from raise to `resolved_at`; null until an incident is resolved.
- **reopened_fingerprints**: findings that recurred after being resolved.
- **steward**: open MDM match candidates and decisions made.

Null KPIs are displayed as n/a, not as zero.

## Behaviour

- Re-running the detector is idempotent. An unchanged finding updates the open flag (description, severity, `updated_at`) without adding history rows.
- `wontfix` closes a finding permanently; `resolved` allows it to be raised again as a new incident.
- A threshold change applies to detections from then on. Rows with `managed_by='runtime'` are kept across re-seeds.
