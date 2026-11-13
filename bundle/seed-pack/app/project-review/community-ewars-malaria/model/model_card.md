# Community EWARS malaria early-warning (climate NB2 GLM)

> **Sample community model.** Submitted through the workshop as the project *Community EWARS Malaria Early-Warning*; `bundle/scripts/seed-graduation.sh` leaves it in review (`in_review`). Registry name on approval: `community_ewars_malaria`. A climate-driven NB2 GLM adapted from the platform EWARS-lite model, on the same `bi_idsr.mart_ml_disease_input` mart.

## What it predicts
For each woreda, disease and ISO week it forecasts the expected weekly case count from lagged climate and seasonality, and estimates the probability that cases exceed the endemic alarm threshold. It is the climate-lead model of the family: unlike the detectors (EARS, Outbreak, Seasonal) it can anticipate a rise from rainfall, temperature and humidity signals weeks ahead.

It is a lightweight, offline stand-in for WHO's EWARS-CSD (an R/INLA Bayesian model needing 48-64 GB of RAM), reproducing the climate early-warning behaviour with a negative-binomial GLM and posterior sampling.

## Inputs
`bi_idsr.mart_ml_disease_input`: weekly `cases`, region `rainfall_mm`, `temp_celsius` and `humidity_pct` (broadcast to woreda), and `population` per `woreda_uid x disease_code`.

## Training data
Trained on the platform's demonstration data (see [`docs/data-provenance.md`](../../../../../../docs/data-provenance.md)): generated weekly IDSR series (53 weeks ending 2026-W27) with injected outbreaks and a lagged coupling to a generated rainfall series, and deterministic (not census) woreda populations. The climate signal the model finds is the one the generator placed in the data. Any metrics logged for this model describe its behaviour on that data; they are not evidence of performance on real surveillance or climate data.

## Method
- **Family**: negative binomial (NB2), variance `mu + alpha·mu²`. `alpha` is estimated by the Cameron-Trivedi auxiliary OLS on a first-pass Poisson fit, then the model is refitted as `NB(alpha)`.
- **Covariates**: rainfall, temperature and humidity lags 1..`nlags` (default 8, built within each woreda series); seasonal sin/cos harmonics of ISO week (`nharmonics` pairs, default 2); region fixed effects.
- **Offset**: `log(population)` only when population is present and positive for the whole training frame; otherwise no offset and the model forecasts raw counts. The path taken is recorded per row in `attrs.use_offset`.
- **Pooling**: one GLM per disease, pooling all woredas with region fixed effects, for more data per fit and a cross-woreda climate signal.
- **Bands**: `n_draws` (default 500) draws from `NB2(mu, alpha)` give `pred_cases_mean/sd/median/lwr95/upr95`.
- **Alarm**: `alarm_threshold` comes from the EARS endemic channel (`endemic_mean + k·endemic_sd`; `k` overridable at predict time); `alarm_prob = P(draw > alarm_threshold)`. The platform derives `alarm_flag = alarm_prob ≥ 0.5`; the package does not emit it. `n_draws` is emitted.
- Extras (`observed_cases`, `mu_glm`, `alpha`, `use_offset`, `glm_kind`, `endemic_kind`, `nlags`, `nharmonics`, `k`) are folded into the `attrs` jsonb by the runner.

## Short-history fallback
- The GLM needs no multi-year history: harmonics and lagged climate carry the seasonal signal from one year of weekly data.
- The endemic channel used for the alarm threshold has the same two branches as EARS: same ISO week in other years when at least `min_history_years` ISO years exist, else a rolling window of the 8 preceding weeks. The active branch is in `attrs.endemic_kind`.
- Where the GLM cannot be fitted (too few rows, singular design, perfect separation) that disease falls back to its endemic mean, recorded in `attrs.glm_kind` (`nb_glm` or `mean`).

## Limitations
- Not the full Bayesian spatio-temporal EWARS-CSD: no INLA cyclic RW1 time or IID space random effects and no DLNM distributed-lag cross-basis; plain climate lags stand in for the DLNM.
- Where population is missing or non-positive anywhere in the training frame, the model runs without the offset, so `pred_cases_*` are raw counts rather than incidence rates.
- Region climate is broadcast to every woreda in the region, so within-region climate variation is not resolved.
