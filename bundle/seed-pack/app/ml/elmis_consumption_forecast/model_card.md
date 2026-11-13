# elmis_consumption_forecast (v2, forecast contract)

## What it predicts
A one-month-ahead demand forecast per facility and tracer product for supply planning (the PCT marts behind the Supply Control Tower) on the Zambian eLMIS data, with P10/P50/P90 quantiles. The model is governed by [`docs/pct-forecast-contract.md`](../../../../../docs/pct-forecast-contract.md).

## Training data
Trained on the platform's demonstration data (see [`docs/data-provenance.md`](../../../../../docs/data-provenance.md)): the eLMIS 2017 to 2018 history, which is either a pattern-preserving mutation of a historical eLMIS extract (distributed separately) or, without that artifact, the committed fallback generator. Metrics describe behaviour on that data; they are not evidence of forecast accuracy on real eLMIS data.

`bi_supply.mart_pct_demand_month`, tracer products, programmes summed per facility, product and month. Target: `adjusted_demand` (eLMIS normalised consumption; dispensed where NULL). Missing months are gaps, never zeros. Stockout-censored months train as observed floors and are never scored.

## Population
Eligible series have at least 6 observed months up to the forecast origin and some non-zero demand (census: `eda/census.sql`). Excluded series keep eLMIS AMC as their planning basis.

## Method
Six transparent comparators (naive-last, trailing 3-month mean, damped Holt, Croston, SBA, TSB) are evaluated on an expanding-window fold ledger: horizon 1, 5 tuning origins, and a final holdout from as-of 2018-07 to 2018-08 that is evaluated once. Per Syntetos-Boylan demand class, the comparator with the lowest tuning WAPE is selected. P10/P50/P90 are the point forecast plus empirical quantiles of scaled tuning errors per class (scale: the series' mean training demand), clipped at 0. Comparator parameters are fixed (`params.yaml`); the fold ledger, not per-series fitting, decides which method serves each class.

## Evaluation
Every training run logs per-fold and holdout metrics (WAPE, bias, P10-P90 coverage, MASE, RMSSE) and the predeclared champion rule (`champion_rule_passed`) inside its MLflow run; the run of the registered version is the authoritative record. The same backtest, one row per fold window (metrics named `backtest_*`), is written to `ops_ml.model_performance` under the registered version and read by `bi_supply.mart_pct_forecast_accuracy`.


## Limitations
- Twelve months of history: no seasonality can be learned.
- Only horizon 1 is produced.
- Demand in stockout months is a floor, not true demand.

## Serving
Batch, flat forecast schema, with the contract keys in `attrs` (`forecast_contract.yaml`); the Gaussian `lwr95`/`upr95` columns are left empty. `bi_supply.mart_pct_forecast` serves the latest batch run of a version that carries this backtest, with the version and MLflow run on every row.

## Promotion
A version is promoted to `@champion` through the gateway's promote route by an operator (R-OPS-ADM), which sets the MLflow alias and writes a gateway audit event. The route does not check the champion rule; the operator does, from the run's logged `champion_rule_passed`. Rollback is promoting the previous version again. Because `bi_supply.mart_pct_forecast` serves only versions that carry the contract backtest, a dashboard must compare the mart's `model_version` with the live champion and fall back to eLMIS AMC, labelled as such, when they differ.

## Owner
Supply-chain analytics.
