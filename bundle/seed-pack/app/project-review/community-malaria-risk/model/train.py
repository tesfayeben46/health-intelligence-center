"""Outbreak: Farrington-style aberration detector (NHDW ML package).

Per (woreda x disease) weekly series:

    baseline (mean, raw_sd) = same ISO-week ± `week_window` (default 2) in the
    prior years of that series.
    over-dispersion spread = max(raw_sd, sqrt(mean))   (Farrington variance floor)
    threshold  = mean + z · spread                     (z = 2, param)
    outbreak_z = (observed − mean) / spread
    alarm_prob = clip( (observed − mean) / (2·z·spread), 0, 1 )   (ears scaling)

The channel width `spread` is reported as `pred_cases_sd`; `endemic_sd` carries
the raw sample sd. No winsorizing and no percentile thresholds: a Gaussian
z-channel with a Poisson over-dispersion floor. One row per (woreda x disease x
ISO-week).

Short history: two explicit branches
When a series has < `min_history_years` (default 2) ISO-years, same-ISO-week-in-
prior-years has no data, so the model falls back to a rolling preceding-8-week
window (the `mart_idsr_outbreaks` convention). The multi-year branch activates
per series once enough ISO-years of history exist.

Package contract (bundle/seed-pack/app/ml/_template): the package never emits alarm_flag
(platform derives alarm_prob >= 0.5) and never serializes attrs (extra columns
are folded into attrs jsonb by the runner). Written from the published WHO EWARS
outbreak-module description; no code is copied.
"""
from __future__ import annotations

import mlflow.pyfunc
import numpy as np
import pandas as pd

MODEL_NAME = "outbreak"
MODEL_VERSION = "v1"

FLAT_COLUMNS = [
    "disease", "district", "year", "week",
    "pred_cases_mean", "pred_cases_sd", "pred_cases_lwr95",
    "pred_cases_median", "pred_cases_upr95",
    "endemic_mean", "endemic_sd", "outbreak_z",
    "alarm_threshold", "alarm_prob", "n_draws",
]


def _week_window(target_week: int, window: int) -> set[int]:
    """ISO weeks within ±window of target, wrapped into 1..52."""
    return {((target_week + off - 1) % 52) + 1 for off in range(-window, window + 1)}


class OutbreakModel(mlflow.pyfunc.PythonModel):
    def __init__(self, z: float = 2.0, week_window: int = 2,
                 min_history_years: int = 2, fallback_window: int = 8):
        self.z = float(z)
        self.week_window = int(week_window)
        self.min_history_years = int(min_history_years)
        self.fallback_window = int(fallback_window)
        self.name = MODEL_NAME
        self.version = MODEL_VERSION

    def predict(self, context, model_input, params=None):  # noqa: D401
        params = params or {}
        z = float(params.get("z", self.z))
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
                    # multi-year branch: same ISO-week ±window, prior years
                    kind = "same-week-multi-year"
                    win = list(_week_window(int(weeks[i]), self.week_window))
                    base = cases[np.isin(weeks, win) & (years < years[i])]
                else:
                    # fallback branch: rolling preceding-8-week window
                    kind = f"rolling-{self.fallback_window}w-fallback"
                    lo = max(0, i - self.fallback_window)
                    base = cases[lo:i]

                if base.size == 0:
                    mean = obs
                    raw_sd = float(np.sqrt(max(obs, 1.0)))
                else:
                    mean = float(np.mean(base))
                    raw_sd = float(np.std(base, ddof=1)) if base.size >= 2 else 0.0

                spread = max(raw_sd, float(np.sqrt(max(mean, 1e-9))))
                threshold = mean + z * spread
                z_score = (obs - mean) / spread if spread > 0 else 0.0
                alarm_prob = float(np.clip((obs - mean) / (2.0 * z * spread), 0.0, 1.0)) if spread > 0 else 0.0
                rows.append({
                    "disease": disease, "district": district,
                    "year": int(years[i]), "week": int(weeks[i]),
                    "pred_cases_mean": mean, "pred_cases_sd": spread,
                    "pred_cases_lwr95": max(0.0, mean - 1.96 * spread),
                    "pred_cases_median": mean,
                    "pred_cases_upr95": mean + 1.96 * spread,
                    "endemic_mean": mean, "endemic_sd": raw_sd,
                    "outbreak_z": z_score, "alarm_threshold": threshold,
                    "alarm_prob": alarm_prob, "n_draws": np.nan,
                    "observed_cases": obs, "baseline_kind": kind,
                    "spread": spread, "n_baseline": int(base.size), "z": z,
                })

        return pd.DataFrame(rows)


def train(df: pd.DataFrame, params: dict) -> mlflow.pyfunc.PythonModel:
    """Return a fitted Outbreak pyfunc model (parameter-free; freezes knobs)."""
    params = params or {}
    model = OutbreakModel(
        z=params.get("z", 2.0),
        week_window=params.get("week_window", 2),
        min_history_years=params.get("min_history_years", 2),
        fallback_window=params.get("fallback_window", 8),
    )
    if model.predict(None, df).empty:
        raise ValueError("training frame produced zero forecast rows; check feature_spec.yaml scoping")
    return model
