# =============================================================================
## =============================================================================
# config.py
# Central configuration for the FGS pipeline.
# All settings live here. No other script should hard-code values.
# =============================================================================

from dotenv import load_dotenv
import os

# In Databricks notebooks __file__ is not defined.
# Use the Databricks workspace path directly instead.
# Adjust this path to match where your .env file sits.
load_dotenv("/Workspace/Users/jon.payne@environment-agency.gov.uk/FGS_Notebooks/.env")

FFC_API_KEY = os.getenv("FFC_API_KEY")

# -----------------------------------------------------------------------------
# UNITY CATALOG
# Three-part naming: catalog.schema.table
# Substitute your actual catalog and schema names below.
# -----------------------------------------------------------------------------
CATALOG = "prd_dash_lab"    # e.g. "lab" -- ask your DASH admin
SCHEMA  = "flood_forecasting_unrestricted" 

# Fully qualified table names built from the above.
# If you change CATALOG or SCHEMA, everything updates automatically.
TBL_STATEMENTS          = f"{CATALOG}.{SCHEMA}.fgs_statements"
TBL_RISK_POLYGONS       = f"{CATALOG}.{SCHEMA}.fgs_risk_polygons"
TBL_AOC_POLYGONS        = f"{CATALOG}.{SCHEMA}.fgs_aoc_polygons"
TBL_EA_FWA              = f"{CATALOG}.{SCHEMA}.ea_flood_warning_areas"
TBL_EA_FAA              = f"{CATALOG}.{SCHEMA}.ea_flood_alert_areas"
TBL_CONSTITUENCIES      = f"{CATALOG}.{SCHEMA}.parliamentary_constituencies"
TBL_MP_CONTACTS         = f"{CATALOG}.{SCHEMA}.mp_contact_details"
TBL_EA_CONST_LOOKUP     = f"{CATALOG}.{SCHEMA}.ea_area_constituency_lookup"
TBL_FGS_EA_INTERSECT    = f"{CATALOG}.{SCHEMA}.fgs_ea_area_intersections"
TBL_FGS_CONST_INTERSECT = f"{CATALOG}.{SCHEMA}.fgs_constituency_intersections"

# -----------------------------------------------------------------------------
# FFC API
# Base URL and path for the Flood Forecasting Centre API v3.
# -----------------------------------------------------------------------------
FFC_BASE_URL        = "https://api.ffc-environment-agency.fgs.metoffice.gov.uk"
FFC_STATEMENTS_PATH = "/api/public/v3/statements"

# -----------------------------------------------------------------------------
# POLLING WINDOW
# The FGS is issued between 10:00 and 22:00.
# The polling job runs every 30 minutes within this window.
# Times are UTC -- adjust if the EA operates on local time.
# -----------------------------------------------------------------------------
POLL_START_HOUR = 10    # Do not poll before this hour (UTC)
POLL_END_HOUR   = 22    # Do not poll after this hour (UTC)

# -----------------------------------------------------------------------------
# SPATIAL INTERSECTION THRESHOLD
# EA flood areas with less than this proportion of their area covered by an
# FGS polygon are excluded from fgs_ea_area_intersections.
# Parliamentary constituency intersections have no minimum threshold.
# -----------------------------------------------------------------------------
EA_INTERSECTION_MIN_PCT = 0.25   # 25 percent minimum coverage

# -----------------------------------------------------------------------------
# EA FLOOD AREA BULK GEOJSON DOWNLOADS
# These return all features in a single call.
# Update the limit parameter if the total area count exceeds 5000.
# -----------------------------------------------------------------------------
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

# -----------------------------------------------------------------------------
# PARLIAMENT MEMBERS API
# Returns current MPs with constituency linkage. No authentication required.
# -----------------------------------------------------------------------------
PARLIAMENT_API_BASE     = "https://members-api.parliament.uk/api"