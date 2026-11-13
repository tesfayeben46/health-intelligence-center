"""EARS package self-test: tiny frame -> train() -> predict() -> flat schema.
Asserts the canonical forecast surface, that alarm_flag/attrs are not emitted,
both history branches, and predict-time param override. Offline; no DB/network."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from train import FLAT_COLUMNS, EarsModel, train  # noqa: E402


def _frame(n_years: int, n_weeks: int = 20, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for yr in range(2024, 2024 + n_years):
        for wk in range(1, n_weeks + 1):
            rows.append({
                "woreda_uid": "WoR001", "disease_code": "MEAS",
                "year": yr, "week": wk,
                "week_start": pd.Timestamp(f"{yr}-01-01") + pd.Timedelta(weeks=wk - 1),
                "cases": int(rng.poisson(10)),
                "rainfall_mm": 5.0, "temp_celsius": 22.0,
                "humidity_pct": 60.0, "population": None,
            })
    return pd.DataFrame(rows)


def test_train_returns_pyfunc_model():
    import mlflow.pyfunc
    m = train(_frame(1), {"k": 3.0})
    assert isinstance(m, (EarsModel, mlflow.pyfunc.PythonModel))
    assert m.k == 3.0


def test_flat_schema_and_no_forbidden_columns():
    df = _frame(1)
    out = train(df, {}).predict(None, df)
    assert not out.empty and len(out) == len(df)
    for c in FLAT_COLUMNS:
        assert c in out.columns, f"missing canonical column {c}"
    assert "alarm_flag" not in out.columns          # platform derives it
    assert "attrs" not in out.columns               # runner folds extras
    assert not out.duplicated(["disease", "district", "year", "week"]).any()
    assert out["alarm_prob"].between(0, 1).all()


def test_fallback_and_multiyear_branches():
    single = train(_frame(1), {}).predict(None, _frame(1))
    assert set(single["baseline_kind"]) == {"rolling-8w-fallback"}
    multi = train(_frame(2), {}).predict(None, _frame(2))
    assert set(multi["baseline_kind"]) == {"same-week-multi-year"}


def test_alarm_fires_on_spike_and_param_override():
    df = _frame(1)
    df.loc[df.index[-1], "cases"] = 500
    m = train(df, {})
    loose = m.predict(None, df, params={"k": 1.0})
    strict = m.predict(None, df, params={"k": 30.0})
    assert (loose["alarm_prob"] >= 0.5).any()       # spike alarms at low k
    assert (strict["alarm_prob"] <= loose["alarm_prob"] + 1e-9).all()


if __name__ == "__main__":
    test_train_returns_pyfunc_model()
    test_flat_schema_and_no_forbidden_columns()
    test_fallback_and_multiyear_branches()
    test_alarm_fires_on_spike_and_param_override()
    print("ears self-tests passed")
