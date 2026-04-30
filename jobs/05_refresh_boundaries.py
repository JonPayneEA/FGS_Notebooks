# =============================================================================
# jobs/05_refresh_boundaries.py
# Quarterly refresh of parliamentary constituency boundaries.
#
# What this script does:
#   1. Fetches current constituency boundaries from the ONS Open Geography Portal.
#   2. Overwrites parliamentary_constituencies in full.
#
# Run schedule: quarterly, or immediately after a general election.
# After this job completes, the Workflow triggers 06_compute_lookup.py
# to recompute the EA area / constituency spatial join.
#
# Boundaries change only after a Boundary Commission review or general election.
# Quarterly is conservative -- you could run this annually -- but the job is
# fast and cheap so the frequency is not a concern.
# =============================================================================

import sys
import json
import requests

sys.path.insert(0, "/Workspace/fgs_pipeline")
from config import ONS_CONSTITUENCIES_URL, TBL_CONSTITUENCIES
from utils.helpers import get_spark, utc_now

import mosaic as mos
from pyspark.sql import Row


# =============================================================================
# SETUP
# =============================================================================

spark = get_spark()
mos.enable_mosaic(spark)
now = utc_now()


# =============================================================================
# FETCH BOUNDARIES
# The ONS portal serves constituency boundaries as a GeoJSON FeatureCollection.
# The URL in config.py requests all features in one call.
# These are generalised (BGC) boundaries -- appropriate for spatial joins.
# If you need more precise boundaries, swap to BFE (full extent) in the URL,
# but note the file size increases substantially.
# =============================================================================

print(f"Fetching constituency boundaries from ONS...")
response = requests.get(ONS_CONSTITUENCIES_URL, timeout=120)

if response.status_code != 200:
    raise Exception(f"ONS request failed: {response.status_code} -- {response.text}")

data     = response.json()
features = data.get("features", [])
print(f"Retrieved {len(features)} constituency features.")


# =============================================================================
# PARSE FEATURES
# The ONS GeoJSON properties include the GSS code (PCON24CD) and name (PCON24NM).
# The field names include the year of the boundary release (24 = 2024).
# If you update to a newer boundary release, check the field names have not changed.
# =============================================================================

constituency_rows = []

for feature in features:
    props = feature.get("properties", {})

    constituency_rows.append(Row(
        # GSS code: the stable ONS identifier for the constituency.
        # This is the join key used in mp_contact_details and the lookup table.
        constituency_id   = props.get("PCON24CD"),
        name              = props.get("PCON24NM"),
        # Serialise the geometry back to GeoJSON string for Mosaic to parse.
        geojson_str       = json.dumps(feature.get("geometry")),
        last_refreshed_at = now.isoformat()
    ))

print(f"Parsed {len(constituency_rows)} rows.")


# =============================================================================
# WRITE TO DELTA
# =============================================================================

const_df = spark.createDataFrame(constituency_rows)

const_df = (
    const_df
    .withColumn("geometry", mos.st_geomfromgeojson("geojson_str"))
    .drop("geojson_str")
)

(
    const_df.write
    .format("delta")
    .mode("overwrite")
    .option("overwriteSchema", "true")
    .saveAsTable(TBL_CONSTITUENCIES)
)
print(f"Written {len(constituency_rows)} rows to {TBL_CONSTITUENCIES}.")

print("Constituency boundary refresh complete.")
# Databricks Workflow triggers 06_compute_lookup.py next.
dbutils.notebook.exit("success")
