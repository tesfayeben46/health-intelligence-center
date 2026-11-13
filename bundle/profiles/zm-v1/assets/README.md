# zm-v1 assets

Profile assets served by the gateway at `/api/profile/asset/<name>`.

This directory holds no logo. `profile.yaml` declares no `ministry.logo`, so the dashboards render the bundled Palladium word-mark. A Zambian deployment adds the operating organisation's logo file here and names it under `ministry.logo`; nothing else changes. No other country's crest is shipped as a fallback.
