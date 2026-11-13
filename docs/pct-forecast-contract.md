# Supply planning forecast contract

PCT is the supply planning layer behind the Supply Control Tower: the `mart_pct_*` marts, the consumption forecast and the plan positions built from the Zambian eLMIS history.

This contract defines what a forecast must satisfy before it drives a position, a recommendation or a promotion. It applies to `elmis_consumption_forecast` and any challenger model. The machine-readable form is `bundle/seed-pack/app/ml/elmis_consumption_forecast/forecast_contract.yaml`; the two are changed together.

## 1. Target

- The target is `adjusted_demand` from `bi_supply.mart_pct_demand_month`: eLMIS stock-out adjusted consumption (`normalized_consumption`), or dispensed quantity where that is NULL.
- A series is one facility and tracer product (programmes summed), monthly.
- Stock-out censored months (`demand_censored`) are kept in training as lower bounds and excluded from scoring.
- Missing months are gaps. Nothing is imputed as zero.

## 2. Scope

Census query: `bundle/seed-pack/app/ml/elmis_consumption_forecast/eda/census.sql`. Counts were measured on the mutated eLMIS extract; the fallback generator gives different counts.

| Group | Series | Share of demand volume |
|---|---|---|
| Eligible, smooth | 2,189 | 58.7% |
| Eligible, erratic | 1,280 | 28.5% |
| Eligible, intermittent | 4,101 | 3.1% |
| Eligible, lumpy | 1,848 | 6.0% |
| Excluded, fewer than 6 training months | 12,162 | 3.7% |
| Excluded, no non-zero demand | 7,622 | 0.04% |

A series is eligible with at least 6 observed months before the holdout month and at least one non-zero demand. Demand classes follow Syntetos-Boylan (ADI 1.32, CV squared 0.49). Excluded series are listed and keep eLMIS AMC as their planning basis.

## 3. Time

- `as_of_month` is the last month the forecast may use; `target_month` is the month predicted; `horizon` is the number of months between them.
- Only horizon 1 is produced. Horizons 2 and 3 are shown as not produced.
- Training cutoff for the final evaluation: `as_of` 2018-07.
- Reference date: `as_of` 2018-08, target 2018-09. `now()` is never used.

## 4. Uncertainty

- P10, P50 and P90 come from the empirical distribution of horizon 1 errors in the fold ledger (section 6), per demand class, added to the point forecast and clipped at zero (a split-conformal interval). Nominal P10 to P90 coverage is 80%.

## 5. Storage

- Predictions use the shared table `ops_ml.ml_predictions`. Contract fields are stored in `attrs`: `facility_code`, `product_code`, `as_of_month`, `target_month`, `horizon`, `p10`, `p50`, `p90`, `demand_class`, `method`.
- `bi_supply.mart_pct_forecast` expands them into columns keyed by run, facility, product, `as_of`, target and horizon, using the latest run only.

## 6. Evaluation

- Expanding-window folds at horizon 1: `as_of` 2018-02 to 2018-06 (five tuning folds, targets 2018-03 to 2018-07). Final holdout: `as_of` 2018-07, target 2018-08, evaluated once.
- Baselines: naive last value, trailing 3-month mean (the AMC equivalent), damped Holt, and Croston, SBA and TSB for intermittent and lumpy series. There is no seasonal naive baseline because the history has no prior year.
- Promotion rule: the candidate is promoted only if, on the tuning folds and the holdout, it beats both naive last value and trailing 3-month mean on WAPE at horizon 1, with absolute bias at most 10% and P10 to P90 coverage between 70% and 90%. Otherwise AMC remains the planning basis and the surface shows that.

## 7. Metrics

Logged in the MLflow run per fold and for the holdout: WAPE (primary) and MAE; MASE and RMSSE scaled on training data only; signed and cumulative bias; P10 and P90 pinball loss; P10 to P90 coverage and mean width; counts of series, observations, demand volume, excluded and censored series. Each metric is broken down by demand class, programme, province and facility type. Operational metrics: stock-out risk recall, false alert rate and the planning difference against AMC. MAPE is not used for selection because many series have zero demand.

## 8. Decision policy

- P50 is expected demand. P90 applies to critical tracers (HIV antiretrovirals, HIV diagnostics, malaria diagnostics).
- Order sizes use P50 as the monthly rate in the eLMIS max/min policy. Lumpy series stay on eLMIS AMC; on the holdout P50 under-covered their volume by 45%.
- P90 is used as the one-month stock-out risk test for critical tracers (stock below P90 means at risk before the next delivery). It is not used as an order rate; doing so would have raised August 2018 replenishment from 2.78M to 7.07M units.
- When the promotion rule fails, or a series is excluded, the position uses eLMIS AMC and is labelled as such.
- Each approved plan line records the forecast run, quantile policy, inputs and rationale, so rebuilding a mart does not change an approved plan.

## 9. Delivery

The path from package to screen: model package, runner, MLflow run with metrics, registered version and alias, Airflow batch inference, `ops_ml`, `mart_pct_forecast` refresh, Supply Control Tower widgets.

Current limits: model packages are delivered by `seed-ml.sh` (`--retrain` when a champion exists), not by CI; batch inference runs on a schedule rather than on data arrival; marts that consume forecasts are not refreshed automatically after predictions land; promotion writes an MLflow alias and a gateway audit event.

## Reporting results

Results are rolling-origin backtest performance on historical data. A method is used only where it beats the declared baselines within the bias and calibration limits.
