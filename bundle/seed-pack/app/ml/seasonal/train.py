"""Seasonal: seasonal-profile expectation and aberration channel (NHDW package).

Per (woreda x disease) weekly series, builds a seasonal profile and scales it by
the current-year level, then flags weeks above `mu + z·spread`.

    profile[W]  = mean of prior years' cases at ISO-week W
    level_ratio = clip( mean(current-year cases) / mean(other-years cases),
                        0.3, 3 )
    mu[W]       = profile[W] · level_ratio                (the expected level)
    spread[W]   = sd of prior years' cases at ISO-week W  (Poisson-floored)
    threshold   = mu + z · spread                          (z = 2, param)
    outbreak_z  = (observed − mu) / spread
    alarm_prob  = clip( (observed − mu) / (2·z·spread), 0, 1 )

One row per (woreda x disease x ISO-week).

Short history: two explicit branches
With < `min_history_years` (default 2) ISO-years there are no "prior years" to
average, so the model falls back to a single-year, leave-one-out seasonal profile: a
centred rolling mean of the one year's own weekly cases (the seasonal shape),
scaled by a leave-one-out level ratio so the expectation is never trivially equal
to the observed week. The multi-year branch activates per series once enough
ISO-years exist.

Package contract (bundle/seed-pack/app/ml/_template): never emits alarm_flag (platform
derives alarm_prob >= 0.5) and never serializes attrs (extras folded by runner).
Written from the published method description; no code is copied.
"""
from __future__ import annotations

import mlflow.pyfunc
import numpy as np
import pandas as pd

MODEL_NAME = "seasonal"
MODEL_VERSION = "v1"

FLAT_COLUMNS = [
    "disease", "district", "year", "week",
    "pred_cases_mean", "pred_cases_sd", "pred_cases_lwr95",
    "pred_cases_median", "pred_cases_upr95",
    "endemic_mean", "endemic_sd", "outbreak_z",
    "alarm_threshold", "alarm_prob", "n_draws",
]


def _floor_sd(sd: float, mu: float) -> float:
    if not np.isfinite(sd) or sd <= 0.0:
        return float(np.sqrt(max(mu, 1.0)))
    return float(sd)


class SeasonalModel(mlflow.pyfunc.PythonModel):
    def __init__(self, z: float = 2.0, min_history_years: int = 2,
                 ratio_lo: float = 0.3, ratio_hi: float = 3.0,
                 smooth_window: int = 5):
        self.z = float(z)
        self.min_history_years = int(min_history_years)
        self.ratio_lo = float(ratio_lo)
        self.ratio_hi = float(ratio_hi)
        self.smooth_window = int(smooth_window)
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
            if n_years >= self.min_history_years:
                rows.extend(self._multiyear(g, district, disease, z))
            else:
                rows.extend(self._single_year(g, district, disease, z))
        return pd.DataFrame(rows)

    # multi-year branch
    def _multiyear(self, g, district, disease, z):
        cases = g["cases"].to_numpy(dtype=float)
        weeks = g["week"].to_numpy(dtype=int)
        years = g["year"].to_numpy(dtype=int)
        out = []
        for i in range(len(g)):
            obs = float(cases[i])
            other = years != years[i]
            base = cases[(weeks == weeks[i]) & other]
            cur_mean = float(np.mean(cases[years == years[i]]))
            other_mean = float(np.mean(cases[other])) if other.any() else cur_mean
            ratio = float(np.clip(cur_mean / other_mean, self.ratio_lo, self.ratio_hi)) if other_mean > 0 else 1.0
            if base.size == 0:
                profile = obs
                sd = float(np.sqrt(max(obs, 1.0)))
            else:
                profile = float(np.mean(base))
                sd = float(np.std(base, ddof=1)) if base.size >= 2 else 0.0
            mu = profile * ratio
            out.append(self._row(district, disease, int(years[i]), int(weeks[i]),
                                  obs, mu, _floor_sd(sd, mu), z,
                                  "same-week-multi-year", profile, ratio))
        return out

    # fallback branch: single year, leave-one-out
    def _single_year(self, g, district, disease, z):
        cases = g["cases"].to_numpy(dtype=float)
        weeks = g["week"].to_numpy(dtype=int)
        years = g["year"].to_numpy(dtype=int)
        n = len(g)
        profile = pd.Series(cases).rolling(self.smooth_window, center=True, min_periods=1).mean().to_numpy()
        resid_sd = float(np.std(cases - profile, ddof=1)) if n >= 2 else 0.0
        total = float(cases.sum())
        prof_total = float(profile.sum())
        out = []
        for i in range(n):
            obs = float(cases[i])
            loo_den = prof_total - profile[i]
            ratio = float(np.clip((total - obs) / loo_den, self.ratio_lo, self.ratio_hi)) if loo_den > 0 else 1.0
            mu = profile[i] * ratio
            out.append(self._row(district, disease, int(years[i]), int(weeks[i]),
                                  obs, mu, _floor_sd(resid_sd, mu), z,
                                  "single-year-loo-fallback", float(profile[i]), ratio))
        return out

    def _row(self, district, disease, year, week, obs, mu, spread, z, kind, profile, ratio):
        z_score = (obs - mu) / spread if spread > 0 else 0.0
        alarm_prob = float(np.clip((obs - mu) / (2.0 * z * spread), 0.0, 1.0)) if spread > 0 else 0.0
        return {
            "disease": disease, "district": district, "year": year, "week": week,
            "pred_cases_mean": mu, "pred_cases_sd": spread,
            "pred_cases_lwr95": max(0.0, mu - 1.96 * spread),
            "pred_cases_median": mu, "pred_cases_upr95": mu + 1.96 * spread,
            "endemic_mean": mu, "endemic_sd": spread,
            "outbreak_z": z_score, "alarm_threshold": mu + z * spread,
            "alarm_prob": alarm_prob, "n_draws": np.nan,
            "observed_cases": obs, "baseline_kind": kind,
            "profile": profile, "level_ratio": ratio, "z": z,
        }


def train(df: pd.DataFrame, params: dict) -> mlflow.pyfunc.PythonModel:
    """Return a fitted Seasonal pyfunc model (parameter-free; freezes knobs)."""
    params = params or {}
    model = SeasonalModel(
        z=params.get("z", 2.0),
        min_history_years=params.get("min_history_years", 2),
        ratio_lo=params.get("ratio_lo", 0.3),
        ratio_hi=params.get("ratio_hi", 3.0),
        smooth_window=params.get("smooth_window", 5),
    )
    if model.predict(None, df).empty:
        raise ValueError("training frame produced zero forecast rows; check feature_spec.yaml scoping")
    return model
