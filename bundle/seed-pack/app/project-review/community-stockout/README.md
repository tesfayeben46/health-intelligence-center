# Community Stockout Forecast

A sample community-contributed Holt-Winters consumption forecast that projects days to stockout per commodity, adapted from the platform stockout model.

## Contents
- `model/`: the runner package the graduation integration reads (`manifest.yaml`, `train.py`, `feature_spec.yaml`, `params.yaml`, `model_card.md`, `requirements.txt`). Registry name: `community_stockout`.
- `predict.py`: standalone batch inference; loads the `@champion` model from MLflow and scores the `model/feature_spec.yaml` query, as the ml-runner does.
- `analysis.ipynb`: exploratory notebook a reviewer can run in a project lab.

## Data
Reads ``bi_supply.mart_resource_allocation` (monthly commodity grain: AMC, stock on hand, receipts, wastage, months of stock)`. No PHI. Like every package here, it trains on the platform's demonstration data (see [`docs/data-provenance.md`](../../../../../docs/data-provenance.md)).

## Review state
Submitted; the reviewer requested changes (`changes_requested`). The owner can address the feedback and resubmit.

`bundle/scripts/seed-graduation.sh` uploads this directory into the project's files and drives the review through the gateway's submit, approve and reject endpoints at boot (step 7.6). On approval a project becomes public and its model is integrated into the shared catalogue (MLflow `@champion`, a `model_deployment` row and a first batch of predictions), after which the nightly `ml_batch_predict` DAG scores it.
