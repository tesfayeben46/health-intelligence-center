# gh-v1 assets

Profile assets served by the gateway at `/api/profile/asset/<name>`.

This directory holds no logo. `profile.yaml` declares no `ministry.logo`, so the dashboards render the bundled Palladium word-mark. A Ghana deployment adds the operating organisation's logo file here and names it under `ministry.logo`; the SPAs then show it in place of the word-mark. No other country's crest is shipped as a fallback.
