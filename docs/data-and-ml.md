# Data, dashboards and machine learning

## Warehouse

The serving layer is a set of `bi_*` marts in Postgres, built by dbt from the Data Vault. Marts are organised by subject: programme cascades (HIV, TB, malaria and others), HMIS service indicators, IDSR outbreak signals, supply and stock, facility readiness and data quality. Each mart carries the geography and programme keys that access control filters on.

[data-dictionary.md](data-dictionary.md) lists programmes, HMIS indicators with their calculation and denominator, supply commodities and the services they support, the outbreak response map, hubs and catchments, regions, and every mart with its description. It is generated from the entity model and the dbt schema files.

## Reference and master data

**Reference vocabulary (`nhdw_ref`).** An OMOP-style concept store (concepts, crosswalks, bitemporal validity) built from the entity model and curated supplements in `bundle/seeds/ref-vocab/`. The vault's resolution views join the crosswalk, so a mapping change is a data change, not a model change. Concept ids come from an append-only committed registry and are stable across rebuilds.

**Master data (MDM).** The matcher (`2_pipelines/mdm`, a Job that runs after the marts build) proposes same-entity candidates with scores and evidence: exact normalised-name matches across sources, ambiguous names resolved by administrative area, and trigram near matches. The matcher does not merge records. Data stewards review the queue in the admin console. Each decision is written to an append-only, bitemporal decision log that the vault build reads:

- accept: the source record is re-keyed onto its master on the next pipeline run;
- revert: the decision is superseded and the record falls back to its derived key;
- empty log: conformed keys are unchanged.

Facility masters are conformed hub entities. Commodity masters are vocabulary concepts; a commodity with an exact name match conforms automatically because the concept code is the normalised name. The facility dimension and marts carry `master_entity_uid` (a stable UUID derived from the conformed key) and `mdm_state` (`conformed` or `single_source`).

## Dashboards

Four dashboards sit behind the gateway, and an operator console runs on the host:

- **Public portal.** Open data, articles and headline indicators, no sign-in.
- **Internal BI.** Programme and service dashboards with region, programme and period filters, and an assistant that answers questions against the marts.
- **Admin.** Users and access, roles and permissions, data quality and lineage, the health and topology view, operator tool launchers.
- **Workshop.** Datasets, models, projects, a JupyterLab sandbox, and a review path for promoting community work.
- **Operator console.** Runs on the host, outside the gateway, for bring-up and monitoring.

Dashboards are catalogue-driven. A page is a set of widgets, each widget declares the mart it reads, and the gateway serves that read under OPA. Adding or changing a panel is a catalogue change.

The Internal BI assistant uses the same gateway and access rules as the dashboards, so its answers are limited to what the user's role can read. It uses the widget catalogue and the current page as context.

## Machine learning

Each model is a package of training code, a feature spec and parameters. It trains against the marts and registers in MLflow, where a `champion` alias marks the version in service. Batch inference runs as Kubernetes jobs scheduled by Airflow and writes predictions back for the dashboards. The models cover outbreak and early-warning signals, seasonal and stock-out forecasting, and consumption forecasting for the supply chain control tower. Example workshop submissions in `bundle/seed-pack/app/project-review/` exercise the review path.

### Model lifecycle

Model packages live in `bundle/seed-pack/app/ml/<name>/`. `elmis_consumption_forecast` implements every step below. The other five packages (`ears`, `outbreak`, `seasonal`, `ewars_lite`, `stockout_forecast`) have a feature spec, manifest, model card and tests.

1. **Exploratory analysis.** `eda/` holds the queries behind the modelling choices: target population, exclusions, series classes, missingness and censoring.
2. **Target and features.** `feature_spec.yaml` lists every column and reads a governed mart. The target is defined once (for the supply forecast, `adjusted_demand` in `mart_pct_demand_month`). Every feature is available at the forecast's `as_of` date, checked by a leakage test.
3. **Evaluation.** A rolling-origin fold ledger with a final holdout, baselines and a promotion rule declared in advance. Metrics suit the data: no MAPE on series with zeros, no seasonal comparison without a prior season.
4. **Tracking.** Each training run logs parameters, metrics (per fold, holdout and segment), the feature query hash and artifacts in MLflow, and registers a version.
5. **Uncertainty.** Quantiles come from a stated method, and their coverage is checked on the holdout.
6. **Serving.** The Airflow batch DAG writes predictions to `ops_ml`, with contract keys in `attrs`; dashboards read them through a governed mart.
7. **Monitoring.** An `actuals` block in the manifest lets the scorer compare predictions with outcomes; drift checks cover the inputs.
8. **Promotion.** The gateway's promote route moves the MLflow alias and writes an audit event, only when the promotion rule passes.
9. **Delivery.** Package changes reach the cluster through `seed-ml.sh` (`--retrain` when a champion exists).
10. **Model card.** Purpose, data, target, exclusions, method, evaluation, limits and owner.

[pct-forecast-contract.md](pct-forecast-contract.md) applies this to supply chain demand forecasting.
