# Handover: what to know to run the FGS pipeline and the Flood Planning App

This note holds what is not obvious from the code. It points to the README for how each notebook works, to the Guide in the app for how each figure is calculated, and to the Economics research page for where the damage figures come from. Where something here is unconfirmed, it says so.

## 1. What there is

| Part | Where it lives | What it does |
|---|---|---|
| The pipeline | `FGS_Notebooks` (Databricks notebooks in `jobs/`, helpers in `utils/`, settings in `config.py`, schedules in `master/`) | Builds the Delta tables |
| The app | `Flood_Planning_App` (R Shiny) | Reads those tables through a SQL warehouse and shows them |

The tables sit in catalogue `prd_dash_lab`. The schema `flood_forecasting_unrestricted` holds the tables the app reads freely. The schema `flood_forecasting_restricted` holds anything built from address-level data. Handle restricted tables and the figures drawn from them with care.

All changes go through branches and pull requests. Nobody commits to `main`.

## 2. What runs on its own

The four master notebooks in `master/` run the scheduled jobs. Schedule them with the Databricks notebook scheduler or with the asset bundle (`databricks.yml`), never both.

| Master | Cadence | Does |
|---|---|---|
| `Master_polling` | Every 15 minutes | Live readings, rapid flood guidance (jobs 36 then 37, a failure there does not stop the rest), the Flood Guidance Statement, intersections, warnings, changes, constituency situation, MP drafts, pipeline health, warning history |
| `Master_daily` | Daily | Real-time archive, constituency exposure, warning coverage by the forecast |
| `Master_weekly` | Weekly | Flood areas, MP contacts, the area and constituency lookup |
| `Master_quarterly` | Quarterly | Constituency boundaries, the lookup |

`master/Master_exposure` runs the exposure chain (jobs 33, 34 and 35) by hand. It is not scheduled. Start it from job 35 after the flood areas change, from 34 after the grid inputs change, and from 33 for a full rebuild. It always runs job 33 with `resume = yes`, and if a step ends with failed tiles it stops, and you run it again with the same start.

These are also by hand, in this order, when their source changes: 29 census and deprivation, 30 hazard rasters, 31 critical sites, 32 responder geographies. Each needs a person to point it at a new file or page, or changes only when its source does. Run the ones that changed, then `Master_exposure` from job 33. Job 38 is by hand too (section 4).

## 3. Rules that have cost time

- **Job 33: always run with `resume = yes`.** A tile is finished when its rows are in the table, and a re-run with `yes` skips finished tiles. `resume = no` rebuilds everything and has failed on serverless compute before, with start-up races on tile tables and driver memory. Failed tiles are retried by running again with `yes`.
- **Job 37 needs `shapely`.** Serverless compute does not ship it. Run `%pip install shapely` at the top of the notebook if the import fails. Job 36 does not need it.
- **Rapid flood guidance is quiet most of the time.** Job 36 stores a row only when the guidance changes, and writes a heartbeat row to `rfg_checks` on every run. The app page says when the job last checked, from that record. A newest update that is weeks old is normal.
- **The health banner ignores the rapid flood guidance check on purpose.** `rfg_uncounted` and `rfg_feed_age` are information only.
- **Job 38 inserts missing rows and never overwrites.** It is safe to run again. See section 4.
- **A blank app with a 403 means the Databricks token has expired.** See section 5.

## 4. Tables edited by hand

`economics_assumptions` is the one table people change. It holds the annual probability of each flood band, the damage per household per flood event, and the price uplift, each in low, central and high versions. To change a value, run an `UPDATE` in a Databricks notebook and set the source and the price year beside it. The app reads the table live, so there is no redeploy, and a page already open needs a reload. The Economics research page in the app records what the values are, where each came from, and what is assumed.

Do not edit other pipeline tables by hand. Rebuild them with their notebook.

## 5. Credentials

| What | Where it is set | Notes |
|---|---|---|
| FFC API key (the Flood Guidance Statement and rapid flood guidance) | A Databricks secret `fgs-pipeline/ffc-api-key`, or `FFC_API_KEY` in a `.env` file one folder above the repo | See the README |
| The app's Databricks access | `DATABRICKS_HOST`, `DATABRICKS_TOKEN` and `DATABRICKS_WAREHOUSE_ID` in the app's `.env` | The token is a personal access token and it expires. A new one needs the `sql` scope |
| The same, on Posit Connect | Environment variables on the published app | Update this as well as the local `.env`, or the published app stays blank |

When the app goes blank and the server log shows `{"error_code":403,"message":"Invalid access token."}`, make a new token, put it in both places, and restart the app.

## 6. Running the app and releasing a change

- **Run it:** Run App on Posit Workbench, or the published app on Connect. Pull before you republish. A local copy that is many merges behind has caused confusing behaviour before (a share link still using the old method).
- **Tests:** from the app folder, `Rscript -e 'testthat::test_dir("tests/testthat")'`. Some tests need `leaflet`, `plotly` or `geojsonsf` and skip when they are missing. In the pipeline repo, `pytest -q tests`.
- **Release:** branch, change, test, open a pull request, merge, pull on Workbench, republish. The page footer shows a build stamp (`APP_BUILD` in `global.R`). Bump it with each change so you can see which version is running.
- **Proof-of-concept features:** `FGS_POC_FEATURES=0` removes them. The Economics research page is one of them.

## 7. When something looks wrong

1. Open the pipeline health table (job 22) or the health banner in the app. Read which check failed before touching anything.
2. The app is blank: the token (section 5).
3. A page says a table is missing: it names the notebook that builds it.
4. Rapid flood guidance looks stale: check `rfg_checks` for the last run before assuming the feed is quiet.
5. The damage figures look wrong: read the Economics research page, then the table.

## 8. What is open

- **The damage figures and the price year** are planning values with their sources stated, not agreed figures. The sources and the working are on the Economics research page. Nobody on the project is an economist.
- **The High band's probability** (4% central, capped at 10%) drives most of the expected annual damage. It is a judgement.
- **Forecast validation data** has been requested and not received.
- **A catchment wetness index** is parked until a soil moisture source is chosen. Candidate sources were identified. None is built.
- **Not confirmed in this note:** who owns the Connect deployment and the warehouse, and whether the bundle or the notebook scheduler is the one in use. Fill these in.
