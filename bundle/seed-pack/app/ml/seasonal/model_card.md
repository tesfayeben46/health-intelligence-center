# Seasonal: seasonal-profile expectation channel

## What it predicts
For every woreda, disease and ISO week it estimates the expected seasonal level (the shape of a normal year, scaled to the current year's overall activity) and flags weeks that rise materially above it.

## Inputs
`bi_idsr.mart_ml_disease_input`: weekly `cases` per `woreda_uid x disease_code`. Case counts only.

## Training data
Trained on the platform's demonstration data (see [`docs/data-provenance.md`](../../../../../docs/data-provenance.md)): generated weekly IDSR series (53 weeks ending 2026-W27) with a fixed set of injected outbreaks. Any metrics logged for this model describe its behaviour on that data; they are not evidence of performance on real surveillance data.

## Method
- **Profile**: `profile[W]` is the mean of prior years' cases at ISO week `W`.
- **Level ratio**: `clip(mean(current-year cases) / mean(other-years cases), 0.3, 3)` rescales the profile to this year's activity.
- **Expected**: `mu[W] = profile[W] · level_ratio`.
- **Spread**: sd of prior years' cases at week `W`, floored at `sqrt(mu)`.
- **Threshold**: `mu + z · spread` (default `z = 2`; overridable at predict time).
- **outbreak_z**: `(observed − mu) / spread`; **alarm_prob**: `clip((observed − mu) / (2·z·spread), 0, 1)`. The platform derives `alarm_flag = alarm_prob ≥ 0.5`; the package does not emit it.
- Extras (`observed_cases`, `baseline_kind`, `profile`, `level_ratio`, `z`) are folded into the `attrs` jsonb column by the runner.

## Short-history fallback
With one ISO year there are no prior years to average, so the fallback branch builds a single-year, leave-one-out profile:

- `profile[i]` is a centred rolling mean (window 5) of the year's own weekly cases: the seasonal shape.
- `level_ratio` is computed leave-one-out (excluding week `i`), so the expectation never trivially equals the observed week.
- The residual sd of `cases − profile` is the spread (Poisson-floored).

The branch is recorded per row in `attrs.baseline_kind` (`same-week-multi-year` or `single-year-loo-fallback`). The multi-year branch activates per series once it has `min_history_years` ISO years.

## Limitations
- Captures seasonality and level, not climate-driven lead time (`ewars_lite` provides that).
- The single-year profile is a smoother of the same year: it detects sharp local spikes but cannot tell an anomalous season from a normal one until several years of history exist.
