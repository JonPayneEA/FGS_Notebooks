# =============================================================================
# config.py
# Central configuration for the FGS pipeline.
# All settings live here. No other script should hard-code values.
# =============================================================================

import os

# -----------------------------------------------------------------------------
# SECRETS
# The FFC API key is never stored in the repo. get_ffc_api_key() looks for it in
# this order and uses the first it finds:
#
#   1. A Databricks secret (preferred, if you can create a secret scope):
#        databricks secrets create-scope fgs-pipeline
#        databricks secrets put-secret fgs-pipeline ffc-api-key
#   2. The FFC_API_KEY environment variable.
#   3. A .env file, containing a line  FFC_API_KEY=... , found in the first of:
#        a. the path in the FGS_ENV_FILE environment variable, if set;
#        b. next to this config.py (inside the repo folder);
#        c. the folder ABOVE the repo folder (outside the repo).
#      .env is in .gitignore, so it is never committed.
#
#      Prefer (c). A Git pull, branch switch or reset on the repo folder can
#      remove files inside it, including ignored ones such as .env. A file one
#      level up is not part of the repo, so Git never touches it. Example: if the
#      repo is /Workspace/Users/you/fgs_pipeline, put the file at
#      /Workspace/Users/you/.env
#
# The key is fetched lazily, so notebooks that do not call the FFC API
# (03 to 07, 09 to 14) never need it.
#
# A .env file is plain text: anyone who can open the folder can read it. A
# secret scope is the safer home for the key if you have the permission.
# -----------------------------------------------------------------------------
SECRET_SCOPE = "fgs-pipeline"
SECRET_KEY   = "ffc-api-key"

_REPO_DIR = os.path.dirname(os.path.abspath(__file__))

# Searched in order; the first that exists and holds FFC_API_KEY wins.
ENV_FILES = [
    p for p in (
        os.getenv("FGS_ENV_FILE"),                                          # explicit
        os.path.join(_REPO_DIR, ".env"),                                    # inside the repo
        os.path.join(os.path.dirname(_REPO_DIR), ".env"),                   # outside the repo
    ) if p
]
ENV_FILE = ENV_FILES[0]   # kept for backwards compatibility


def _read_env_file(path: str) -> dict:
    """
    Parse a .env file into a dict. Handles comments, blank lines, an optional
    leading "export ", spaces around "=", and single or double quotes. Returns
    an empty dict if the file does not exist. Nothing is written to os.environ.
    """
    values = {}
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                if line.startswith("export "):
                    line = line[len("export "):].lstrip()
                name, _, value = line.partition("=")
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                values[name.strip()] = value
    except OSError:
        return {}
    return values


def get_ffc_api_key() -> str:
    """Return the FFC API key: Databricks secret, else environment, else .env."""
    try:
        from databricks.sdk.runtime import dbutils
        key = dbutils.secrets.get(scope=SECRET_SCOPE, key=SECRET_KEY)
        if key:
            return key
    except Exception:
        pass

    key = os.getenv("FFC_API_KEY")
    if key:
        return key

    for path in ENV_FILES:
        key = _read_env_file(path).get("FFC_API_KEY")
        if key:
            return key

    raise RuntimeError(
        "FFC API key not found. Provide it as a Databricks secret "
        f"(scope '{SECRET_SCOPE}', key '{SECRET_KEY}'), the FFC_API_KEY "
        "environment variable, or a line FFC_API_KEY=... in a .env file at one of: "
        + ", ".join(ENV_FILES)
    )


# -----------------------------------------------------------------------------
# UNITY CATALOG
# Three-part naming: catalog.schema.table
# -----------------------------------------------------------------------------
CATALOG = "prd_dash_lab"
SCHEMA  = "flood_forecasting_unrestricted"

# Restricted schema, for data that must not sit alongside the unrestricted tables
# (the flood thresholds). Nothing in the FGS pipeline reads from it.
RESTRICTED_SCHEMA = "flood_forecasting_restricted"

# Fully qualified table names built from the above.
# If you change CATALOG or SCHEMA, everything updates automatically.
TBL_STATEMENTS          = f"{CATALOG}.{SCHEMA}.fgs_statements"
TBL_RISK_POLYGONS       = f"{CATALOG}.{SCHEMA}.fgs_risk_polygons"
TBL_AOC_POLYGONS        = f"{CATALOG}.{SCHEMA}.fgs_aoc_polygons"
TBL_RISK_BLOCKS         = f"{CATALOG}.{SCHEMA}.fgs_risk_blocks"
TBL_EA_FWA              = f"{CATALOG}.{SCHEMA}.ea_flood_warning_areas"
TBL_EA_FAA              = f"{CATALOG}.{SCHEMA}.ea_flood_alert_areas"
TBL_CONSTITUENCIES      = f"{CATALOG}.{SCHEMA}.parliamentary_constituencies"
TBL_MP_CONTACTS         = f"{CATALOG}.{SCHEMA}.mp_contact_details"
TBL_EA_CONST_LOOKUP     = f"{CATALOG}.{SCHEMA}.ea_area_constituency_lookup"
TBL_FGS_EA_INTERSECT    = f"{CATALOG}.{SCHEMA}.fgs_ea_area_intersections"
TBL_FGS_CONST_INTERSECT = f"{CATALOG}.{SCHEMA}.fgs_constituency_intersections"
TBL_FGS_INTERSECT_LOG   = f"{CATALOG}.{SCHEMA}.fgs_intersections_log"

# EA real-time data (jobs 08 to 12, and the manual notebooks 15 to 17)
TBL_EA_RT_BRONZE        = f"{CATALOG}.{SCHEMA}.ea_rt_readings_bronze"
TBL_EA_RT_REGISTER      = f"{CATALOG}.{SCHEMA}.ea_rt_measure_register"
TBL_FLOOD_WARN_CURRENT  = f"{CATALOG}.{SCHEMA}.flood_warnings_current"
TBL_FLOOD_WARN_LOG      = f"{CATALOG}.{SCHEMA}.flood_warnings_log"
TBL_WARNING_CONTACTS    = f"{CATALOG}.{SCHEMA}.flood_warning_constituency_contacts"
TBL_WARNING_MESSAGES_HISTORY = f"{CATALOG}.{SCHEMA}.ea_warning_messages_history"   # job 25: the message history file, loaded once
TBL_WARNING_EPISODES    = f"{CATALOG}.{SCHEMA}.ea_warning_episodes"   # job 25: each run in force, any level
TBL_WARNING_PERIODS     = f"{CATALOG}.{SCHEMA}.ea_warning_periods"    # job 25: each run at one level
TBL_FGS_CONTACTS        = f"{CATALOG}.{SCHEMA}.fgs_constituency_mp_contacts"
TBL_EA_ADMIN_AREAS      = f"{CATALOG}.{SCHEMA}.ea_administrative_areas"
TBL_EA_RT_STATIONS      = f"{CATALOG}.{SCHEMA}.ea_rt_stations"
TBL_EA_RT_STATION_MEASURES = f"{CATALOG}.{SCHEMA}.ea_rt_station_measures"
TBL_WISKI_LOOKUP        = f"{CATALOG}.{SCHEMA}.ea_rt_wiski_lookup"          # job 16: now a VIEW over the telemetry mapping (matched rows with a WISKI id)
TBL_TELEMETRY_MAPPING   = f"{CATALOG}.{SCHEMA}.ea_rt_telemetry_mapping"     # job 16: the mapping file, each row matched to a measure
TBL_MEASURES_MAPPED     = f"{CATALOG}.{SCHEMA}.ea_rt_measures_mapped"       # job 16: the register with each measure's gauge name, FFFS id and WISKI id

# Live readings (job 23). The daily archive (job 10) lands each day's readings about
# one to two days late, so this table holds the last few days polled from the EA API
# every 15 to 30 minutes, all sites. A day is released from it once that day's archive
# is in bronze. The two views read through it: the newest reading per measure, and the
# archive plus the live tail as one series.
TBL_EA_RT_LIVE          = f"{CATALOG}.{SCHEMA}.ea_rt_readings_live"
TBL_EA_RT_LATEST        = f"{CATALOG}.{SCHEMA}.ea_rt_latest"
TBL_EA_RT_ALL           = f"{CATALOG}.{SCHEMA}.ea_rt_readings_all"

# Products built on the FGS and warning tables (jobs 18 to 22)
TBL_FGS_CHANGES         = f"{CATALOG}.{SCHEMA}.fgs_constituency_changes"
TBL_FGS_CHANGES_LOG     = f"{CATALOG}.{SCHEMA}.fgs_changes_log"
TBL_CONST_SITUATION     = f"{CATALOG}.{SCHEMA}.constituency_flood_situation"
TBL_MP_BRIEFING_DRAFTS  = f"{CATALOG}.{SCHEMA}.mp_briefing_drafts"
TBL_FGS_EXPOSURE        = f"{CATALOG}.{SCHEMA}.fgs_constituency_exposure"
TBL_WARNING_FGS_COVERAGE = f"{CATALOG}.{SCHEMA}.ea_warning_fgs_coverage"   # job 26: each warning, and whether and when an FGS polygon covered it
TBL_PIPELINE_HEALTH     = f"{CATALOG}.{SCHEMA}.pipeline_health"
TBL_EA_THRESHOLDS       = f"{CATALOG}.{RESTRICTED_SCHEMA}.ea_thresholds_all"
TBL_FW_TIMING           = f"{CATALOG}.{RESTRICTED_SCHEMA}.ea_fw_timing_validation"   # job 27: each RES crossing against the warning in force
TBL_FW_THRESHOLD_LINKS  = f"{CATALOG}.{RESTRICTED_SCHEMA}.ea_fw_threshold_gauge_links"   # job 28: Observed flood warning thresholds linked to gauge measures

# Externally supplied CSVs, read from Unity Catalog volumes by notebooks 16 and 17.
EXTERNAL_VOLUME  = "shared_external_volume"
WISKI_LOOKUP_CSV = f"/Volumes/{CATALOG}/{SCHEMA}/{EXTERNAL_VOLUME}/realTime_WISKI_lookup.csv"   # retired: 16 no longer reads it
WISKI_MAPPING_CSV = f"/Volumes/{CATALOG}/{SCHEMA}/{EXTERNAL_VOLUME}/WISKI_Mapping.csv"          # TelemetryID, FFFS_ID, WISKI_ID and the parameter each row is for
THRESHOLDS_CSV   = f"/Volumes/{CATALOG}/{RESTRICTED_SCHEMA}/{EXTERNAL_VOLUME}/Thresholds/2026_02_01_EA_Thresholds.csv"
# Optional two-column CSV (threshold_unit, measure_unit) naming units that mean the same, read by 28 when a threshold unit is worded differently from the register's.
THRESHOLD_UNIT_ALIASES_CSV = f"/Volumes/{CATALOG}/{RESTRICTED_SCHEMA}/{EXTERNAL_VOLUME}/Thresholds/unit_aliases.csv"
# Mapping rows to leave out (NRW and astronomical tide sources): FFFS_ID and Keep columns, Keep = FALSE leaves a row out.
OUT_OF_SCOPE_CSV = f"/Volumes/{CATALOG}/{SCHEMA}/{EXTERNAL_VOLUME}/Import_Timeseries.csv"

# -----------------------------------------------------------------------------
# EA REAL-TIME LIVE READINGS (job 23)
# One call returns the latest reading for every measure, every site in England.
# -----------------------------------------------------------------------------
EA_RT_LATEST_URL        = "https://environment.data.gov.uk/flood-monitoring/data/readings?latest"
EA_RT_LIVE_PAGE_LIMIT   = 10000   # items per page; the job pages until a short page
EA_RT_LIVE_MIN_MEASURES = 1000    # fewer than this is a bad response, not a quiet day
EA_RT_LIVE_KEEP_DAYS    = 4       # safety net if the archive hand-over (job 10) has not run

# -----------------------------------------------------------------------------
# FFC API
# Base URL and paths for the Flood Forecasting Centre public API v3.
# V1 and V2 were decommissioned on 01/07/2026. Authentication is an
# X-Api-Key header. Statements list: page_number / page_size (default 50).
# -----------------------------------------------------------------------------
FFC_BASE_URL        = "https://api.ffc-environment-agency.fgs.metoffice.gov.uk"
FFC_STATEMENTS_PATH = "/api/public/v3/statements"
FFC_RFG_PATH        = "/api/public/v3/rfg"                 # returns {"rfg_updates": [...]}, newest first (page_number, page_size)
FFC_RFG_LATEST_PATH = "/api/public/v3/rfg/latest"          # one update, or only last_updated and a status when no guidance is current
FFC_LATEST_PATH     = "/api/public/v3/statements/latest"   # returns {"statement": {...}}

# -----------------------------------------------------------------------------
# POLLING WINDOW
# The FGS is issued from 10:30. Padding extra hours either side.
# The ingest notebook exits cleanly outside this window.
# Times are UTC.
# -----------------------------------------------------------------------------
POLL_START_HOUR = 9    # Do not poll before this hour (UTC)
POLL_END_HOUR   = 23   # Do not poll at or after this hour (UTC)

# -----------------------------------------------------------------------------
# FGS GEOMETRY SOURCE
# Which FGS geometries the intersections (07) and the FGS contact table (14)
# are built from:
#
#   "risk_areas" -> fgs_risk_polygons  (the risk area polygons, the default)
#   "aoc"        -> fgs_aoc_polygons   (the Area of Concern map polygons)
#
# Both sets are always ingested and kept. Each AOC polygon carries a
# risk_area_block_id, and fgs_risk_blocks says what that block means (flood
# source, forecast days, impact and likelihood), so an AOC polygon inherits its
# risk from its block. Intersection rows are tagged with geometry_source, so
# results for the two sources sit side by side and switching never deletes the
# other. After changing this value, run 07_compute_intersections once with
# recompute_all = true so the chosen source is fully computed.
# -----------------------------------------------------------------------------
FGS_GEOMETRY_SOURCE = "risk_areas"

if FGS_GEOMETRY_SOURCE not in ("risk_areas", "aoc"):
    raise ValueError(
        f"FGS_GEOMETRY_SOURCE must be 'risk_areas' or 'aoc', not {FGS_GEOMETRY_SOURCE!r}"
    )

# -----------------------------------------------------------------------------
# MP BRIEFING DRAFTS (job 20)
# A draft is written for an MP when the forecast for their constituency reaches
# this risk rank on any of the five days: 1 Very Low, 2 Low, 3 Medium, 4 High.
# 1 means any FGS polygon over the constituency, so a statement that is Very Low
# everywhere still drafts for every constituency with a polygon over it. Raise it to
# 3 to draft only for Medium and above.
# Drafts are never sent. They wait in mp_briefing_drafts for a person to review.
# -----------------------------------------------------------------------------
BRIEFING_MIN_RISK_RANK = 1

if BRIEFING_MIN_RISK_RANK not in (1, 2, 3, 4):
    raise ValueError(f"BRIEFING_MIN_RISK_RANK must be 1 to 4, not {BRIEFING_MIN_RISK_RANK!r}")

# -----------------------------------------------------------------------------
# PIPELINE HEALTH (job 22)
# Age limits in hours. Beyond WARN the check reads "warn"; beyond FAIL it reads
# "fail". The FGS is issued once a day, so its limits are wide.
# -----------------------------------------------------------------------------
HEALTH_FGS_WARN_HOURS      = 30
HEALTH_FGS_FAIL_HOURS      = 54
HEALTH_WARNINGS_WARN_HOURS = 3
HEALTH_WARNINGS_FAIL_HOURS = 12
HEALTH_CONTACTS_WARN_HOURS = 3
HEALTH_CONTACTS_FAIL_HOURS = 12
HEALTH_MP_WARN_DAYS        = 10     # MP details refresh weekly
HEALTH_MP_FAIL_DAYS        = 21

# Live EA real-time readings (job 23): age in hours of the newest reading ingested. This
# check WARNS at most and never fails: a stale feed is usually the EA API having a bad
# spell, not the pipeline failing, and it must not fail the polling run that carries the
# FGS and warnings alerts.
HEALTH_LIVE_WARN_HOURS     = 1
# the exposure products (jobs 29 to 35) are rebuilt by hand when the hazard maps, the address points or the areas change, so they are old
# by design between rebuilds. The check warns, never fails, because a stale exposure table must not fail the polling run.
HEALTH_EXPOSURE_WARN_DAYS  = 180
HEALTH_EXPOSURE_PEOPLE_MIN = 55_000_000      # England's Census 2021 population is 56.49 million; a total far outside this means a broken join
HEALTH_EXPOSURE_PEOPLE_MAX = 58_000_000

# -----------------------------------------------------------------------------
# SPATIAL INTERSECTION THRESHOLDS
# EA flood areas with less than this proportion of their area covered by an
# FGS polygon are excluded from fgs_ea_area_intersections.
# Line geometries (coastal polylines) have no area, so the threshold does not
# apply to them: any overlap with the widened line (see COASTAL_LINE_BUFFER_M
# below) is kept and intersection_pct is NULL.
# Constituency intersections have no percentage threshold, but overlaps
# smaller than MIN_OVERLAP_AREA_M2 (boundary slivers and touching edges) are
# ignored.
# -----------------------------------------------------------------------------
EA_INTERSECTION_MIN_PCT = 0.25   # 25 percent minimum coverage
MIN_OVERLAP_AREA_M2     = 1.0    # Square metres in EPSG:27700

# Coastal FGS geometries are lines along the coast. A constituency stops at the coast, and
# a coastal flood warning or alert area is a polygon beside it, so a line on or just off
# that edge often crosses neither. Each line is widened by this many metres on each side
# before the overlap test, for BOTH the constituency and the EA flood area intersections.
# The row keeps its line geometry_type and a NULL intersection_pct (the EA coverage
# threshold applies to polygons only). 0 turns it off. Polygons are never widened.
# After changing it, run 07_compute_intersections with coastal_only = true (only the
# statements that hold a coastal line are recomputed). New statements use it automatically.
COASTAL_LINE_BUFFER_M   = 1000
if COASTAL_LINE_BUFFER_M < 0:
    raise ValueError(f"COASTAL_LINE_BUFFER_M must be 0 or more, not {COASTAL_LINE_BUFFER_M!r}")

# Even widened, a coastal line may not reach every flood warning area inside a flood alert
# area it does reach. Each flood warning area names its parent alert area, so when True a
# coastal alert area under the forecast also raises the coastal warning areas whose parent
# it is (fgs_ea_area_intersections.link_type = 'parent_alert', NULL intersection_pct). Only
# coastal-source rows are linked. After changing it, run 07 with coastal_only = true.
COASTAL_LINK_WARNINGS_TO_ALERTS = True

# flood_warning_constituency_contacts: a warning or alert is linked to a
# constituency when at least this share of the warning AREA lies inside it
# (the intersection_pct in ea_area_constituency_lookup). 0.01 means 1 percent:
# it stops an MP being linked to a warning that only clips the edge of their
# constituency. Set 0.0 to keep every real overlap, matching
# fgs_constituency_intersections. share_of_area_in_constituency is carried in
# the table, so you can also filter harder at query time.
WARNING_CONTACT_MIN_PCT = 0.01

# fgs_constituency_mp_contacts: the same idea for the forecast (FGS). A polygon
# is linked to a constituency when it covers at least this share of the
# CONSTITUENCY. Coastal polylines have no area, so they are always kept.
FGS_CONTACT_MIN_PCT = 0.01

# -----------------------------------------------------------------------------
# EA FLOOD AREA BULK GEOJSON DOWNLOADS
# These return all features in a single call.
# Update the limit parameter (and EA_GEOJSON_LIMIT) if the total area count
# approaches 5000. The refresh job fails rather than load a truncated layer.
# -----------------------------------------------------------------------------
EA_GEOJSON_LIMIT = 5000
EA_FWA_GEOJSON_URL = (
    "https://environment.data.gov.uk/geoservices/datasets/"
    "87e5d78f-d465-11e4-9343-f0def148f590/ogc/features/v1/collections/"
    "Flood_Warning_Areas/items?f=application%2Fgeo%2Bjson&limit=5000"
)
EA_FAA_GEOJSON_URL = (
    "https://environment.data.gov.uk/geoservices/datasets/"
    "864c72de-d465-11e4-855f-f0def148f590/ogc/features/v1/collections/"
    "Flood_Alert_Areas/items?f=application%2Fgeo%2Bjson&limit=5000"
)

# -----------------------------------------------------------------------------
# PARLIAMENTARY BOUNDARIES
# ONS constituency boundaries -- 2024 release, generalised (BGC).
# Update this URL after any boundary review.
# -----------------------------------------------------------------------------
ONS_CONSTITUENCIES_URL = (
    "https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/"
    "Westminster_Parliamentary_Constituencies_July_2024_Boundaries_UK_BGC/"
    "FeatureServer/0/query?where=1%3D1&outFields=*&f=geojson"
)
MIN_CONSTITUENCIES = 600   # A refresh returning fewer than this is rejected

# -----------------------------------------------------------------------------
# PARLIAMENT MEMBERS API
# Returns current MPs with constituency linkage. No authentication required.
# -----------------------------------------------------------------------------
PARLIAMENT_API_BASE = "https://members-api.parliament.uk/api"
MIN_MPS             = 600    # A refresh returning fewer than this is rejected


# -----------------------------------------------------------------------------
# EXPOSURE AND RISK GRID (jobs 29 onwards; plan in docs/EXPOSURE_AND_RISK_GRID_PLAN.md)
# -----------------------------------------------------------------------------
TBL_CENSUS_OA = f"{CATALOG}.{SCHEMA}.census2021_oa"          # job 29: Census 2021 by output area (England): population, households, people aged 65 and over
TBL_IMD_LSOA  = f"{CATALOG}.{SCHEMA}.imd2025_lsoa"          # job 29: English Indices of Deprivation 2025 by LSOA: score, rank, decile
TBL_HAZARD_CELLS = f"{CATALOG}.{SCHEMA}.hazard_cells_100m"  # job 30: per 100 m cell and band, the share of the cell in that band; source is rofrs, rofsw or flood_zones
HAZARD_RES_M = 10          # the raster resolution the flood polygons are painted at (point lookups are within 7 m of a polygon edge)
HAZARD_RASTER_DIR = f"/Volumes/{CATALOG}/{SCHEMA}/{EXTERNAL_VOLUME}/hazard_rasters"   # job 30: one .npz per source and 50 km tile; job 33 samples them at address points
TBL_ADDRESS_POINTS = f"{CATALOG}.{RESTRICTED_SCHEMA}.exposure_address_points"   # job 33: one row per England UPRN (restricted schema): Census shares, deprivation, flood band ranks
TBL_CRITICAL_SITES = f"{CATALOG}.{RESTRICTED_SCHEMA}.critical_sites"                  # job 31 (not yet built): one row per site; job 34 counts it per cell if it exists
TBL_EXPOSURE_GRID  = f"{CATALOG}.{RESTRICTED_SCHEMA}.exposure_grid_100m"              # job 34: one row per 100 m cell with people, exposure, band shares, critical sites and the score
TBL_AREA_EXPOSURE       = f"{CATALOG}.{RESTRICTED_SCHEMA}.ea_area_exposure"             # job 35: one row per flood warning or alert area: gross, unique and split people by band, deprivation, critical sites
TBL_AREA_OVERLAPS       = f"{CATALOG}.{RESTRICTED_SCHEMA}.ea_area_overlaps"             # job 35: each pair of areas of one kind that share addresses, with what they share
TBL_AREA_CONST_EXPOSURE = f"{CATALOG}.{RESTRICTED_SCHEMA}.ea_area_constituency_exposure" # job 35: each area split by constituency (addresses, people, people in High or Medium)
TBL_AREA_GEOGRAPHIES    = f"{CATALOG}.{RESTRICTED_SCHEMA}.ea_area_geographies"          # job 35: each warning or alert area split by district, county, ward, constituency, police force and region
TBL_CONST_SITES         = f"{CATALOG}.{RESTRICTED_SCHEMA}.constituency_sites"            # job 35: each critical site inside each England constituency, with its rivers and sea band
TBL_JOB35_CHECKPOINT   = f"{CATALOG}.{RESTRICTED_SCHEMA}.ea_area_exposure_tile_checkpoint"  # job 35: the per-tile sums, saved so a restart does not repeat the long tile step (one row, overwritten)
TBL_AREA_SITES          = f"{CATALOG}.{RESTRICTED_SCHEMA}.ea_area_sites"                # job 35: each critical site inside each warning or alert area, with its rivers and sea band
TBL_AREA_COVERAGE       = f"{CATALOG}.{RESTRICTED_SCHEMA}.ea_area_coverage"             # job 35: distinct people covered by any warning (or alert) area, against all, so the gap is visible
TBL_CONST_EXPOSURE      = f"{CATALOG}.{RESTRICTED_SCHEMA}.constituency_flood_exposure"  # job 35: per constituency, people by band and how many are in a warning or alert area
TBL_GEOGRAPHY_NAMES    = f"{CATALOG}.{SCHEMA}.responder_geography_names"                 # job 32: geography, code, name, source (open data: names and codes only)
TBL_OA_FIRE            = f"{CATALOG}.{SCHEMA}.oa_fire_authority"                         # job 32: output area to fire and rescue authority (open data: codes only)
TBL_DISTRICT_LOOKUP     = f"{CATALOG}.{SCHEMA}.district_responder_lookup"                 # job 32: district to combined authority (and fire, if supplied) (open data: codes only)
TBL_GEOGRAPHY_EXPOSURE = f"{CATALOG}.{RESTRICTED_SCHEMA}.geography_flood_exposure"      # job 32: people by rivers and sea band, Flood Zone and critical sites, for each local authority, police force and so on
GRID_CELL_M = 100          # the cell size of the British National Grid layer (see utils/grid100.py; changing it changes every key)
# The composite score per cell: hazard (worse of the rivers and sea and the surface water band) times a weighted mix of these three.
# The weights are normalised, so only their proportions matter. The layers are always stored; the score is never the only output.
RISK_SCORE_WEIGHTS = {"pop": 0.4, "vulnerability": 0.3, "critical": 0.3}
RISK_SCORE_INCLUDE_SURFACE = False   # flood warning areas are for rivers, the sea and tidal waters: surface water is shown separately and left out of the score unless this is True
RISK_SCORE_CRITICAL_CAP = 3   # critical sites in a cell beyond this add nothing more to the score

# ---- Rapid Flood Guidance (jobs 36 and 37) ----------------------------------------------------------------------------------
TBL_RFG_UPDATES       = f"{CATALOG}.{SCHEMA}.rfg_updates"                                  # job 36: one row per RFG update, and one per "no guidance" state (public)
TBL_RFG_POLYGONS      = f"{CATALOG}.{SCHEMA}.rfg_polygons"                                 # job 36: one row per polygon per update, GeoJSON, with its latest text and validity (public)
TBL_RFG_EXPOSURE      = f"{CATALOG}.{RESTRICTED_SCHEMA}.rfg_polygon_exposure"              # job 37: people, households, bands and vulnerability under each polygon (written last: its rows mean the update is done)
TBL_RFG_CONSTITUENCIES = f"{CATALOG}.{RESTRICTED_SCHEMA}.rfg_polygon_constituencies"       # job 37: each polygon split by constituency
TBL_RFG_AREAS         = f"{CATALOG}.{RESTRICTED_SCHEMA}.rfg_polygon_areas"                 # job 37: the people of each polygon in each warning or alert area
TBL_RFG_SITES         = f"{CATALOG}.{RESTRICTED_SCHEMA}.rfg_polygon_sites"                 # job 37: the critical sites inside each polygon

# ---- Economics (job 38) -----------------------------------------------------------------------------------------------------
TBL_ECON_ASSUMPTIONS = f"{CATALOG}.{SCHEMA}.economics_assumptions"   # job 38: the probability per band and the damage per household (low, central, high) behind the indicative expected annual damage; edited by hand, never overwritten by the job
