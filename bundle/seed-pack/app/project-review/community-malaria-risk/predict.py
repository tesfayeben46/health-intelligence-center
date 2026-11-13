#!/usr/bin/env python3
"""Standalone batch inference for the community_malaria_risk detector.

The platform normally scores this model through the ml-runner
(`runner.py predict-batch community_malaria_risk`), which fills
ops_ml.ml_predictions. This script is the human-facing equivalent: load the
registered @champion model from MLflow and score the model/feature_spec.yaml
query, printing the flat forecast rows. It is provided so the project is
runnable outside the runner and so the graduation essential-files check finds
an inference script.

Usage:
    python predict.py            # score the champion; print the first rows
"""
from __future__ import annotations

import os
from pathlib import Path

import mlflow
import pandas as pd
import yaml

MODEL = "community_malaria_risk"
SPEC = Path(__file__).with_name("model") / "feature_spec.yaml"


def _feature_query() -> str:
    spec = yaml.safe_load(SPEC.read_text())
    return spec["input"]["query"]


def main() -> None:
    mlflow.set_tracking_uri(
        os.environ.get("MLFLOW_TRACKING_URI",
                       "http://mlflow.nhdw.svc.cluster.local:5000"))
    # The batch feature frame comes from the governed mart (read-only).
    import sqlalchemy
    eng = sqlalchemy.create_engine(
        os.environ["OPS_DB_URL"])              # provided in the project lab env
    frame = pd.read_sql(_feature_query(), eng)

    model = mlflow.pyfunc.load_model(f"models:/{MODEL}@champion")
    preds = model.predict(frame)
    out = pd.DataFrame(preds) if not isinstance(preds, pd.DataFrame) else preds
    print(out.head(20).to_string(index=False))
    print(f"\nscored {len(out)} rows for {MODEL}")


if __name__ == "__main__":
    main()
