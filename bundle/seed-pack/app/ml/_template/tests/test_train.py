"""Package self-test: the minimum bar every NHDW model package must clear.

The platform validators run `tests/` before a package may train (community
uploads are rejected on failure); run it locally with either

    python tests/test_train.py          # stdlib only
    pytest tests/                       # if you have pytest

The smoke test builds a tiny synthetic frame matching feature_spec.yaml's
input.signature, calls train(), calls predict(), and asserts the output
honours the flat forecast schema. It runs in seconds and touches no
database and no network: it validates the contract, not the statistics.

Keep this file passing offline with only the baked runner libs
(pandas / numpy / mlflow) plus whatever requirements.txt adds.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

# Make the package root importable no matter where the test is invoked from.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from train import train  # noqa: E402  (path bootstrap above)


def _tiny_frame() -> pd.DataFrame:
    """20 weeks x 2 woredas x 1 disease, mirroring input.signature exactly.

    Deterministic (seeded) so self-tests never flake.
    """
    rng = np.random.default_rng(42)
    rows = []
    for woreda in ("WoR001", "WoR002"):
        for week in range(1, 21):
            rows.append({
                "woreda_uid": woreda,
                "disease_code": "MEAS",
                "year": 2026,
                "week": week,
                "week_start": pd.Timestamp("2026-01-01")
                + pd.Timedelta(weeks=week - 1),
                # flat endemic baseline + a spike in the final week so the
                # alarm path is exercised, not just the quiet path
                "cases": int(rng.poisson(5)) + (40 if week == 20 else 0),
                "rainfall_mm": float(rng.uniform(0, 120)),
                "temp_celsius": float(rng.uniform(12, 30)),
                "humidity_pct": float(rng.uniform(30, 90)),
                "population": None,          # nullable until DHIS2 pop lands
            })
    return pd.DataFrame(rows)


def test_train_returns_pyfunc_model():
    import mlflow.pyfunc
    model = train(_tiny_frame(), {"k": 3.0})
    assert isinstance(model, mlflow.pyfunc.PythonModel), (
        "train() must return an mlflow.pyfunc.PythonModel instance")


def test_predict_emits_flat_forecast_schema():
    df = _tiny_frame()
    model = train(df, {"k": 3.0})
    out = model.predict(None, df)

    assert isinstance(out, pd.DataFrame) and not out.empty
    # Required keys of the flat schema.
    for col in ("district", "year", "week"):
        assert col in out.columns, f"missing required flat-schema column {col}"
    # This template is a forecaster, so the forecast surface must be there.
    for col in ("pred_cases_mean", "endemic_mean", "alarm_threshold",
                "alarm_prob"):
        assert col in out.columns, f"missing forecast column {col}"

    # One row per series, keyed uniquely.
    assert not out.duplicated(["disease", "district", "year", "week"]).any()
    # alarm_prob is a probability; the spiked series must raise an alarm.
    assert out["alarm_prob"].between(0, 1).all()
    assert (out["alarm_prob"] >= 0.5).any(), "the seeded spike should alarm"
    # The platform derives alarm_flag; packages must not emit it.
    assert "alarm_flag" not in out.columns


def test_predict_time_param_override_is_honoured():
    df = _tiny_frame()
    model = train(df, {"k": 3.0})
    strict = model.predict(None, df, params={"k": 30.0})
    # A huge k pushes thresholds up and probabilities down.
    assert (strict["alarm_prob"] <= model.predict(None, df,
            params={"k": 1.0})["alarm_prob"]).all()


if __name__ == "__main__":
    for fn in (test_train_returns_pyfunc_model,
               test_predict_emits_flat_forecast_schema,
               test_predict_time_param_override_is_honoured):
        fn()
        print(f"ok · {fn.__name__}")
    print("all package self-tests passed")
