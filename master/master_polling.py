# =============================================================================
# master/master_polling.py
# Master notebook for the FGS polling cadence.
#
# SCHEDULE THIS NOTEBOOK -- not the individual job notebooks.
# Run every 30 minutes. The ingest job checks the time window itself and
# exits cleanly outside 10:00-22:00 UTC, so this schedule can run all day.
#
# HOW TO SCHEDULE IN DATABRICKS:
#   1. Open this notebook in the Databricks workspace
#   2. Click "Schedule" in the top right
#   3. Set to "Every 30 minutes"
#   4. Ensure the cluster is set to your DASH cluster
#   5. Save
#
# This notebook chains the individual job notebooks in sequence.
# Each job returns an exit value that this notebook reads to decide
# whether to continue to the next step.
# =============================================================================

import sys
sys.path.insert(0, "/Workspace/fgs_pipeline")   # Adjust to your repo path


# =============================================================================
# STEP 1: INGEST FGS
# Poll the FFC API and write any new FGS to Delta.
# Exit values:
#   "success"        -- new FGS found and written, proceed to intersections
#   "no_change"      -- nothing new since last poll, stop here
#   "outside_window" -- outside 10:00-22:00 UTC polling window, stop here
#   "no_data"        -- API returned no statements (unusual), stop here
# =============================================================================

print("=" * 60)
print("STEP 1: Ingest FGS")
print("=" * 60)

ingest_result = dbutils.notebook.run(
    path      = "/Workspace/fgs_pipeline/jobs/01_ingest_fgs",
    timeout_seconds = 300,    # 5 minutes -- fail if ingest takes longer
    arguments = {}
)

print(f"Ingest result: {ingest_result}")

# Only proceed to intersection computation if ingest found new data.
# All other exit values mean there is nothing new to intersect.
if ingest_result != "success":
    print(f"Ingest exited with '{ingest_result}'. No intersection run needed.")
    dbutils.notebook.exit(ingest_result)


# =============================================================================
# STEP 2: COMPUTE INTERSECTIONS
# Intersect the newly ingested FGS polygons against EA flood areas
# and parliamentary constituencies.
# Only runs if step 1 returned "success".
# =============================================================================

print("=" * 60)
print("STEP 2: Compute intersections")
print("=" * 60)

intersect_result = dbutils.notebook.run(
    path      = "/Workspace/fgs_pipeline/jobs/07_compute_intersections",
    timeout_seconds = 600,    # 10 minutes
    arguments = {"recompute_all": "false"}   # Only process the new statement
)

print(f"Intersection result: {intersect_result}")

print("Polling run complete.")
dbutils.notebook.exit("success")
