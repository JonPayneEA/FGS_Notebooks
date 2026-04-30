# =============================================================================
# jobs/06_compute_lookup.py
# Compute the static EA flood area / parliamentary constituency lookup table.
#
# What this script does:
#   1. Loads EA flood areas and constituency boundaries from Delta.
#   2. Runs a spatial join using GeoPandas to find all intersections.
#   3. Records the intersection percentage for reference (no minimum threshold).
#   4. Overwrites ea_area_constituency_lookup in full.
#
# Run triggers:
#   - After 03_refresh_ea_flood_areas (weekly)
#   - After 05_refresh_boundaries (quarterly / post-election)
#
# WHY GEOPANDAS ON THE DRIVER:
# GeoPandas loads all geometries into memory on the Spark driver node and
# runs the spatial join there. This is appropriate here because:
#   - EA flood areas: a few thousand polygons
#   - Constituencies: 650 polygons
# These fit easily in memory. Distributed spatial joins add complexity
# without benefit at this scale.
# =============================================================================

import sys
import json

sys.path.insert(0, "/Workspace/fgs_pipeline")
from config import (
    TBL_EA_FWA, TBL_EA_FAA, TBL_CONSTITUENCIES, TBL_EA_CONST_LOOKUP
)
from utils.helpers import get_spark, utc_now

import geopandas as gpd
import pandas as pd
from shapely.geometry import shape
from pyspark.sql import Row


# =============================================================================
# SETUP
# =============================================================================

spark   = get_spark()
now_iso = utc_now().isoformat()


# =============================================================================
# LOAD REFERENCE TABLES FROM DELTA INTO PANDAS
# We collect() to bring the data to the driver for GeoPandas processing.
# This is safe at these data volumes.
# =============================================================================

print("Loading EA flood areas from Delta...")

# Load FWAs and FAAs and combine into one DataFrame with an area_type column.
fwa_pdf = spark.table(TBL_EA_FWA).toPandas()
faa_pdf = spark.table(TBL_EA_FAA).toPandas()
ea_pdf  = pd.concat([fwa_pdf, faa_pdf], ignore_index=True)

print(f"  {len(ea_pdf)} EA flood areas loaded.")

print("Loading constituency boundaries from Delta...")
const_pdf = spark.table(TBL_CONSTITUENCIES).toPandas()
print(f"  {len(const_pdf)} constituencies loaded.")


# =============================================================================
# CONVERT TO GEODATAFRAMES
# The geometry column in Delta holds GeoJSON strings.
# shape() from Shapely parses a GeoJSON dict into a Shapely geometry object.
# GeoPandas needs Shapely geometry objects to run spatial operations.
# =============================================================================

def geojson_str_to_geodataframe(pdf: pd.DataFrame, crs: str = "EPSG:4326") -> gpd.GeoDataFrame:
    """
    Convert a Pandas DataFrame with a GeoJSON geometry string column
    into a GeoPandas GeoDataFrame.

    Args:
        pdf: Pandas DataFrame with a "geometry" column of GeoJSON strings.
        crs: Coordinate reference system. EPSG:4326 is WGS84 (lat/lon),
             which is what the FFC API and EA WFS both use.

    Returns:
        GeoPandas GeoDataFrame with a proper geometry column.
    """
    # Parse each GeoJSON string to a Shapely geometry object.
    # json.loads converts the string to a dict; shape() converts the dict
    # to a Shapely geometry that GeoPandas understands.
    geometries = pdf["geometry"].apply(lambda s: shape(json.loads(s)))

    return gpd.GeoDataFrame(pdf, geometry=geometries, crs=crs)


ea_gdf    = geojson_str_to_geodataframe(ea_pdf)
const_gdf = geojson_str_to_geodataframe(const_pdf)


# =============================================================================
# SPATIAL JOIN
# gpd.sjoin finds all pairs of EA areas and constituencies that intersect.
# predicate="intersects" means any overlap at all qualifies -- no threshold.
# The threshold for fgs_ea_area_intersections (25%) is applied separately
# in 07_compute_intersections.py. This lookup table is unthresholded.
# =============================================================================

print("Running spatial join (EA flood areas x constituencies)...")

joined = gpd.sjoin(
    ea_gdf,
    const_gdf[["constituency_id", "name", "geometry"]],
    how="inner",
    predicate="intersects"
)

print(f"  Spatial join produced {len(joined)} rows.")


# =============================================================================
# COMPUTE INTERSECTION PERCENTAGE
# For each matched pair, calculate what proportion of the EA area is
# covered by the constituency.
#
# We use EPSG:27700 (British National Grid) for area calculations.
# EPSG:4326 coordinates are in degrees, not metres, so area calculations
# in that CRS are inaccurate. Projecting to BNG gives areas in square metres.
# =============================================================================

print("Computing intersection percentages...")

# Project both layers to British National Grid for accurate area calculation.
ea_bng    = ea_gdf.to_crs("EPSG:27700")
const_bng = const_gdf.to_crs("EPSG:27700")

# Rebuild joined in BNG so intersection areas are accurate.
joined_bng = gpd.sjoin(
    ea_bng,
    const_bng[["constituency_id", "name", "geometry"]],
    how="inner",
    predicate="intersects"
)

# Calculate intersection area for each matched pair.
# overlay() returns the actual intersection polygons with their areas.
intersection_bng = gpd.overlay(ea_bng, const_bng, how="intersection")

# Compute intersection percentage: intersection area / EA area.
intersection_bng["ea_area_m2"]           = ea_bng.loc[
    intersection_bng.index, "geometry"
].area.values

intersection_bng["intersection_area_m2"] = intersection_bng.geometry.area
intersection_bng["intersection_pct"]     = (
    intersection_bng["intersection_area_m2"]
    / intersection_bng["ea_area_m2"]
)


# =============================================================================
# BUILD OUTPUT ROWS
# =============================================================================

lookup_rows = []

for _, row in intersection_bng.iterrows():
    lookup_rows.append(Row(
        ea_area_code      = row.get("ea_area_code"),
        ea_area_type      = row.get("ea_area_type"),
        ea_area_name      = row.get("ea_area_name"),
        constituency_id   = row.get("constituency_id"),
        constituency_name = row.get("name"),
        intersection_pct  = float(row.get("intersection_pct", 0.0)),
        last_computed_at  = now_iso
    ))

print(f"Built {len(lookup_rows)} lookup rows.")


# =============================================================================
# WRITE TO DELTA
# Full overwrite -- this table is always recomputed from scratch.
# =============================================================================

lookup_df = spark.createDataFrame(lookup_rows)
(
    lookup_df.write
    .format("delta")
    .mode("overwrite")
    .option("overwriteSchema", "true")
    .saveAsTable(TBL_EA_CONST_LOOKUP)
)
print(f"Written {len(lookup_rows)} rows to {TBL_EA_CONST_LOOKUP}.")

print("Lookup table computation complete.")
dbutils.notebook.exit("success")
