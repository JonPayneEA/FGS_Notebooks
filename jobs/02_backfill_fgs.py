# =============================================================================
# jobs/02_backfill_fgs.py
# One-off historical backfill job.
#
# Run this ONCE to populate fgs_statements, fgs_risk_polygons, and
# fgs_aoc_polygons with all historical FGS records available from the API.
#
# After this completes, the live polling job (01_ingest_fgs.py) takes over.
# Do not run this again unless you need to reseed the tables from scratch.
#
# Parsing logic is shared with 01_ingest_fgs.py via utils/helpers.py.
# =============================================================================
# Rmeove cached modules - remove this code when working 
import importlib
import utils.helpers
importlib.reload(utils.helpers)

import sys

sys.path.insert(0, "/Workspace/Users/jon.payne@environment-agency.gov.uk/FGS_Notebooks/")
from config import (
    FFC_API_KEY, FFC_BASE_URL, FFC_STATEMENTS_PATH,
    TBL_STATEMENTS, TBL_RISK_POLYGONS, TBL_AOC_POLYGONS
)
from utils.helpers import (
    get_spark, ffc_get, utc_now, parse_statement_row, 
    parse_risk_rows, parse_aoc_rows
)

# =============================================================================
# SETUP
# =============================================================================

spark   = get_spark()
# Make dbutils available when running as a .py file rather than a notebook
#from pyspark.dbutils import DBUtils
#dbutils = DBUtils(spark)
#api_key = FFC_API_KEY
now_iso = utc_now().isoformat()
url     = f"{FFC_BASE_URL}{FFC_STATEMENTS_PATH}"


# =============================================================================
# FETCH, PARSE, AND WRITE ONE PAGE AT A TIME
#
# Each iteration:
#   1. Fetches one page of 50 statements from the API
#   2. Parses the statements into Spark Rows
#   3. Writes them to Delta immediately
#   4. Moves to the next page
#
# The loop ends when the API returns fewer than 50 results, which signals
# the last page. No total count is needed.
# =============================================================================

page_number   = 1
total_written = 0

while True:
    print(f"Fetching page {page_number}...")

    response = ffc_get(
        url,
        FFC_API_KEY,
        params={"page_size": 50, "page_number": page_number}
    )

    batch = response.get("statements", [])

    # Empty response means the API has no more data.
    if not batch:
        print("Empty page returned -- backfill complete.")
        break

    print(f"  Got {len(batch)} statements. Parsing...")

    # --- Parse ---
    statement_rows = [parse_statement_row(s, now_iso) for s in batch]

    risk_rows = []
    aoc_rows  = []
    for s in batch:
        risk_rows.extend(parse_risk_rows(s, now_iso))
        aoc_rows.extend(parse_aoc_rows(s, now_iso))

    # --- Write statements ---
    # Always write -- every FGS has a statement row even on low-risk days.
    spark.createDataFrame(statement_rows).write \
        .format("delta").mode("append") \
        .option("mergeSchema", "true") \
        .saveAsTable(TBL_STATEMENTS)

    # --- Write risk polygons ---
    # Guard against low-risk days with no polygons -- createDataFrame([])
    # cannot infer a schema and will throw an error.
    if risk_rows:
        spark.createDataFrame(risk_rows).write \
            .format("delta").mode("append") \
            .option("mergeSchema", "true") \
            .saveAsTable(TBL_RISK_POLYGONS)

    # --- Write AOC polygons ---
    # Same guard as risk polygons.
    if aoc_rows:
        spark.createDataFrame(aoc_rows).write \
            .format("delta").mode("append") \
            .option("mergeSchema", "true") \
            .saveAsTable(TBL_AOC_POLYGONS)

    total_written += len(batch)
    print(
        f"  Page {page_number} written -- "
        f"{len(statement_rows)} statements, "
        f"{len(risk_rows)} risk rows, "
        f"{len(aoc_rows)} AOC rows. "
        f"Running total: {total_written} statements."
    )

    # A partial page means this was the last one.
    if len(batch) < 50:
        print("Partial page -- reached end of available history.")
        break

    page_number += 1

print(f"Backfill complete. {total_written} statements written in total.")
