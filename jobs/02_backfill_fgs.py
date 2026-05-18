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

import sys

sys.path.insert(0, "/Workspace/Users/jon.payne@environment-agency.gov.uk/FGS_Notebooks/")
from config import (
    SECRET_SCOPE, SECRET_KEY, FFC_BASE_URL, FFC_STATEMENTS_PATH,
    TBL_STATEMENTS, TBL_RISK_POLYGONS, TBL_AOC_POLYGONS
)
from utils.helpers import (
    get_ffc_api_key, get_spark, ffc_get, utc_now,
    parse_statement_row, parse_risk_rows, parse_aoc_rows
)


# =============================================================================
# SETUP
# =============================================================================

spark   = get_spark()
api_key = get_ffc_api_key(SECRET_SCOPE, SECRET_KEY)
now_iso = utc_now().isoformat()


# =============================================================================
# FETCH ALL HISTORICAL STATEMENTS
# The API may paginate. We follow "next" links until there are none.
#
# NOTE: Check the API docs for the exact pagination mechanism.
# The placeholder below assumes a "next" URL in the response envelope.
# Adjust the field name if the API uses a different pattern.
# =============================================================================

all_statements = []
url    = f"{FFC_BASE_URL}{FFC_STATEMENTS_PATH}"
params = {"page_size": 100}   # Request 100 per page -- adjust if API caps lower
page   = 0

while url:
    page += 1
    print(f"Fetching page {page}: {url}")
    response = ffc_get(url, api_key, params=params)

    batch = response.get("statements", [])
    all_statements.extend(batch)
    print(f"  Got {len(batch)} statements. Running total: {len(all_statements)}.")

    # Follow the next-page link if present. None ends the loop.
    # Change "next" to whatever field name the API actually uses.
    url    = response.get("next")
    params = None   # Subsequent pages encode params in the next URL already

print(f"Fetched {len(all_statements)} statements in total.")


# =============================================================================
# PARSE AND WRITE IN BATCHES
# Processing 50 statements at a time keeps driver memory usage bounded.
# Each batch is appended to the same Delta tables as the live ingest job.
# =============================================================================

BATCH_SIZE = 50

for batch_start in range(0, len(all_statements), BATCH_SIZE):
    batch = all_statements[batch_start : batch_start + BATCH_SIZE]

    statement_rows = [parse_statement_row(s, now_iso) for s in batch]

    risk_rows = []
    aoc_rows  = []
    for s in batch:
        risk_rows.extend(parse_risk_rows(s, now_iso))
        aoc_rows.extend(parse_aoc_rows(s, now_iso))

    # Write statements
    spark.createDataFrame(statement_rows).write \
        .format("delta").mode("append") \
        .option("mergeSchema", "true") \
        .saveAsTable(TBL_STATEMENTS)

    # Write risk polygons
    spark.createDataFrame(risk_rows).write \
        .format("delta").mode("append") \
        .option("mergeSchema", "true") \
        .saveAsTable(TBL_RISK_POLYGONS)

    # Write AOC polygons
    spark.createDataFrame(aoc_rows).write \
        .format("delta").mode("append") \
        .option("mergeSchema", "true") \
        .saveAsTable(TBL_AOC_POLYGONS)

    batch_num = batch_start // BATCH_SIZE + 1
    print(
        f"Batch {batch_num}: wrote {len(statement_rows)} statements, "
        f"{len(risk_rows)} risk rows, {len(aoc_rows)} AOC rows."
    )

print("Backfill complete.")
