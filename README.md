# FGS Pipeline -- DASH / Databricks

Ingests the Flood Guidance Statement from the FFC API v3, stores it as a
queryable Delta Lake history, and maintains a set of spatial intersection
tables linking FGS risk polygons to EA flood areas and parliamentary constituencies.

---

## Before you start

### 1. Set up the Databricks secret scope

Run this once from the Databricks CLI on your local machine. You need the
Databricks CLI installed and configured first.

```bash
databricks secrets create-scope fgs-pipeline
databricks secrets put-secret fgs-pipeline ffc-api-key
# Paste your FFC API key when prompted
```

### 2. Update config.py

Open `config.py` and substitute:
- `CATALOG` -- your Unity Catalog name (ask your DASH admin)
- `SCHEMA` -- the schema you want these tables created in (default: `fgs`)
- `SECRET_SCOPE` and `SECRET_KEY` -- only if you used different names above

Everything else in `config.py` can be left as-is unless the external URLs change.

### 3. Deploy to the Databricks workspace

Upload the `fgs_pipeline` folder to your Databricks workspace at:
`/Workspace/fgs_pipeline`

If you use Git integration in DASH, clone the repo directly instead.

### 4. Run the backfill once

Run `jobs/02_backfill_fgs.py` manually as a one-off job to seed the full
historical record from the API. This may take several minutes depending on
how far back the API serves data.

### 5. Deploy the workflows

Import the definitions in `workflows/databricks_workflows.yml` into
Databricks Workflows, or deploy via the Databricks CLI. Replace
`YOUR_CLUSTER_ID` with your actual cluster ID in each task definition.

---

## Table inventory

| Table | Refresh | Description |
|---|---|---|
| `fgs_statements` | Per FGS issue | One row per issued FGS. Top-level metadata. |
| `fgs_risk_polygons` | Per FGS issue | One row per polygon per source per day. |
| `fgs_aoc_polygons` | Per FGS issue | Cartographic AOC polygons. |
| `ea_flood_warning_areas` | Weekly | EA Flood Warning Areas from WFS. |
| `ea_flood_alert_areas` | Weekly | EA Flood Alert Areas from WFS. |
| `parliamentary_constituencies` | Quarterly | ONS constituency boundaries. |
| `mp_contact_details` | Weekly | Current MPs and contact details. |
| `ea_area_constituency_lookup` | Weekly / quarterly | Static spatial join: EA areas to constituencies. |
| `fgs_ea_area_intersections` | Per FGS issue | FGS polygons intersecting EA areas (25% threshold). |
| `fgs_constituency_intersections` | Per FGS issue | FGS polygons intersecting constituencies (any overlap). |

---

## Workflow summary

```
EVERY 30 MIN (10:00-22:00 UTC)
  01_ingest_fgs
    └── 07_compute_intersections (if new data)

WEEKLY (Sun 02:00 UTC)
  03_refresh_ea_flood_areas ──┐
                               ├── 06_compute_lookup ── 07_compute_intersections (all)
  04_refresh_mp_details ──────┘

QUARTERLY (or post-election)
  05_refresh_boundaries ── 06_compute_lookup ── 07_compute_intersections (all)
```

---

## Typical query: which MPs need to be alerted?

```sql
-- All MPs whose constituencies intersect an FGS risk area for today,
-- for river flooding at risk level 3 or above.

SELECT DISTINCT
    mp.name               AS mp_name,
    mp.party,
    mp.email,
    mp.constituency_name,
    fi.source,
    fi.risk_level_max,
    fi.forecast_date

FROM fgs.fgs_constituency_intersections fi
JOIN fgs.mp_contact_details mp
  ON fi.constituency_id = mp.constituency_id  -- requires constituency_id join via lookup

WHERE fi.forecast_date = CURRENT_DATE
  AND fi.source        = 'river'
  AND fi.risk_level_max >= 3

ORDER BY fi.risk_level_max DESC, mp.constituency_name;
```

---

## After a general election

Run these two jobs manually, in order, before the next polling window:

1. `05_refresh_boundaries`
2. `04_refresh_mp_details`

The workflows will handle the lookup and intersection recomputation automatically.

---

## Files

```
fgs_pipeline/
├── config.py                          All settings -- edit this first
├── utils/
│   └── helpers.py                     Shared utilities
├── jobs/
│   ├── 01_ingest_fgs.py               Live polling and ingest
│   ├── 02_backfill_fgs.py             One-off historical backfill
│   ├── 03_refresh_ea_flood_areas.py   Weekly EA boundary refresh
│   ├── 04_refresh_mp_details.py       Weekly MP contact refresh
│   ├── 05_refresh_boundaries.py       Quarterly constituency refresh
│   ├── 06_compute_lookup.py           EA area / constituency spatial join
│   └── 07_compute_intersections.py    FGS polygon intersection computation
└── workflows/
    └── databricks_workflows.yml       Workflow definitions
```
