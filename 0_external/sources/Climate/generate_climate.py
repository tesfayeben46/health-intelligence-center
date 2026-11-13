#!/usr/bin/env python3
"""Generate the Climate source's synthetic weekly rainfall, temperature and humidity for Ethiopia.

Weekly series per region for the 53 weeks ending 2026-W27, with an Ethiopian
seasonal shape (kiremt main rains around weeks 24-38, belg short rains around
weeks 9-19; lowlands hotter and drier). The values are generated, not
observations. Climate is a source of its own; the IDSR seeder
(0_external/sources/IDSR/seed_idsr_dhis2.py) reads this CSV to couple case
counts to rainfall with a lag.

Output (served by the climate-api container):
  bundle/seeds/sources/climate/weekly.csv

Deterministic (fixed seed). To change the data, edit the region profile below,
re-run this script, then re-run the IDSR seeder.
"""
from __future__ import annotations

import csv
import hashlib
import os
import random
from datetime import date, timedelta

RNG = random.Random(20260701)

# Repo root is three levels up (0_external/sources/Climate/ -> repo).
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
CLIMATE_CSV = os.path.join(REPO, "bundle/seeds/sources/climate/weekly.csv")

# 53-week window ending 2026-W27 (aligns with the surveillance and HMIS marts).
N_WEEKS = 53

def iso_weeks():
    last_monday = date.fromisocalendar(2026, 27, 1)
    out = []
    for i in range(N_WEEKS - 1, -1, -1):
        mon = last_monday - timedelta(weeks=i)
        y, w, _ = mon.isocalendar()
        out.append((f"{y}-W{w:02d}", mon))
    return out

# Region climate profile: code -> (name, base_rain_mm, base_temp_c, lowland 0..1)
REGIONS = {
    "OR": ("Oromia",              90, 21, 0.4),
    "AM": ("Amhara",              78, 19, 0.2),
    "TI": ("Tigray",              55, 18, 0.2),
    "SO": ("Somali",              30, 28, 1.0),
    "SI": ("Sidama",              95, 20, 0.3),
    "SW": ("South West Ethiopia",110, 22, 0.5),
    "SE": ("South Ethiopia",     100, 21, 0.5),
    "CE": ("Central Ethiopia",    92, 20, 0.3),
    "AF": ("Afar",                22, 30, 1.0),
    "BG": ("Benishangul-Gumuz",  115, 24, 0.6),
    "GA": ("Gambela",            120, 27, 0.9),
    "AA": ("Addis Ababa",         70, 17, 0.0),
    "DD": ("Dire Dawa",           45, 26, 0.9),
    "HA": ("Harari",              55, 23, 0.7),
}

def _bump(w, center, width):
    """Gaussian-ish seasonal bump on a 1..53 week axis, wrapping the year."""
    d = min(abs(w - center), 53 - abs(w - center))
    return pow(2.718281828, -(d * d) / (2 * width * width))

def _hash(*parts) -> str:
    return str(int(hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:15], 16))

def gen_climate(weeks):
    rows = []
    for code, (name, base_rain, base_temp, lowland) in REGIONS.items():
        for wk, mon in weeks:
            wnum = int(wk.split("-W")[1])
            kiremt = _bump(wnum, 31, 7)              # main rains
            belg = 0.4 * _bump(wnum, 14, 5)          # short rains
            season = (kiremt + belg) * (1.0 - 0.5 * lowland)
            rain = max(0.0, base_rain * season * (0.7 + 0.6 * RNG.random()))
            temp = base_temp + 3.5 * (1 - season) - 2.0 * kiremt + RNG.uniform(-1.2, 1.2)
            # relative humidity: wetter in the rains, drier and hotter in lowlands
            humidity = 42 + 38 * season - 12 * lowland + RNG.uniform(-4, 4)
            humidity = max(15.0, min(95.0, humidity))
            rows.append((wk, mon.isoformat(), code, round(rain, 1), round(temp, 1),
                         round(humidity, 1), _hash("clim", code, wk)))
    return rows

def main():
    weeks = iso_weeks()
    os.makedirs(os.path.dirname(CLIMATE_CSV), exist_ok=True)
    rows = gen_climate(weeks)
    with open(CLIMATE_CSV, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["iso_week", "week_start", "region_id", "rainfall_mm",
                    "temp_celsius", "humidity_pct", "source_record_hash"])
        w.writerows(rows)
    print(f"climate: {len(rows)} rows, {len(REGIONS)} regions -> {CLIMATE_CSV}")

if __name__ == "__main__":
    main()
