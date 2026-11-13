"""elmis_consumption_forecast v2 self-test: the forecast contract's testable promises."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from train import (FLAT_COLUMNS, METHODS, ElmisConsumptionForecastModel,  # noqa: E402
                   demand_class, eligible, f_holt_damped, train)

MONTHS = [str(m) for m in pd.period_range("2017-09", periods=12, freq="M")]


def _frame(series=(("F001", "P01"), ("F001", "P02"), ("F002", "P01"), ("F003", "P03")), seed=7, gap_month=None):
    rng = np.random.default_rng(seed); rows = []
    for k, (fac, prod) in enumerate(series):
        for i, m in enumerate(MONTHS):
            if gap_month is not None and m == gap_month:
                continue
            base = 120 + 3 * i if k != 3 else (0 if i % 3 else 60)      # series 4 is lumpy/intermittent
            rows.append({"month_id": m, "facility_code": fac, "product_code": prod, "product_name": f"Product {prod}",
                         "adjusted_demand": float(max(base + rng.normal(0, 10), 0)), "demand_censored": False,
                         "stock_in_hand": 300.0, "amc": 125.0, "region_id": "50", "facility_type_code": "HC",
                         "programme_name": "Antiretroviral Drugs"})
    return pd.DataFrame(rows)


def test_train_returns_model_with_metrics_and_evaluation():
    m = train(_frame(), {})
    assert "champion_rule_passed" in m.metrics and "holdout_wape" in m.metrics
    assert {"fold_ledger", "per_class", "quantiles_scaled"} <= set(m.evaluation)
    assert all(isinstance(v, float) for v in m.metrics.values())


def test_fold_ledger_is_expanding_and_holdout_once():
    led = train(_frame(), {}).evaluation["fold_ledger"]
    hold = [r for r in led if r["split"] == "holdout"]
    assert len(hold) == 1 and hold[0]["as_of_month"] == MONTHS[-2] and hold[0]["target_month"] == MONTHS[-1]
    tune = sorted(r["as_of_month"] for r in led if r["split"] == "tuning")
    assert tune and tune[-1] < hold[0]["as_of_month"]


def test_no_leakage_future_spike_does_not_change_past_forecast():
    """A forecast made at an origin must not move when a later month changes."""
    y = np.array([100, 110, 105, 120, 115, 125, 130, np.nan, np.nan, np.nan, np.nan, np.nan], float)
    p = ElmisConsumptionForecastModel().p
    before = {m: fn(y[:7], p) for m, fn in __import__("train").FORECASTERS.items()}
    y2 = y.copy(); y2[9] = 1e6                                   # a spike after the origin
    after = {m: fn(y2[:7], p) for m, fn in __import__("train").FORECASTERS.items()}
    assert before == after
    assert demand_class(y[:7]) == demand_class(y2[:7]) and eligible(y[:7], 6) == eligible(y2[:7], 6)


def test_gaps_are_gaps_not_zeros():
    y_gap = np.array([100, 100, np.nan, 100, 100, 100], float)
    y_zero = np.array([100, 100, 0.0, 100, 100, 100], float)
    p = ElmisConsumptionForecastModel().p
    assert f_holt_damped(y_gap, p) != f_holt_damped(y_zero, p)
    assert abs(f_holt_damped(y_gap, p) - 100) < 1e-6          # a gap does not drag the level down


def test_censored_actuals_are_not_scored():
    df = _frame()
    df.loc[df.month_id == MONTHS[-1], "demand_censored"] = True    # every holdout actual censored
    bt = ElmisConsumptionForecastModel().backtest(df)
    assert (bt.split == "holdout").sum() == 0


def test_predict_contract_keys_and_flat_schema():
    contract = yaml.safe_load(open(HERE / "forecast_contract.yaml"))
    out = train(_frame(), {}).predict(None, _frame())
    for c in ["district", "year", "week"]:
        assert c in out.columns
    for k in contract["attrs"]:
        assert k in out.columns, k
    assert (out.horizon == 1).all() and (out.as_of_month == MONTHS[-1]).all()
    assert (out.p10 <= out.p50).all() and (out.p50 <= out.p90).all() and (out.p10 >= 0).all()
    assert out.pred_cases_lwr95.isna().all() and out.pred_cases_upr95.isna().all()   # Gaussian band not used
    assert out.method.isin(METHODS).all()


def test_performance_rows_one_per_fold_window():
    m = train(_frame(), {})
    led = m.evaluation["fold_ledger"]
    assert len(m.performance_rows) == len(led)
    for r in m.performance_rows:
        assert r["window_start"] < r["window_end"] and r["sample_size"] > 0
        assert all(k.startswith("backtest_") for k in r["metrics"])
        split = "holdout" if any(k.startswith("backtest_holdout_") for k in r["metrics"]) else "tuning"
        assert f"backtest_{split}_wape" in r["metrics"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("ok")
