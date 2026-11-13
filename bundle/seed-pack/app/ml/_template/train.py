"""train.py: the single code entry point of an NHDW model package.

The contract (enforced by the ml-runner, 2_pipelines/ml/runner/):

    def train(df: pd.DataFrame, params: dict) -> mlflow.pyfunc.PythonModel

* `df`      is the frame your feature_spec.yaml query returned, already
            validated + dtype-coerced against input.signature.
* `params`  is params.yaml as a dict (defaults; the platform may override).
* return    an instance of an mlflow.pyfunc.PythonModel subclass, fully
            fitted. train.py stays pure (no MLflow calls, no DB, no files);
            the runner handles logging, signature inference, registration,
            aliasing and the drift reference profile.

Your PythonModel's predict() runs on every scheduled batch:

    def predict(self, context, model_input, params=None) -> pd.DataFrame

* `model_input` is a fresh frame from the same feature query.
* `params`      arrives when a deployment row or operator passes overrides
                (only keys that exist in params.yaml as scalars survive;
                they form the model's MLflow params schema). Accepting the
                argument is optional but recommended.
* return        for batch models (deployment_kind: batch): the unified flat
                forecast schema, one row per (disease, district, year, week):

    district          REQUIRED  geography id (woreda_uid for disease models;
                                commodity name for supply models)
    year, week        REQUIRED  ISO year/week of the target period (int)
    disease           optional  disease_code; omit/NULL for non-disease models
    pred_cases_mean/_sd/_lwr95/_median/_upr95   forecast point + interval
    endemic_mean/_sd  historical baseline ("expected" line on widgets)
    outbreak_z        standardised deviation of observed vs baseline
    alarm_threshold   the alarm trigger level on the case scale
    alarm_prob        0..1 outbreak probability (risk bands: low<0.2,
                      watch<0.5, elevated<0.8, high≤1.0)
    n_draws           optional  sampling draws behind the interval
    <anything else>   folded into the attrs jsonb column automatically

  Do not emit alarm_flag: the platform derives it (alarm_prob >= 0.5) so the
  rule is uniform across every model.

Serialization: the runner pickles the model classes by value (cloudpickle), so
keep train.py self-contained. Helpers imported from sibling files are fine at
train time, but everything the PythonModel instance references at predict time
must live in this file (or in the baked/requirements libs).

The reference implementation below is a working endemic-channel forecaster,
kept small. Replace the statistics, keep the shape.
"""
from __future__ import annotations

import mlflow.pyfunc
import numpy as np
import pandas as pd


class TemplateForecaster(mlflow.pyfunc.PythonModel):
    """Same-ISO-week endemic channel with the documented short-history fallback.

    Short-history fallback (required for every model whose baseline is
    "same week in other years"): the seeded warehouse holds about one year
    (~53 weeks) of IDSR history, so a same-week-other-years baseline has no
    support. When fewer than `min_history_years` distinct prior years exist
    for a series, fall back to a rolling window of the `fallback_window`
    preceding weeks (the mart_idsr_outbreaks convention). The multi-year
    branch must still exist; it activates automatically when longer history
    is present. Document the fallback in model_card.md.
    """

    def __init__(self, k: float = 3.0, fallback_window: int = 8,
                 min_history_years: int = 2):
        self.k = float(k)
        self.fallback_window = int(fallback_window)
        self.min_history_years = int(min_history_years)

    # shared baseline logic (train-time fit == predict-time refresh)
    def _score_latest_week(self, g: pd.DataFrame, k: float) -> dict | None:
        g = g.sort_values(["year", "week"])
        latest = g.iloc[-1]
        observed = float(latest["cases"])
        history = g.iloc[:-1]

        prior_years = history.loc[history["year"] < latest["year"], "year"].nunique()
        if prior_years >= self.min_history_years:
            # Multi-year branch: same ISO week in other years.
            base = history.loc[history["week"] == latest["week"], "cases"]
        else:
            # Short-history fallback: preceding N weeks.
            base = history["cases"].tail(self.fallback_window)
        if len(base) < 3:                       # not even a fallback baseline
            return None

        mean = float(base.mean())
        sd = float(base.std(ddof=0))
        spread = max(sd, np.sqrt(max(mean, 1e-9)))   # over-dispersion floor
        threshold = mean + k * spread
        z = (observed - mean) / spread if spread > 0 else 0.0
        prob = float(np.clip((observed - mean) / (2.0 * k * spread), 0.0, 1.0))

        return {
            "disease": latest["disease_code"],
            "district": latest["woreda_uid"],
            "year": int(latest["year"]),
            "week": int(latest["week"]),
            "pred_cases_mean": mean,
            "pred_cases_sd": spread,
            "pred_cases_lwr95": max(mean - 1.96 * spread, 0.0),
            "pred_cases_median": mean,
            "pred_cases_upr95": mean + 1.96 * spread,
            "endemic_mean": mean,
            "endemic_sd": sd,
            "outbreak_z": z,
            "alarm_threshold": threshold,
            "alarm_prob": prob,
            # Extra context -> automatically folded into the attrs jsonb.
            "observed_cases": observed,
            "baseline_kind": "same-week-multi-year" if prior_years >= self.min_history_years
                             else f"rolling-{self.fallback_window}w-fallback",
        }

    # the pyfunc entry point
    def predict(self, context, model_input: pd.DataFrame,
                params: dict | None = None) -> pd.DataFrame:
        params = params or {}
        # Runtime overrides: only scalar keys declared in params.yaml arrive.
        # Keep them local; never mutate fitted state inside predict().
        k = float(params.get("k", self.k))
        rows = []
        for _, g in model_input.groupby(["woreda_uid", "disease_code"], sort=False):
            row = self._score_latest_week(g, k)
            if row is not None:
                rows.append(row)
        return pd.DataFrame(rows)


def train(df: pd.DataFrame, params: dict) -> mlflow.pyfunc.PythonModel:
    """Fit and return the model instance.

    This template's "fit" is trivial (the endemic channel re-derives its
    baseline from the fresh frame at predict time), but the shape is the
    same for heavyweight models: fit estimators here, store the fitted
    state on the instance, return the instance. Deterministic models should
    seed their RNGs from params (no fabricated randomness).
    """
    model = TemplateForecaster(
        k=float(params.get("k", 3.0)),
        fallback_window=int(params.get("fallback_window", 8)),
        min_history_years=int(params.get("min_history_years", 2)),
    )
    # Fail fast at train time if the frame can't produce a single row;
    # the runner treats an exception here as a failed ml_run.
    sample = model.predict(None, df)
    if sample.empty:
        raise ValueError("training frame produced zero forecast rows, "
                         "check feature_spec.yaml scoping")
    return model
