"""EWARS-lite NB2 GLM package self-test."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from train import FLAT_COLUMNS, EwarsLiteModel, train  # noqa: E402


def _frame(n_woredas=8, n_weeks=40, with_pop=True, regions=("OR", "AA"),
           seed=7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for w in range(n_woredas):
        region = regions[w % len(regions)]
        for wk in range(1, n_weeks + 1):
            rain = 40 + 30 * np.sin(2 * np.pi * wk / 52.0) + rng.normal(0, 3)
            rows.append({
                "woreda_uid": f"WoR{w:03d}", "disease_code": "MAL", "region_id": region,
                "year": 2025, "week": wk,
                "week_start": pd.Timestamp("2025-01-01") + pd.Timedelta(weeks=wk - 1),
                "cases": int(rng.poisson(max(2 + 0.06 * max(rain, 0), 0.5))),
                "rainfall_mm": rain, "temp_celsius": 22 + 4 * np.sin(2 * np.pi * wk / 52.0),
                "humidity_pct": 55 + 10 * np.cos(2 * np.pi * wk / 52.0),
                "population": (50000.0 + 1000 * w) if with_pop else None,
            })
    return pd.DataFrame(rows)


def test_train_fits_glm():
    import mlflow.pyfunc
    m = train(_frame(with_pop=False), {})
    assert isinstance(m, (EwarsLiteModel, mlflow.pyfunc.PythonModel))
    assert m.fits["MAL"]["kind"] == "nb_glm"          # enough rows -> real GLM
    assert m.fits["MAL"]["use_offset"] is False        # no population -> no offset


def test_flat_schema_and_bands():
    df = _frame(with_pop=False)
    out = train(df, {}).predict(None, df)
    for c in FLAT_COLUMNS:
        assert c in out.columns
    assert "alarm_flag" not in out.columns and "attrs" not in out.columns
    assert len(out) == len(df)
    assert (out["n_draws"] == 500).all()
    assert out["alarm_prob"].between(0, 1).all()
    assert (out["pred_cases_lwr95"] <= out["pred_cases_upr95"] + 1e-9).all()
    assert (out["glm_kind"] == "nb_glm").all() and (~out["use_offset"]).all()


def test_population_offset_used_when_present():
    m = train(_frame(with_pop=True), {})
    assert m.fits["MAL"]["use_offset"] is True
    out = m.predict(None, _frame(with_pop=True))
    assert bool(out.iloc[0]["use_offset"]) is True


def test_mean_fallback_when_too_few_rows():
    m = train(_frame(n_woredas=1, n_weeks=6, with_pop=False), {})
    assert m.fits["MAL"]["kind"] == "mean"
    out = m.predict(None, _frame(n_woredas=1, n_weeks=6, with_pop=False))
    for c in FLAT_COLUMNS:
        assert c in out.columns
    assert (out["glm_kind"] == "mean").all()


if __name__ == "__main__":
    test_train_fits_glm()
    test_flat_schema_and_bands()
    test_population_offset_used_when_present()
    test_mean_fallback_when_too_few_rows()
    print("ewars_lite self-tests passed")
