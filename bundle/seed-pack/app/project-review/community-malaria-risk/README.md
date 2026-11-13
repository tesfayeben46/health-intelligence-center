# Community Malaria Outbreak-Risk

A sample community-contributed early-warning model that flags anomalous weekly malaria counts per woreda, adapted from the platform Farrington-style outbreak detector.

## Contents
- `model/`: the runner package the graduation integration reads (`manifest.yaml`, `train.py`, `feature_spec.yaml`, `params.yaml`, `model_card.md`, `requirements.txt`). Registry name: `community_malaria_risk`.
- `predict.py`: standalone batch inference; loads the `@champion` model from MLflow and scores the `model/feature_spec.yaml` query, as the ml-runner does.
- `analysis.ipynb`: exploratory notebook a reviewer can run in a project lab.

## Data
Reads ``bi_idsr.mart_ml_disease_input` (per woreda, disease and ISO week: cases and climate drivers)`. No PHI. Like every package here, it trains on the platform's demonstration data (see [`docs/data-provenance.md`](../../../../../docs/data-provenance.md)).

## Review state
Approved and public; the model is integrated into the shared catalogue.

`bundle/scripts/seed-graduation.sh` uploads this directory into the project's files and drives the review through the gateway's submit, approve and reject endpoints at boot (step 7.6). On approval a project becomes public and its model is integrated into the shared catalogue (MLflow `@champion`, a `model_deployment` row and a first batch of predictions), after which the nightly `ml_batch_predict` DAG scores it.
