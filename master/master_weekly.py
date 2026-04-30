# =============================================================================
# master/master_weekly.py
# Master notebook for the weekly refresh cadence.
#
# SCHEDULE THIS NOTEBOOK.
# Recommended: Sunday 02:00 UTC, weekly.
#
# HOW TO SCHEDULE IN DATABRICKS:
#   1. Open this notebook in the Databricks workspace
#   2. Click "Schedule" in the top right
#   3. Set to "Weekly", day "Sunday", time "02:00 UTC"
#   4. Ensure the cluster is set to your DASH cluster
#   5. Save
#
# What this run does:
#   1. Refreshes EA Flood Warning Areas and Flood Alert Areas from the EA WFS
#   2. Refreshes MP contact details from the Parliament API
#   3. Recomputes the EA area / constituency lookup table
#   4. Recomputes ALL historical FGS intersections against the fresh boundaries
#
# Steps 1 and 2 are independent and could run in parallel, but Databricks
# notebook scheduling runs sequentially. The total runtime is acceptable.
# Steps 3 and 4 depend on steps 1 and 2 completing successfully.
#
# If any step fails, the notebook stops and subsequent steps do not run.
# Databricks will notify you of the failure via the job alert settings.
# =============================================================================

import sys
sys.path.insert(0, "/Workspace/fgs_pipeline")


# =============================================================================
# STEP 1: REFRESH EA FLOOD AREAS
# Overwrites ea_flood_warning_areas and ea_flood_alert_areas from the EA WFS.
# =============================================================================

print("=" * 60)
print("STEP 1: Refresh EA flood areas")
print("=" * 60)

ea_result = dbutils.notebook.run(
    path            = "/Workspace/fgs_pipeline/jobs/03_refresh_ea_flood_areas",
    timeout_seconds = 600,    # 10 minutes -- WFS fetch can be slow
    arguments       = {}
)

print(f"EA refresh result: {ea_result}")

if ea_result != "success":
    raise Exception(f"EA flood area refresh failed with: {ea_result}")


# =============================================================================
# STEP 2: REFRESH MP CONTACT DETAILS
# Overwrites mp_contact_details from the Parliament Members API.
# Makes one API call per MP (650 calls) -- allow sufficient timeout.
# =============================================================================

print("=" * 60)
print("STEP 2: Refresh MP contact details")
print("=" * 60)

mp_result = dbutils.notebook.run(
    path            = "/Workspace/fgs_pipeline/jobs/04_refresh_mp_details",
    timeout_seconds = 1800,   # 30 minutes -- 650 API calls takes time
    arguments       = {}
)

print(f"MP refresh result: {mp_result}")

if mp_result != "success":
    raise Exception(f"MP contact refresh failed with: {mp_result}")


# =============================================================================
# STEP 3: RECOMPUTE LOOKUP TABLE
# Spatial join of EA flood areas against constituencies.
# Depends on steps 1 and 2 both succeeding.
# =============================================================================

print("=" * 60)
print("STEP 3: Recompute EA area / constituency lookup")
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
# STEP 4: RECOMPUTE ALL INTERSECTIONS
# Reprocesses every historical FGS statement against the freshly updated
# EA area boundaries. This ensures historical records stay accurate if
# an EA flood area boundary has changed since it was first intersected.
# =============================================================================

print("=" * 60)
print("STEP 4: Recompute all FGS intersections")
print("=" * 60)

intersect_result = dbutils.notebook.run(
    path            = "/Workspace/fgs_pipeline/jobs/07_compute_intersections",
    timeout_seconds = 7200,   # 2 hours -- reprocessing full history may take time
    arguments       = {"recompute_all": "true"}
)

print(f"Intersection result: {intersect_result}")

if intersect_result not in ("success", "no_work"):
    raise Exception(f"Intersection computation failed with: {intersect_result}")

print("Weekly refresh complete.")
dbutils.notebook.exit("success")
