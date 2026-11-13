# Outbreak: Farrington-style aberration detector

## What it predicts
For every woreda, disease and ISO week it estimates the expected endemic count and flags weeks whose observed count exceeds a Farrington-style threshold. A week-window baseline pool and a Poisson over-dispersion floor make it more robust to seasonal noise than plain EARS.

## Inputs
`bi_idsr.mart_ml_disease_input`: weekly `cases` per `woreda_uid x disease_code` (zero-filled densified grid). Case counts only; climate is not used.

## Training data
Trained on the platform's demonstration data (see [`docs/data-provenance.md`](../../../../../docs/data-provenance.md)): generated weekly IDSR series (53 weeks ending 2026-W27) with a fixed set of injected outbreaks. Any metrics logged for this model describe its behaviour on that data, including how well it recovers the injected signals; they are not evidence of performance on real surveillance data.

## Method
- **Baseline pool**: counts from the same ISO week ±2 weeks in the prior years of the series, giving `mean` and sample `sd`.
- **Over-dispersion spread**: `max(sd, sqrt(mean))` (Farrington variance floor).
- **Threshold**: `mean + z · spread` (default `z = 2`; overridable at predict time).
- **outbreak_z**: `(observed − mean) / spread`.
- **alarm_prob**: `clip((observed − mean) / (2·z·spread), 0, 1)`. The platform derives `alarm_flag = alarm_prob ≥ 0.5`; the package does not emit it.
- `pred_cases_sd` carries `spread`, `endemic_sd` the raw sample sd; the spread is also in `attrs.spread`.
- No winsorising and no percentile thresholds: a Gaussian z-channel with a Poisson floor, matching the flat-schema convention.
- Extras (`observed_cases`, `baseline_kind`, `spread`, `n_baseline`, `z`) are folded into the `attrs` jsonb column by the runner.

## Short-history fallback
Two branches, selected per series by the number of ISO years:

- **Multi-year** (`≥ min_history_years`, default 2): same ISO week ±2 in prior years. It activates automatically once a series has enough history.
- **Fallback** (`< min_history_years`): a rolling window of the 8 preceding weeks (the `mart_idsr_outbreaks` convention).

The active branch is in `attrs.baseline_kind` (`same-week-multi-year` or `rolling-8w-fallback`). Empty baselines (the first weeks of a series) fall back to the observed value with a Poisson spread and `alarm_prob = 0`.

## Limitations
- A detector, not a climate-lead forecaster (`ewars_lite` provides that).
- With about one year of history the prior-years pool is empty, so the model runs on the rolling 8-week fallback; multi-year history makes the baseline substantially stronger.
