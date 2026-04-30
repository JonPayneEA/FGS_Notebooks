# =============================================================================
# jobs/01_ingest_fgs.py
# FGS ingestion job.
#
# What this script does:
#   1. Polls the FFC API for the latest FGS statement.
#   2. Compares the issued_at timestamp against what is already stored.
#   3. If nothing has changed, exits without writing anything.
#   4. If a new or updated FGS is found, parses the full response and writes:
#        - One row to fgs_statements (top-level metadata)
#        - One row per polygon per source per day to fgs_risk_polygons
#        - One row per AOC polygon to fgs_aoc_polygons
#   5. Triggers the intersection computation job after a successful ingest.
#
# Run schedule: every 30 minutes between 10:00 and 22:00 UTC via Databricks
# Workflows. The job checks the time window itself and exits cleanly outside it.
#
# Geometry is stored as GeoJSON strings -- no spatial library needed here.
# All spatial operations happen in 07_compute_intersections.py using GeoPandas.
# =============================================================================

import sys

sys.path.insert(0, "/Workspace/fgs_pipeline")   # Adjust to your repo path in DASH
from config import (
    SECRET_SCOPE, SECRET_KEY, FFC_BASE_URL, FFC_STATEMENTS_PATH,
    POLL_START_HOUR, POLL_END_HOUR,
    TBL_STATEMENTS, TBL_RISK_POLYGONS, TBL_AOC_POLYGONS
)
from utils.helpers import (
    get_ffc_api_key, get_spark, ffc_get, utc_now, table_exists,
    parse_statement_row, parse_risk_rows, parse_aoc_rows
)


# =============================================================================
# SETUP
# =============================================================================

spark   = get_spark()
api_key = get_ffc_api_key(SECRET_SCOPE, SECRET_KEY)
now     = utc_now()


# =============================================================================
# TIME WINDOW CHECK
# Exit cleanly if this job fires outside the polling window.
# Databricks Workflows reschedules the next run automatically.
# =============================================================================

if not (POLL_START_HOUR <= now.hour < POLL_END_HOUR):
    print(f"Outside polling window ({POLL_START_HOUR}:00-{POLL_END_HOUR}:00 UTC). Exiting.")
    dbutils.notebook.exit("outside_window")


# =============================================================================
# FETCH LATEST STATEMENT FROM API
# =============================================================================

print("Fetching latest FGS from FFC API...")

url      = f"{FFC_BASE_URL}{FFC_STATEMENTS_PATH}"
response = ffc_get(url, api_key)

# The API returns a list of statements ordered newest first.
statements = response.get("statements", [])
if not statements:
    print("No statements returned by API. Exiting.")
    dbutils.notebook.exit("no_data")

latest           = statements[0]
latest_issued_at = latest["issued_at"]
print(f"Latest FGS issued_at from API: {latest_issued_at}")


# =============================================================================
# CHECK WHETHER WE HAVE ALREADY INGESTED THIS STATEMENT
# Compare against the most recent issued_at in fgs_statements.
# If they match, nothing has changed -- exit cleanly.
# =============================================================================

if table_exists(spark, TBL_STATEMENTS):
    last_stored = (
        spark.table(TBL_STATEMENTS)
        .orderBy("issued_at", ascending=False)
        .limit(1)
        .collect()
    )

    if last_stored and last_stored[0]["issued_at"] == latest_issued_at:
        print("No new FGS since last poll. Exiting.")
        dbutils.notebook.exit("no_change")

print("New or updated FGS detected. Beginning ingest...")


# =============================================================================
# PARSE AND WRITE
# Parsing logic lives in utils/helpers.py so the backfill job reuses it.
# =============================================================================

now_iso = now.isoformat()

# --- fgs_statements: one row for this FGS issue ---
stmt_row = parse_statement_row(latest, now_iso)
stmt_df  = spark.createDataFrame([stmt_row])
(
    stmt_df.write
    .format("delta")
    .mode("append")
    .option("mergeSchema", "true")   # Tolerate new API fields without failing
    .saveAsTable(TBL_STATEMENTS)
)
print(f"Written 1 row to {TBL_STATEMENTS}.")

# --- fgs_risk_polygons: one row per polygon per source per day ---
risk_rows = parse_risk_rows(latest, now_iso)
risk_df   = spark.createDataFrame(risk_rows)
(
    risk_df.write
    .format("delta")
    .mode("append")
    .option("mergeSchema", "true")
    .saveAsTable(TBL_RISK_POLYGONS)
)
print(f"Written {len(risk_rows)} rows to {TBL_RISK_POLYGONS}.")

# --- fgs_aoc_polygons: cartographic reference polygons ---
aoc_rows = parse_aoc_rows(latest, now_iso)
aoc_df   = spark.createDataFrame(aoc_rows)
(
    aoc_df.write
    .format("delta")
    .mode("append")
    .option("mergeSchema", "true")
    .saveAsTable(TBL_AOC_POLYGONS)
)
print(f"Written {len(aoc_rows)} rows to {TBL_AOC_POLYGONS}.")

print("FGS ingest complete.")
dbutils.notebook.exit("success")
