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

# You only schedule the four MASTER notebooks (polling, daily, weekly, quarterly).
# The individual job notebooks (01_ through 12_) are called by the masters,
# by relative path, so the repo can live in any workspace or Git folder.
#
# If you DO have Workflows permissions you can deploy workflows/databricks_workflows.yml
# instead (see databricks.yml). Use one approach or the other, never both.
#
# FIRST-TIME RUN ORDER is in README.md. In particular, run
# 07_compute_intersections once by hand with recompute_all = true BEFORE
# scheduling master_polling, so the polling run does not have to process the
# whole history inside its timeout.


# =============================================================================
# NOTEBOOK 1: master_polling.py
# Path:     master/Master_polling
# Schedule: Every 15 minutes (all day -- the ingest job applies its own
#           UTC window from config.py)
# Cron:     0 0/15 * * * ?
#
# Each step is isolated: a failure in the FGS ingest does not stop the flood
# warnings ingest. The run still fails at the end so the alert fires.
# Two steps rebuild contact tables: fgs_constituency_mp_contacts (the latest FGS
# forecast, proactive) after the intersections, and flood_warning_constituency_contacts
# (current EA warnings and alerts, reactive) at the end.
#
# This is the most important schedule. Missing a run during a flood event
# means missing a potential FGS update. Set a failure alert.
# =============================================================================

# STEP 0 OF THIS RUN: LIVE READINGS. It fetches the latest EA real-time reading for
# every measure (every site) into ea_rt_readings_live, first and with a short
# timeout, so a slow FGS step cannot delay it. The daily archive lands one to two
# days late, so this table is the live tail until then; job 10 releases each day from
# it once that day is archived.
#
# CADENCE. Every 15 minutes, because the EA real-time API and the EA flood warnings
# API both refresh every 15 minutes, so polling less often misses state in between.
# Every step is idempotent and exits quickly when there is nothing new. The one limit is
# run length: only one run happens at a time, so a run that takes over 15 minutes
# causes the next to be skipped, live readings included. Check the run durations in
# Databricks before going finer, and slacken to 30 minutes if they approach 15.
#
# STEP 0 IS NOT FATAL. A live readings failure is printed and shown in the run summary
# but does not fail the run, so a bad spell at the EA cannot fire the alert that
# protects the FGS and flood warnings. The ea_live_readings_age check in step 9 warns
# (never fails) if the newest live reading is over an hour old.
#
# To run live readings on their own schedule as well, schedule
# jobs/23_ea_rt_live_readings directly. It skips itself if the table was filled in the
# last 10 minutes, so it cannot double-call the API.
# =============================================================================


# =============================================================================
# NOTEBOOK 2: master_weekly.py
# Path:     master/Master_weekly
# Schedule: Weekly, Sunday 02:00 UTC
# Cron:     0 0 2 ? * SUN
#
# Refreshes EA flood areas and MP contacts, then recomputes the EA area /
# constituency lookup. It does NOT recompute historical intersections: each
# statement keeps the intersections computed when it was issued, so the tables
# are an archive of what was recommended at the time.
# Runs overnight to avoid competing with the polling job.
# =============================================================================


# =============================================================================
# NOTEBOOK 3: master_quarterly.py
# Path:     master/Master_quarterly
# Schedule: Quarterly -- first day of each quarter at 03:00 UTC
# Cron:     0 0 3 1 1,4,7,10 ?
#           (03:00 UTC on 1 Jan, 1 Apr, 1 Jul, 1 Oct)
#
# Refreshes constituency boundaries and recomputes the EA area / constituency
# lookup. Statements already issued keep the intersections computed against the
# boundaries of their time; new statements use the new boundaries.
# Also run manually immediately after a general election or boundary review.
# After an election, also run master_weekly.py to refresh MP details.
# =============================================================================


# =============================================================================
# NOTEBOOK 4: master_daily.py
# Path:     master/Master_daily
# Schedule: Daily at 23:00 UTC
# Cron:     0 0 23 * * ?
#
# Lands yesterday's EA real-time archive file. The file for a day is generated
# at 22:00 the following day, so do not schedule this earlier. If the file is
# not there the run FAILS (exit "no_file"): re-run it later.
# =============================================================================


# =============================================================================
# TIMEOUT REFERENCE
# The timeouts set in the master notebooks are conservative.
# If a notebook times out, either increase the timeout in the master notebook
# or run the failing job notebook manually to diagnose.
#
# master_polling:    total ~30 min  (ingest 5min + intersections 10min + FGS contacts 5min +
#                                    warnings 5min + warning contacts 5min)
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
