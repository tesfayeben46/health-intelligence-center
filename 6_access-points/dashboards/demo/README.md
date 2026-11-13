# demo

Catalog stub for a future demo-control dashboard. There is no UI, BFF or
container image in this directory.

## Layout

```
catalog/
  dashboard.yaml          id, title, description of the dashboard
  pages/demo--*.yaml      four pages (scenarios, data injection, stress tests,
                          instructions) with id, title and ordering only
```

## How it is used

`bundle/scripts/collect-catalog.py` picks up every dashboard directory that has
`catalog/dashboard.yaml`, so these files contribute a `demo` dashboard and its
pages to `catalog.json`, which the security-seed loader (`load_security_seed.py`)
upserts into the dashboard and page catalog tables. Pages have no sections or widgets yet, so nothing renders
until a UI is built against them.

The demo-control API that such a surface would call is implemented in the
gateway (`6_access-points/api/nhdw-gateway/app/routes/demo.py`, `/api/demo/*`).
