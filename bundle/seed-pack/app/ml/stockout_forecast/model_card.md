# Stockout forecast: Holt-Winters days-to-stockout

## What it predicts
Per commodity, it forecasts next-period consumption and projects how many days of stock remain before a stockout, with a status label (`stockout`, `risk`, `watch`, `ok`).

## Inputs
`bi_supply.mart_resource_allocation` (monthly, commodity grain): `amc` (average monthly consumption), `soh_eom_avg` (end-of-month stock on hand), `received`, `wastage_units`, `stockout_days`, `mos`. The model uses `amc` as the consumption series and `soh_eom_avg` as current stock.

## Training data
Trained on the platform's demonstration data (see [`docs/data-provenance.md`](../../../../../docs/data-provenance.md)): HCMIS WDM stock events, issues, receipts and stock-out episodes produced by the repository's synthesizers. Any metrics logged for this model describe its behaviour on that data; they are not evidence of performance on real supply data.

## Method
- Order each commodity's monthly `amc` series by `month_id`.
- Fit Holt-Winters (additive trend, no seasonal component) and forecast next month's consumption; fall back to the series mean when fewer than `min_months` (default 4) points are available (recorded in `attrs.method`).
- `days_remaining = soh_eom_avg / (projected monthly consumption / 30)`.
- `status`: `stockout` if SOH ≤ 0; `risk` if `days_remaining ≤ horizon/2`; `watch` if `≤ horizon`; else `ok`.

## Flat-schema mapping
- `disease = ''` (the `ops_ml` key default for non-disease models); `district = commodity_name`.
- `year` and `week` are the ISO year and week of the projection (last month plus the horizon).
- `pred_cases_mean/sd/lwr95/median/upr95` carry the projected-consumption statistics.
- `alarm_threshold` is the projected consumption (expected demand over the horizon).
- `alarm_prob = clip(1 − days_remaining/horizon_days, 0, 1)` (1.0 if already out of stock). The platform derives `alarm_flag = alarm_prob ≥ 0.5`; the package does not emit it.
- Extras `{soh_now, amc_30d, projected_days_remaining, projected_stockout_date, status, method}` are folded into the `attrs` jsonb by the runner.
- `endemic_*`, `outbreak_z` and `n_draws` are NULL: not applicable to this model.

## Limitations
- `mart_resource_allocation` averages HCMIS stock across facilities to a national commodity grain, so this is a country-level commodity proxy, not a per-facility stockout forecast. A stocked-out facility can be masked by national averages, and the reverse. Site-level alerting needs a facility-grain mart.
- Monthly grain and a short history mean no seasonal term is fitted; the forecast is a trend-only projection.
- `amc` is an average rate, not a metered daily draw-down, so days remaining is a central estimate, not a worst-case runway.
- There is no actuals binding: the truth (next-month `amc`) aligns by month, not by the projection year and week the flat schema stores, so performance scoring needs a month-grain join.
