# Model card: <model name>

<!-- model_card.md: the plain-language description of the model. Required.

Audience: a district health officer or a surveillance analyst deciding whether
to trust the number on a widget, not an ML engineer. The gateway serves this
file with the model metadata, and the workshop catalogue renders it.

Keep every section below. Delete the comments, keep the headings.
-->

## What it predicts

<!-- One or two sentences, no jargon. Example: "For every woreda and
priority disease, the expected number of weekly cases and the probability
that the current week is the start of an outbreak." -->

Template placeholder: expected weekly cases per woreda and disease, plus a
0-1 outbreak-alarm probability for the latest reported week.

## Inputs

<!-- Where the data comes from (the mart), the columns used, and the
population the model covers. -->

- `bi_idsr.mart_ml_disease_input`: the densified weekly IDSR grid (all
  woredas x priority diseases x all weeks; missing weeks are zeros, not gaps).
- Columns used: `cases` per ISO week. Climate columns (`rainfall_mm`,
  `temp_celsius`, `humidity_pct`) and `population` are in the same mart;
  this template ignores them. Climate-driven models state here which lags
  they use.
- `population` can be NULL; this model does not depend on it. Models that
  use a population offset must document what happens when it is NULL.

## Training data

<!-- Which data the model was trained and evaluated on, and what the logged
metrics therefore mean. In this repository every package trains on the
demonstration data described in docs/data-provenance.md. -->

Trained on the platform's demonstration data (see `docs/data-provenance.md`).
Metrics logged for the model describe its behaviour on that data, not on real
surveillance data.

## Method

<!-- A short paragraph, method-level, no equations required. Name the
established technique it is based on. -->

Endemic channel: the baseline for a week is the mean and sd of the same ISO
week in previous years; the alarm threshold is `mean + k·sd` (k = 3 by
default) and the alarm probability scales the exceedance into 0-1.

## Limitations

<!-- The section reviewers read first. Include the history caveat where it
applies. -->

- **Short-history fallback**: the IDSR data holds about one year of weekly
  history, so the same-week-other-years baseline has no support; the model
  then falls back to a rolling window of the preceding 8 weeks. The
  multi-year branch activates automatically when more history exists. Until
  then, alarms reflect deviation from recent weeks, not seasonality.
- Series with fewer than 3 baseline weeks are skipped (no forecast row).
- No spatial smoothing: each woreda is scored independently, and sparse
  woredas produce noisy baselines.
- Weekly grain only; reporting delays shift signals by up to one week.

## Output and how to read it

The unified flat forecast schema (`ops_ml.ml_predictions`).
`alarm_prob` risk bands used across all dashboards:
low < 0.2 ≤ watch < 0.5 ≤ elevated < 0.8 ≤ high.
`alarm_flag` is set by the platform at `alarm_prob ≥ 0.5`.

## Provenance and maintenance

<!-- Who built it, on what method or source, who maintains it. -->

- Provenance: `community` (template).
- Method reference: WHO EWARS endemic-channel practice, adapted.
- Maintainer: <team / contact>.
