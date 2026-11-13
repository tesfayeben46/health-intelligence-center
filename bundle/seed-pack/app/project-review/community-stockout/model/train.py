"""Stockout-forecast: Holt-Winters consumption forecast -> days-to-stockout.

Applies Holt-Winters exponential smoothing to the monthly commodity mart
`bi_supply.mart_resource_allocation` (month_id x commodity_name) and emits the
unified flat ops_ml.ml_predictions schema.

Per commodity:
  1. Order the monthly `amc` (average monthly consumption) series by month_id.
  2. Fit Holt-Winters (additive trend, no seasonal) and forecast next month's
     consumption. Fall back to the series mean when history is too short.
  3. days_remaining = soh_eom_avg / (projected monthly consumption / 30).
  4. status = stockout | risk | watch | ok  (by days_remaining / SOH).

Flat-schema mapping (package contract):
  disease   = ''                       (non-disease model; ops_ml PK default)
  district  = commodity_name           (the "geography" slot)
  year/week = ISO year/week of the projection week (last month + horizon)
  pred_cases_mean/sd/lwr95/median/upr95 = projected-consumption stats
  alarm_threshold = projected consumption (expected demand over the horizon)
  alarm_prob = clip(1 - days_remaining/horizon_days, 0, 1)   (stockout risk)
  endemic_*, outbreak_z, n_draws = NULL (not applicable)
  extras (soh_now, amc_30d, projected_days_remaining, projected_stockout_date,
  status, method) -> folded into attrs jsonb by the runner.

The platform derives alarm_flag (alarm_prob >= 0.5); the package does not emit
it. Algorithm: Holt 1957 / Winters 1960 via statsmodels ExponentialSmoothing;
no third-party code is copied.
"""
from __future__ import annotations

import mlflow.pyfunc
import numpy as np
import pandas as pd
from statsmodels.tsa.holtwinters import ExponentialSmoothing

MODEL_NAME = "stockout_forecast"
MODEL_VERSION = "v1"

FLAT_COLUMNS = [
    "disease", "district", "year", "week",
    "pred_cases_mean", "pred_cases_sd", "pred_cases_lwr95",
    "pred_cases_median", "pred_cases_upr95",
    "endemic_mean", "endemic_sd", "outbreak_z",
    "alarm_threshold", "alarm_prob", "n_draws",
]


def _status(soh: float, days_remaining: float, horizon_days: int) -> str:
    if soh <= 0:
        return "stockout"
    if not np.isfinite(days_remaining):
        return "ok"
    if days_remaining <= horizon_days / 2:
        return "risk"
    if days_remaining <= horizon_days:
        return "watch"
    return "ok"


class StockoutForecastModel(mlflow.pyfunc.PythonModel):
    def __init__(self, horizon_days: int = 30, min_months: int = 4,
                 trend: str = "add"):
        self.horizon_days = int(horizon_days)
        self.min_months = int(min_months)
        self.trend = trend
        self.name = MODEL_NAME
        self.version = MODEL_VERSION

    def predict(self, context, model_input, params=None):  # noqa: D401
        params = params or {}
        horizon_days = int(params.get("horizon_days", self.horizon_days))
        df = model_input.copy()
        for c in ("amc", "soh_eom_avg"):
            if c in df:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.sort_values(["commodity_name", "month_id"])
        rows = []
        for commodity, g in df.groupby("commodity_name", sort=False):
            g = g.reset_index(drop=True)
            amc = g["amc"].astype(float).fillna(0.0).to_numpy()
            soh_now = float(g["soh_eom_avg"].astype(float).fillna(0.0).iloc[-1])
            last_month = str(g["month_id"].iloc[-1])

            projected, resid_sd, method = self._project(amc)
            daily = projected / horizon_days if projected > 0 else 0.0
            days_remaining = (soh_now / daily) if daily > 0 else float("inf")
            proj_date = self._projection_date(last_month, horizon_days)
            iso = proj_date.isocalendar()
            stockout_date = (
                (proj_date - pd.Timedelta(days=horizon_days) +
                 pd.Timedelta(days=days_remaining)).date().isoformat()
                if np.isfinite(days_remaining) else None
            )
            status = _status(soh_now, days_remaining, horizon_days)
            alarm_prob = (float(np.clip(1.0 - days_remaining / horizon_days, 0.0, 1.0))
                          if np.isfinite(days_remaining) else 0.0)
            if soh_now <= 0:
                alarm_prob = 1.0

            rows.append({
                "disease": "", "district": commodity,
                "year": int(iso[0]), "week": int(iso[1]),
                "pred_cases_mean": projected, "pred_cases_sd": resid_sd,
                "pred_cases_lwr95": max(0.0, projected - 1.96 * resid_sd),
                "pred_cases_median": projected,
                "pred_cases_upr95": projected + 1.96 * resid_sd,
                "endemic_mean": np.nan, "endemic_sd": np.nan, "outbreak_z": np.nan,
                "alarm_threshold": projected, "alarm_prob": alarm_prob,
                "n_draws": np.nan,
                # extras -> attrs
                "soh_now": soh_now, "amc_30d": float(amc[-1]),
                "projected_days_remaining": (None if not np.isfinite(days_remaining)
                                             else round(float(days_remaining), 1)),
                "projected_stockout_date": stockout_date,
                "status": status, "method": method,
            })
        return pd.DataFrame(rows)

    def _project(self, amc: np.ndarray) -> tuple[float, float, str]:
        """Return (projected_next_month_consumption, resid_sd, method)."""
        series = pd.Series(amc, dtype=float)
        if len(series) < self.min_months or series.sum() <= 0:
            mean = float(series[series > 0].mean()) if (series > 0).any() else float(series.mean())
            mean = 0.0 if not np.isfinite(mean) else max(mean, 0.0)
            sd = float(series.std(ddof=1)) if len(series) > 1 else float(np.sqrt(max(mean, 1.0)))
            return mean, (sd if np.isfinite(sd) and sd > 0 else float(np.sqrt(max(mean, 1.0)))), "mean"
        try:
            model = ExponentialSmoothing(series, trend=self.trend, seasonal=None,
                                         initialization_method="estimated").fit()
            fc = float(np.clip(model.forecast(1).iloc[0], 0.0, None))
            resid = series.to_numpy() - np.asarray(model.fittedvalues)
            sd = float(np.std(resid, ddof=1)) if len(resid) > 1 else 0.0
            if not np.isfinite(sd) or sd <= 0:
                sd = float(np.sqrt(max(fc, 1.0)))
            return fc, sd, "holt_winters"
        except Exception:
            mean = float(series.tail(3).mean())
            return max(mean, 0.0), float(np.sqrt(max(mean, 1.0))), "mean_fallback"

    def _projection_date(self, month_id: str, horizon_days: int) -> pd.Timestamp:
        try:
            end = pd.Period(month_id, freq="M").end_time.normalize()
        except Exception:
            end = pd.Timestamp.utcnow().tz_localize(None).normalize()
        return end + pd.Timedelta(days=horizon_days)


def train(df: pd.DataFrame, params: dict) -> mlflow.pyfunc.PythonModel:
    """Return a fitted Stockout pyfunc model. Holt-Winters fits per series at
    predict() time, so training only freezes hyperparameters."""
    params = params or {}
    model = StockoutForecastModel(
        horizon_days=params.get("horizon_days", 30),
        min_months=params.get("min_months", 4),
        trend=params.get("trend", "add"),
    )
    if model.predict(None, df).empty:
        raise ValueError("training frame produced zero forecast rows; check feature_spec.yaml scoping")
    return model
