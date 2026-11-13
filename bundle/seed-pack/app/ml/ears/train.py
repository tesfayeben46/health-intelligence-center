"""EARS: endemic-channel aberration detector (NHDW ML package).

WHO/EWARS "EARS" endemic channel (not the CDC C1/C2/C3 day-window variant):

    baseline (endemic_mean, endemic_sd) = mean + sd of the same ISO-week in
    the other years of history for this (woreda x disease) series.

    threshold  = endemic_mean + k · endemic_sd          (k = 3, param)
    outbreak_z = (observed − endemic_mean) / endemic_sd
    alarm_prob = clip( (observed − endemic_mean) / (2·k·endemic_sd), 0, 1 )

The predicted endemic level is the forecast: pred_cases_mean = endemic_mean,
with a 95 % band of endemic_mean ± 1.96·endemic_sd. One row per (woreda x
disease x ISO-week) so the surveillance widgets can draw the endemic channel
across the season (the reference ml_predictions is a per-week audit log).

Short history: two explicit branches
A (woreda x disease) series may have < 2 ISO-years of history (the demonstration
seed holds about one year). Same-week-other-years then has no data, so the model
falls back to a rolling preceding-8-week window (the `mart_idsr_outbreaks`
convention). The multi-year branch activates per series once
>= `min_history_years` ISO-years of history exist.

Package contract (bundle/seed-pack/app/ml/_template): train(df, params) returns a fitted
mlflow.pyfunc.PythonModel; predict(context, model_input, params) emits the flat
forecast schema. The package never emits alarm_flag (the platform derives it,
alarm_prob >= 0.5) and never serializes attrs (extra columns are folded into the
attrs jsonb by the runner). Written from the published method description; no
third-party code is copied.
"""
from __future__ import annotations

import mlflow.pyfunc
import numpy as np
import pandas as pd

MODEL_NAME = "ears"
MODEL_VERSION = "v1"

# Canonical flat-schema columns (no alarm_flag, no attrs; see module docstring).
# Any additional columns the model returns are folded into attrs by the runner.
FLAT_COLUMNS = [
    "disease", "district", "year", "week",
    "pred_cases_mean", "pred_cases_sd", "pred_cases_lwr95",
    "pred_cases_median", "pred_cases_upr95",
    "endemic_mean", "endemic_sd", "outbreak_z",
    "alarm_threshold", "alarm_prob", "n_draws",
]


def _safe_sd(values: np.ndarray, mean: float) -> float:
    """Endemic sd with a Poisson floor when the sample is degenerate."""
    values = np.asarray(values, dtype=float)
    sd = float(np.std(values, ddof=1)) if values.size >= 2 else 0.0
    if not np.isfinite(sd) or sd <= 0.0:
        sd = float(np.sqrt(max(mean, 1.0)))
    return sd


class EarsModel(mlflow.pyfunc.PythonModel):
    """Endemic-channel detector. Stateless: baselines are derived from the
    full-history feature frame at predict() time (the batch query returns all
    weeks). Params (k, min_history_years, fallback_window) are frozen at
    train(); `k` may be overridden at predict time."""

    def __init__(self, k: float = 3.0, min_history_years: int = 2,
                 fallback_window: int = 8):
        self.k = float(k)
        self.min_history_years = int(min_history_years)
        self.fallback_window = int(fallback_window)
        self.name = MODEL_NAME
        self.version = MODEL_VERSION

    def predict(self, context, model_input, params=None):  # noqa: D401
        params = params or {}
        k = float(params.get("k", self.k))
        df = model_input.copy()
        df["year"] = df["year"].astype(int)
        df["week"] = df["week"].astype(int)
        df["cases"] = pd.to_numeric(df["cases"], errors="coerce").fillna(0.0).astype(float)
        df = df.sort_values(["woreda_uid", "disease_code", "year", "week"])

        rows = []
        for (district, disease), g in df.groupby(["woreda_uid", "disease_code"], sort=False):
            g = g.reset_index(drop=True)
            n_years = int(g["year"].nunique())
            multi_year = n_years >= self.min_history_years
            cases = g["cases"].to_numpy(dtype=float)
            weeks = g["week"].to_numpy(dtype=int)
            years = g["year"].to_numpy(dtype=int)

            for i in range(len(g)):
                obs = float(cases[i])
                if multi_year:
                    # multi-year branch: same ISO-week, other years
                    kind = "same-week-multi-year"
                    base = cases[(weeks == weeks[i]) & (years != years[i])]
                else:
                    # fallback branch: rolling preceding-8-week window
                    kind = f"rolling-{self.fallback_window}w-fallback"
                    lo = max(0, i - self.fallback_window)
                    base = cases[lo:i]

                if base.size == 0:
                    mean = obs
                    sd = float(np.sqrt(max(obs, 1.0)))
                else:
                    mean = float(np.mean(base))
                    sd = _safe_sd(base, mean)

                threshold = mean + k * sd
                z = (obs - mean) / sd if sd > 0 else 0.0
                alarm_prob = float(np.clip((obs - mean) / (2.0 * k * sd), 0.0, 1.0)) if sd > 0 else 0.0
                rows.append({
                    "disease": disease, "district": district,
                    "year": int(years[i]), "week": int(weeks[i]),
                    "pred_cases_mean": mean, "pred_cases_sd": sd,
                    "pred_cases_lwr95": max(0.0, mean - 1.96 * sd),
                    "pred_cases_median": mean,
                    "pred_cases_upr95": mean + 1.96 * sd,
                    "endemic_mean": mean, "endemic_sd": sd,
                    "outbreak_z": z, "alarm_threshold": threshold,
                    "alarm_prob": alarm_prob, "n_draws": np.nan,
                    # extras -> folded into attrs jsonb by the runner
                    "observed_cases": obs, "baseline_kind": kind,
                    "n_baseline": int(base.size), "k": k,
                })

        return pd.DataFrame(rows)


def train(df: pd.DataFrame, params: dict) -> mlflow.pyfunc.PythonModel:
    """Return a fitted EARS pyfunc model. EARS is parameter-free; 'training'
    freezes hyperparameters. The runner logs + registers the returned instance
    and calls predict(context, feature_df) on every batch."""
    params = params or {}
    model = EarsModel(
        k=params.get("k", 3.0),
        min_history_years=params.get("min_history_years", 2),
        fallback_window=params.get("fallback_window", 8),
    )
    if model.predict(None, df).empty:
        raise ValueError("training frame produced zero forecast rows; check feature_spec.yaml scoping")
    return model
