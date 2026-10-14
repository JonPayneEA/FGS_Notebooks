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

# EA real-time data (jobs 08 to 12)
TBL_EA_RT_BRONZE        = f"{CATALOG}.{SCHEMA}.ea_rt_readings_bronze"
TBL_EA_RT_REGISTER      = f"{CATALOG}.{SCHEMA}.ea_rt_measure_register"
TBL_FLOOD_WARN_CURRENT  = f"{CATALOG}.{SCHEMA}.flood_warnings_current"
TBL_FLOOD_WARN_LOG      = f"{CATALOG}.{SCHEMA}.flood_warnings_log"
TBL_WARNING_CONTACTS    = f"{CATALOG}.{SCHEMA}.flood_warning_constituency_contacts"
TBL_FGS_CONTACTS        = f"{CATALOG}.{SCHEMA}.fgs_constituency_mp_contacts"
TBL_EA_ADMIN_AREAS      = f"{CATALOG}.{SCHEMA}.ea_administrative_areas"

# -----------------------------------------------------------------------------
# FFC API
# Base URL and paths for the Flood Forecasting Centre public API v3.
# V1 and V2 were decommissioned on 01/07/2026. Authentication is an
# X-Api-Key header. Statements list: page_number / page_size (default 50).
# -----------------------------------------------------------------------------
FFC_BASE_URL        = "https://api.ffc-environment-agency.fgs.metoffice.gov.uk"
FFC_STATEMENTS_PATH = "/api/public/v3/statements"
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
# SPATIAL INTERSECTION THRESHOLDS
# EA flood areas with less than this proportion of their area covered by an
# FGS polygon are excluded from fgs_ea_area_intersections.
# Line geometries (coastal polylines) have no area, so the threshold does not
# apply to them: any positive-length overlap is kept and intersection_pct is
# NULL.
# Constituency intersections have no percentage threshold, but overlaps
# smaller than MIN_OVERLAP_AREA_M2 (boundary slivers and touching edges) are
# ignored.
# -----------------------------------------------------------------------------
EA_INTERSECTION_MIN_PCT = 0.25   # 25 percent minimum coverage
MIN_OVERLAP_AREA_M2     = 1.0    # Square metres in EPSG:27700

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
