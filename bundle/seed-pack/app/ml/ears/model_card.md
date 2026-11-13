# EARS: endemic-channel aberration detector

## What it predicts
For every woreda, disease and ISO week it estimates the endemic level (the number of cases normally expected that week) and flags weeks whose observed count sits abnormally far above it, a possible outbreak signal.

## Inputs
`bi_idsr.mart_ml_disease_input`: weekly `cases` per `woreda_uid x disease_code` (zero-filled densified grid), plus region climate and population. EARS uses only the case counts.

## Training data
Trained on the platform's demonstration data (see [`docs/data-provenance.md`](../../../../../docs/data-provenance.md)): generated weekly IDSR series (53 weeks ending 2026-W27) with a fixed set of injected outbreaks. Any metrics logged for this model describe its behaviour on that data, including how well it recovers the injected signals; they are not evidence of performance on real surveillance data.

## Method
- **Endemic baseline**: mean and standard deviation of the same ISO week in the other years of the series.
- **Threshold**: `endemic_mean + k · endemic_sd` (default `k = 3`; overridable at predict time through the deployment params).
- **outbreak_z**: `(observed − endemic_mean) / endemic_sd`.
- **alarm_prob**: `clip((observed − endemic_mean) / (2·k·endemic_sd), 0, 1)`. The platform derives `alarm_flag = alarm_prob ≥ 0.5`; the package does not emit it.
- The endemic level is also the forecast: `pred_cases_mean = endemic_mean`, 95% band `endemic_mean ± 1.96·endemic_sd`.
- One row per woreda, disease and ISO week; extra context (`observed_cases`, `baseline_kind`, `n_baseline`, `k`) is folded into the `attrs` jsonb column by the runner.

## Short-history fallback
The data holds about one ISO year, so "same week in other years" has no support. Each series runs one of two branches, chosen by how many ISO years it has:

- **Multi-year** (`≥ min_history_years`, default 2): the same-week baseline above. It activates automatically once a series has enough history.
- **Fallback** (`< min_history_years`): a rolling window of the 8 weeks immediately before the target week (the same convention as `mart_idsr_outbreaks`).

The active branch is recorded in `attrs.baseline_kind` (`same-week-multi-year` or `rolling-8w-fallback`). When even the fallback window is empty (the first weeks of a series), the baseline is the observed value with a Poisson sd `sqrt(max(obs, 1))` and `alarm_prob = 0`.

## Limitations
- A detector, not a climate-driven forecaster: it cannot anticipate outbreaks before cases rise. `ewars_lite` provides climate-lead early warning.
- With rolling 8-week baselines, a sustained rise inflates its own baseline and can be under-flagged; multi-year baselines are stronger.
- When the baseline sample has fewer than two values or zero variance, `endemic_sd` is replaced by `sqrt(max(mean, 1))` to avoid divide-by-zero alarms.
