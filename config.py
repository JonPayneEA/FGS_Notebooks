# =============================================================================
# config.py
# Central configuration for the FGS pipeline.
# All settings live here. No other script should hard-code values.
# =============================================================================

# -----------------------------------------------------------------------------
# DATABRICKS SECRET SCOPE
# Before running any job, create your secret scope and store the API key.
# Run these commands once in the Databricks CLI on your local machine:
#
#   databricks secrets create-scope fgs-pipeline
#   databricks secrets put-secret fgs-pipeline ffc-api-key
#       (you will be prompted to paste the key value)
#
# Then substitute the scope and key names below if you used different names.
# -----------------------------------------------------------------------------
SECRET_SCOPE = "xxxx"       # The name of the secret scope you created
SECRET_KEY = "xxx"          # The name of the secret within that scope

# -----------------------------------------------------------------------------
# UNITY CATALOG
# Three-part naming: catalog.schema.table
# Substitute your actual catalog and schema names below.
# -----------------------------------------------------------------------------
#CATALOG = "your_catalog"            # e.g. "ea_flood" -- ask your DASH admin
#SCHEMA  = "fgs"                     # Schema (database) within that catalog

CATALOG = "/Workspace/Users/jon.payne@environment-agency.gov.uk/FGS_Notebooks/Data/"            # e.g. "ea_flood" -- ask your DASH admin
SCHEMA  = "fgs_dev"                     # Schema (database) within that catalog
spark.sql("CREATE SCHEMA IF NOT EXISTS /Workspace/Users/jon.payne@environment-agency.gov.uk/FGS_Notebooks/Data.fgs_dev")
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
# Base URL for the Flood Forecasting Centre API v3.
# The statements endpoint returns the current and recent FGS records.
# -----------------------------------------------------------------------------
FFC_BASE_URL        = "https://api.foursources.metoffice.gov.uk"
FFC_STATEMENTS_PATH = "/v3/statements"   # Adjust if the API path differs

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
# EXTERNAL DATA SOURCES
# EA flood areas are served via the EA's open data WFS.
# Parliamentary boundaries are published by the ONS/Boundary Commission.
# The Parliament API serves current MP details.
# These URLs were correct as of April 2026 -- verify if jobs start failing.
# -----------------------------------------------------------------------------
EA_WFS_BASE_URL = (
    "https://environment.data.gov.uk/spatialdata/flood-map-for-planning/"
    "wfs?service=WFS&version=2.0.0&request=GetFeature"
    "&outputFormat=application/json"
)
EA_FWA_TYPENAME = "EA.FloodMapForPlanning:FloodAlertAndWarningAreas_FWA"
EA_FAA_TYPENAME = "EA.FloodMapForPlanning:FloodAlertAndWarningAreas_FAA"

# ONS constituency boundaries -- GeoJSON from the Open Geography Portal.
# This URL points to the 2024 parliamentary constituency boundaries.
# Update the URL after any boundary review.
ONS_CONSTITUENCIES_URL = (
    "https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/"
    "Westminster_Parliamentary_Constituencies_July_2024_Boundaries_UK_BGC/"
    "FeatureServer/0/query?where=1%3D1&outFields=*&f=geojson"
)

# Parliament Members API -- returns current MPs with constituency linkage.
PARLIAMENT_API_BASE  = "https://members-api.parliament.uk/api"
PARLIAMENT_MEMBERS_PATH = "/Members/Search?House=Commons&IsCurrentMember=true&take=650"
