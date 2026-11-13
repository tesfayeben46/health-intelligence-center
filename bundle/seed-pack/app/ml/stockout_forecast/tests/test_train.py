"""Stockout-forecast package self-test (monthly commodity grain)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from train import FLAT_COLUMNS, StockoutForecastModel, train  # noqa: E402


def _frame(n_months=12, soh=200.0, seed=3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for m in pd.period_range("2025-01", periods=n_months, freq="M"):
        rows.append({
            "month_id": str(m), "commodity_name": "ACT (Coartem)",
            "amc": float(max(100 + rng.normal(0, 8), 1)),
            "soh_eom_avg": soh, "received": 120.0, "wastage_units": 2.0,
            "stockout_days": 0, "mos": round(soh / 100, 2),
        })
    return pd.DataFrame(rows)


def test_train_returns_pyfunc_model():
    import mlflow.pyfunc
    m = train(_frame(), {"horizon_days": 30})
    assert isinstance(m, (StockoutForecastModel, mlflow.pyfunc.PythonModel))


def test_flat_schema_and_holt_winters():
    out = train(_frame(12), {}).predict(None, _frame(12))
    for c in FLAT_COLUMNS:
        assert c in out.columns
    assert "alarm_flag" not in out.columns and "attrs" not in out.columns
    assert len(out) == 1
    r = out.iloc[0]
    assert r["disease"] == "" and r["district"] == "ACT (Coartem)"
    assert r["pred_cases_mean"] > 0 and 1 <= int(r["week"]) <= 53
    assert r["method"] == "holt_winters"
    assert set(["soh_now", "amc_30d", "projected_days_remaining",
                "projected_stockout_date", "status"]).issubset(out.columns)
    assert 0.0 <= r["alarm_prob"] <= 1.0


def test_mean_fallback_short_history():
    out = train(_frame(2), {}).predict(None, _frame(2))
    assert out.iloc[0]["method"] == "mean"


def test_stockout_status_and_alarm():
    out = train(_frame(12, soh=0.0), {}).predict(None, _frame(12, soh=0.0))
    assert out.iloc[0]["status"] == "stockout"
    assert out.iloc[0]["alarm_prob"] >= 0.5


if __name__ == "__main__":
    test_train_returns_pyfunc_model()
    test_flat_schema_and_holt_winters()
    test_mean_fallback_short_history()
    test_stockout_status_and_alarm()
    print("stockout_forecast self-tests passed")
