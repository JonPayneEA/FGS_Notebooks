# =============================================================================
# master/master_quarterly.py
# Master notebook for the quarterly boundary refresh cadence.
#
# SCHEDULE THIS NOTEBOOK.
# Recommended: first day of each quarter at 03:00 UTC.
# Also run manually immediately after a general election or boundary review --
# do not wait for the next scheduled run.
#
# HOW TO SCHEDULE IN DATABRICKS:
#   1. Open this notebook in the Databricks workspace
#   2. Click "Schedule" in the top right
#   3. Set a custom cron expression: 0 0 3 1 1,4,7,10 ?
#      (03:00 UTC on 1 Jan, 1 Apr, 1 Jul, 1 Oct)
#   4. Ensure the cluster is set to your DASH cluster
#   5. Save
#
# HOW TO RUN MANUALLY AFTER AN ELECTION:
#   1. Open this notebook in the Databricks workspace
#   2. Click "Run all"
#   3. Monitor the output -- each step prints its result
#
# What this run does:
#   1. Refreshes parliamentary constituency boundaries from ONS
#   2. Recomputes the EA area / constituency lookup table
#   3. Recomputes ALL historical FGS intersections against the new boundaries
#
# After a general election, also run master_weekly.py to refresh MP details.
# Boundary refresh and MP refresh are separate notebooks because boundaries
# change rarely (every 5+ years); MP details change every election and sometimes
# between elections via by-elections and resignations.
# =============================================================================

import sys
sys.path.insert(0, "/Workspace/fgs_pipeline")


# =============================================================================
# STEP 1: REFRESH CONSTITUENCY BOUNDARIES
# Overwrites parliamentary_constituencies from the ONS Open Geography Portal.
# =============================================================================

print("=" * 60)
print("STEP 1: Refresh parliamentary constituency boundaries")
print("=" * 60)

boundary_result = dbutils.notebook.run(
    path            = "/Workspace/fgs_pipeline/jobs/05_refresh_boundaries",
    timeout_seconds = 600,    # ONS GeoJSON download can be large -- allow time
    arguments       = {}
)

print(f"Boundary refresh result: {boundary_result}")

if boundary_result != "success":
    raise Exception(f"Boundary refresh failed with: {boundary_result}")


# =============================================================================
# STEP 2: RECOMPUTE LOOKUP TABLE
# The spatial join between EA areas and constituencies must be rerun
# whenever either layer changes. New boundaries mean new intersections.
# =============================================================================

print("=" * 60)
print("STEP 2: Recompute EA area / constituency lookup")
print("=" * 60)

lookup_result = dbutils.notebook.run(
    path            = "/Workspace/fgs_pipeline/jobs/06_compute_lookup",
    timeout_seconds = 600,
    arguments       = {}
)

print(f"Lookup result: {lookup_result}")

if lookup_result != "success":
    raise Exception(f"Lookup table computation failed with: {lookup_result}")


# =============================================================================
# STEP 3: RECOMPUTE ALL INTERSECTIONS
# Reprocesses every historical FGS statement against the new constituency
# boundaries. Necessary because constituency boundaries have changed --
# historical intersections computed against the old boundaries are now stale.
#
# This is the longest step. The 2-hour timeout is conservative; actual
# runtime depends on how many historical statements are in the table.
# If it times out, increase the timeout or run 07_compute_intersections
# manually with recompute_all=true.
# =============================================================================

print("=" * 60)
print("STEP 3: Recompute all FGS intersections against new boundaries")
print("=" * 60)

intersect_result = dbutils.notebook.run(
    path            = "/Workspace/fgs_pipeline/jobs/07_compute_intersections",
    timeout_seconds = 7200,   # 2 hours
    arguments       = {"recompute_all": "true"}
)

print(f"Intersection result: {intersect_result}")

if intersect_result not in ("success", "no_work"):
    raise Exception(f"Intersection computation failed with: {intersect_result}")

print("Quarterly boundary refresh complete.")
print("Remember to also run master_weekly.py to refresh MP contact details.")
dbutils.notebook.exit("success")
