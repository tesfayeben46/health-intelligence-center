#!/usr/bin/env python3
"""Standalone batch inference for the community_ewars_malaria forecaster.

Load the registered @champion model from MLflow and score the
model/feature_spec.yaml query, printing the flat forecast rows. Mirrors what
the ml-runner does (`runner.py predict-batch community_ewars_malaria`); provided
so the project is runnable outside the runner and the essential-files check
finds an inference script.

Usage:
    python predict.py
"""
from __future__ import annotations

import os
from pathlib import Path

import mlflow
import pandas as pd
import yaml

MODEL = "community_ewars_malaria"
SPEC = Path(__file__).with_name("model") / "feature_spec.yaml"


def main() -> None:
    mlflow.set_tracking_uri(
        os.environ.get("MLFLOW_TRACKING_URI",
                       "http://mlflow.nhdw.svc.cluster.local:5000"))
    query = yaml.safe_load(SPEC.read_text())["input"]["query"]
    import sqlalchemy
    eng = sqlalchemy.create_engine(os.environ["OPS_DB_URL"])
    frame = pd.read_sql(query, eng)

    model = mlflow.pyfunc.load_model(f"models:/{MODEL}@champion")
    preds = model.predict(frame)
    out = pd.DataFrame(preds) if not isinstance(preds, pd.DataFrame) else preds
    print(out.head(20).to_string(index=False))
    print(f"\nscored {len(out)} rows for {MODEL}")


if __name__ == "__main__":
    main()
