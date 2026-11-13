"""Seasonal package self-test."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from train import FLAT_COLUMNS, SeasonalModel, train  # noqa: E402


def _frame(n_years: int, n_weeks: int = 30, seed: int = 2) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for yr in range(2024, 2024 + n_years):
        for wk in range(1, n_weeks + 1):
            lam = 8 + 5 * np.sin(2 * np.pi * wk / 52.0)
            rows.append({
                "woreda_uid": "WoR001", "disease_code": "MEN",
                "year": yr, "week": wk,
                "week_start": pd.Timestamp(f"{yr}-01-01") + pd.Timedelta(weeks=wk - 1),
                "cases": int(rng.poisson(max(lam, 0.1))),
                "rainfall_mm": 3.0, "temp_celsius": 20.0,
                "humidity_pct": 50.0, "population": None,
            })
    return pd.DataFrame(rows)


def test_train_returns_pyfunc_model():
    import mlflow.pyfunc
    assert isinstance(train(_frame(1), {"z": 2.0}), (SeasonalModel, mlflow.pyfunc.PythonModel))


def test_flat_schema_and_fallback():
    df = _frame(1)
    out = train(df, {}).predict(None, df)
    for c in FLAT_COLUMNS:
        assert c in out.columns
    assert "alarm_flag" not in out.columns and "attrs" not in out.columns
    assert out["alarm_prob"].between(0, 1).all()
    assert (out["pred_cases_mean"] >= 0).all()
    assert set(out["baseline_kind"]) == {"single-year-loo-fallback"}


def test_multiyear_branch_activates():
    out = train(_frame(2), {}).predict(None, _frame(2))
    assert set(out["baseline_kind"]) == {"same-week-multi-year"}


def test_alarm_and_param_override():
    df = _frame(1)
    df.loc[df.index[-1], "cases"] = 300
    m = train(df, {})
    loose = m.predict(None, df, params={"z": 1.0})
    strict = m.predict(None, df, params={"z": 20.0})
    assert (loose["alarm_prob"] >= 0.5).any()
    assert (strict["alarm_prob"] <= loose["alarm_prob"] + 1e-9).all()


if __name__ == "__main__":
    test_train_returns_pyfunc_model()
    test_flat_schema_and_fallback()
    test_multiyear_branch_activates()
    test_alarm_and_param_override()
    print("seasonal self-tests passed")
