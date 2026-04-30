# =============================================================================
# workflows/scheduling_guide.md
# How to schedule the FGS pipeline notebooks in Databricks.
#
# You do not need Databricks Workflows or ETL job permissions.
# All scheduling is done through the standard Databricks notebook scheduler,
# which is available to all users.
# =============================================================================

# HOW TO SCHEDULE A NOTEBOOK IN DATABRICKS
# -----------------------------------------
# 1. Open the notebook in the Databricks workspace
# 2. Click the "Schedule" button in the top right of the notebook toolbar
# 3. Set the schedule as described below for each master notebook
# 4. Set the cluster to your DASH cluster
# 5. Optionally add an email alert for failures (recommended)
# 6. Click "Save"

# You only schedule the three MASTER notebooks.
# The individual job notebooks (01_ through 07_) are called by the masters.


# =============================================================================
# NOTEBOOK 1: master_polling.py
# Path:     /Workspace/fgs_pipeline/master/master_polling
# Schedule: Every 30 minutes (all day -- the notebook handles its own window)
# Cron:     0 0/30 * * * ?
#
# This is the most important schedule. Missing a run during a flood event
# means missing a potential FGS update. Set a failure alert.
# =============================================================================


# =============================================================================
# NOTEBOOK 2: master_weekly.py
# Path:     /Workspace/fgs_pipeline/master/master_weekly
# Schedule: Weekly, Sunday 02:00 UTC
# Cron:     0 0 2 ? * SUN
#
# Refreshes EA flood areas, MP contacts, and recomputes all intersections.
# Runs overnight to avoid competing with the polling job.
# =============================================================================


# =============================================================================
# NOTEBOOK 3: master_quarterly.py
# Path:     /Workspace/fgs_pipeline/master/master_quarterly
# Schedule: Quarterly -- first day of each quarter at 03:00 UTC
# Cron:     0 0 3 1 1,4,7,10 ?
#           (03:00 UTC on 1 Jan, 1 Apr, 1 Jul, 1 Oct)
#
# Refreshes constituency boundaries and recomputes all intersections.
# Also run manually immediately after a general election or boundary review.
# After an election, also run master_weekly.py to refresh MP details.
# =============================================================================


# =============================================================================
# TIMEOUT REFERENCE
# The timeouts set in the master notebooks are conservative.
# If a notebook times out, either increase the timeout in the master notebook
# or run the failing job notebook manually to diagnose.
#
# master_polling:    total ~15 min  (ingest 5min + intersections 10min)
# master_weekly:     total ~3 hrs   (EA 10min + MPs 30min + lookup 10min +
#                                    intersections 2hrs for full history)
# master_quarterly:  total ~3 hrs   (boundaries 10min + lookup 10min +
#                                    intersections 2hrs for full history)
# =============================================================================


# =============================================================================
# FAILURE ALERTS
# Set up email alerts for each scheduled notebook:
#   1. In the schedule settings, expand "Advanced options"
#   2. Add your email under "Alert on failure"
#   3. Consider also alerting on "Skipped" runs
#
# For master_polling, a failure during a flood event is operationally
# significant. Consider alerting more than one person.
# =============================================================================


# =============================================================================
# MANUAL RUNS
# Any master notebook can be run manually at any time via "Run all".
# Useful for:
#   - Running master_quarterly.py after a general election
#   - Re-running master_weekly.py after a failed scheduled run
#   - Running master_polling.py to test after initial setup
#
# The individual job notebooks can also be run manually for debugging.
# They are safe to run in isolation -- they do not have side effects
# beyond writing to their target Delta tables.
# =============================================================================
