# =============================================================================
# jobs/03_refresh_ea_flood_areas.py
# Weekly refresh of EA Flood Warning Areas and Flood Alert Areas.
#
# What this script does:
#   1. Fetches current FWA and FAA boundaries from the EA WFS endpoint.
#   2. Stores geometries as GeoJSON strings in the geometry column.
#   3. Overwrites ea_flood_warning_areas and ea_flood_alert_areas in full.
#
# Run schedule: weekly, Sunday 02:00 UTC.
# No spatial library is needed here -- geometries are stored as GeoJSON strings
# exactly as the WFS serves them. GeoPandas is used only in the intersection job.
# =============================================================================

import sys
import json
import requests

sys.path.insert(0, "/Workspace/fgs_pipeline")
from config import (
    EA_WFS_BASE_URL, EA_FWA_TYPENAME, EA_FAA_TYPENAME,
    TBL_EA_FWA, TBL_EA_FAA
)
from utils.helpers import get_spark, utc_now

from pyspark.sql import Row


# =============================================================================
# SETUP
# =============================================================================

spark   = get_spark()
now_iso = utc_now().isoformat()


# =============================================================================
# FETCH FROM WFS
# The EA WFS serves GeoJSON FeatureCollections.
# We request all features in a single call -- these datasets are not large.
# =============================================================================

def fetch_ea_wfs(typename: str) -> list:
    """
    Fetch all features for a WFS layer from the EA open data service.

    Args:
        typename: WFS layer name (from config.py).

    Returns:
        List of GeoJSON Feature dicts.
    """
    url = f"{EA_WFS_BASE_URL}&typeName={typename}"
    print(f"Fetching WFS layer: {typename}")

    response = requests.get(url, timeout=60)
    if response.status_code != 200:
        raise Exception(
            f"WFS request failed: {response.status_code} -- {response.text}"
        )

    features = response.json().get("features", [])
    print(f"  Retrieved {len(features)} features.")
    return features


def features_to_rows(features: list, area_type: str, now_iso: str) -> list:
    """
    Convert WFS GeoJSON Features to Spark Rows for Delta storage.

    The geometry is stored as a GeoJSON string -- the same format the WFS
    serves it in, and the same format Leaflet expects for map rendering.

    The exact WFS property names depend on the EA layer schema.
    Check the GetCapabilities response if these names do not match:
    {EA_WFS_BASE_URL}&request=GetCapabilities

    Args:
        features:  List of GeoJSON Feature dicts from the WFS.
        area_type: "flood_warning" or "flood_alert".
        now_iso:   Current UTC timestamp string.

    Returns:
        List of Spark Rows.
    """
    rows = []
    for feature in features:
        props = feature.get("properties", {})

        rows.append(Row(
            # EA area code -- unique identifier for each flood area.
            # Common field names for EA flood area layers are shown below.
            # Adjust if the WFS returns different property names.
            ea_area_code      = props.get("FWS_TACODE") or props.get("fws_tacode"),
            ea_area_name      = props.get("DESCRIP")    or props.get("descrip"),
            river_basin       = props.get("QDIAL")      or props.get("qdial"),
            ea_area_type      = area_type,
            # Store geometry as GeoJSON string.
            # json.dumps serialises the geometry dict back to a string.
            geometry          = json.dumps(feature.get("geometry")),
            last_refreshed_at = now_iso
        ))
    return rows


# =============================================================================
# FETCH, PARSE, AND WRITE
# Both FWA and FAA follow the same pattern.
# =============================================================================

for typename, area_type, table_name in [
    (EA_FWA_TYPENAME, "flood_warning", TBL_EA_FWA),
    (EA_FAA_TYPENAME, "flood_alert",   TBL_EA_FAA),
]:
    features = fetch_ea_wfs(typename)
    rows     = features_to_rows(features, area_type, now_iso)

    df = spark.createDataFrame(rows)

    # Overwrite in full each week. These are reference boundaries, not events.
    # Historical intersection records in fgs_ea_area_intersections are preserved.
    (
        df.write
        .format("delta")
        .mode("overwrite")
        .option("overwriteSchema", "true")
        .saveAsTable(table_name)
    )
    print(f"Written {len(rows)} rows to {table_name}.")

print("EA flood area refresh complete.")
dbutils.notebook.exit("success")
