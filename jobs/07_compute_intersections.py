# =============================================================================
# jobs/07_compute_intersections.py
# Compute FGS polygon intersections against EA flood areas and constituencies.
#
# What this script does:
#   1. Identifies FGS statements not yet intersected (or all, if recompute_all).
#   2. For each new statement:
#        a. Intersects risk polygons against EA flood areas.
#           Retains only pairs where the EA area is >= 25% covered.
#           Appends to fgs_ea_area_intersections.
#        b. Intersects risk polygons against constituencies.
#           Retains all intersections regardless of size.
#           Appends to fgs_constituency_intersections.
#
# Run triggers:
#   - After 01_ingest_fgs.py (up to 3x daily)
#   - After 06_compute_lookup.py (pass recompute_all=true to reprocess history)
#
# WHY GEOPANDAS ON THE DRIVER:
# FGS risk polygons per statement: typically tens to low hundreds.
# EA flood areas: a few thousand.
# Constituencies: 650.
# All fit comfortably in driver memory. GeoPandas spatial joins are fast
# at this scale -- no distributed spatial processing needed.
# =============================================================================

import sys
import json

sys.path.insert(0, "/Workspace/fgs_pipeline")
from config import (
    EA_INTERSECTION_MIN_PCT,
    TBL_RISK_POLYGONS, TBL_EA_FWA, TBL_EA_FAA,
    TBL_CONSTITUENCIES, TBL_FGS_EA_INTERSECT, TBL_FGS_CONST_INTERSECT
)
from utils.helpers import get_spark, utc_now, table_exists

import geopandas as gpd
import pandas as pd
from shapely.geometry import shape
from pyspark.sql import Row
from pyspark.sql.functions import col


# =============================================================================
# SETUP
# =============================================================================

spark   = get_spark()
now_iso = utc_now().isoformat()

# Check for the recompute_all parameter set by Databricks Workflows.
# When True, reprocesses every statement rather than just new ones.
# Used after a boundary or EA area refresh to update historical intersections.
try:
    recompute_all = dbutils.widgets.get("recompute_all").lower() == "true"
except Exception:
    recompute_all = False

print(f"recompute_all = {recompute_all}")


# =============================================================================
# IDENTIFY STATEMENTS TO PROCESS
# =============================================================================

risk_df = spark.table(TBL_RISK_POLYGONS)

if recompute_all or not table_exists(spark, TBL_FGS_EA_INTERSECT):
    statements_to_process = [
        r["statement_id"]
        for r in risk_df.select("statement_id").distinct().collect()
    ]
    print(f"Processing all {len(statements_to_process)} statements.")
else:
    already_done = (
        spark.table(TBL_FGS_EA_INTERSECT)
        .select("statement_id").distinct()
    )
    statements_to_process = [
        r["statement_id"]
        for r in risk_df.select("statement_id").distinct()
        .subtract(already_done)
        .collect()
    ]
    print(f"{len(statements_to_process)} new statements to process.")

if not statements_to_process:
    print("Nothing to process. Exiting.")
    dbutils.notebook.exit("no_work")


# =============================================================================
# LOAD REFERENCE GEOMETRIES ONCE
# EA areas and constituencies are loaded once outside the statement loop.
# Loading them per statement would be slow and wasteful.
# =============================================================================

print("Loading reference geometries...")

def to_geodataframe(spark_table: str, crs: str = "EPSG:4326") -> gpd.GeoDataFrame:
    """
    Load a Delta table with a GeoJSON geometry column into a GeoPandas GeoDataFrame.

    Args:
        spark_table: Fully qualified Delta table name.
        crs:         Coordinate reference system for the GeoDataFrame.

    Returns:
        GeoPandas GeoDataFrame.
    """
    pdf = spark.table(spark_table).toPandas()
    # Parse GeoJSON strings to Shapely geometry objects.
    geometries = pdf["geometry"].apply(lambda s: shape(json.loads(s)))
    return gpd.GeoDataFrame(pdf, geometry=geometries, crs=crs)


fwa_gdf   = to_geodataframe(TBL_EA_FWA)
faa_gdf   = to_geodataframe(TBL_EA_FAA)
# Combine FWAs and FAAs into a single GeoDataFrame for one join pass.
ea_gdf    = pd.concat([fwa_gdf, faa_gdf], ignore_index=True)
ea_gdf    = gpd.GeoDataFrame(ea_gdf, geometry="geometry", crs="EPSG:4326")
const_gdf = to_geodataframe(TBL_CONSTITUENCIES)

# Project to British National Grid for accurate area calculations.
# EPSG:27700 measures in metres, which is needed for the 25% threshold.
ea_bng    = ea_gdf.to_crs("EPSG:27700")
const_bng = const_gdf.to_crs("EPSG:27700")

print(f"  {len(ea_gdf)} EA flood areas, {len(const_gdf)} constituencies loaded.")


# =============================================================================
# PROCESS EACH STATEMENT
# =============================================================================

for statement_id in statements_to_process:
    print(f"Processing statement_id: {statement_id}")

    # Load risk polygons for this statement only.
    stmt_pdf = (
        risk_df
        .filter(col("statement_id") == statement_id)
        .toPandas()
    )

    # Parse risk polygon geometries.
    stmt_geometries = stmt_pdf["geometry"].apply(lambda s: shape(json.loads(s)))
    stmt_gdf        = gpd.GeoDataFrame(stmt_pdf, geometry=stmt_geometries, crs="EPSG:4326")
    stmt_bng        = stmt_gdf.to_crs("EPSG:27700")

    # -------------------------------------------------------------------------
    # EA FLOOD AREA INTERSECTIONS
    # Find all EA areas that intersect any FGS polygon.
    # Then compute intersection percentage and apply the 25% threshold.
    # -------------------------------------------------------------------------

    # Initial intersection join -- finds candidate pairs.
    ea_joined = gpd.sjoin(
        ea_bng,
        stmt_bng[["poly_id", "day_index", "forecast_date",
                  "source", "risk_level_min", "risk_level_max", "geometry"]],
        how="inner",
        predicate="intersects"
    )

    if len(ea_joined) > 0:
        # Compute the actual intersection geometry and its area.
        ea_overlay = gpd.overlay(ea_bng, stmt_bng, how="intersection")

        # Intersection percentage: intersection area / EA area.
        # The denominator is the EA area -- we are asking how much of the
        # flood area is covered by the FGS polygon, not the other way around.
        ea_overlay["ea_area_m2"]           = ea_bng.loc[
            ea_overlay.index, "geometry"
        ].area.values
        ea_overlay["intersection_area_m2"] = ea_overlay.geometry.area
        ea_overlay["intersection_pct"]     = (
            ea_overlay["intersection_area_m2"] / ea_overlay["ea_area_m2"]
        )

        # Apply the 25% minimum coverage threshold.
        ea_filtered = ea_overlay[
            ea_overlay["intersection_pct"] >= EA_INTERSECTION_MIN_PCT
        ]

        ea_rows = [
            Row(
                statement_id   = int(statement_id),
                issued_at      = str(row.get("issued_at", "")),
                poly_id        = int(row.get("poly_id", 0)),
                day_index      = int(row.get("day_index", 0)),
                forecast_date  = str(row.get("forecast_date", "")),
                ea_area_code   = str(row.get("ea_area_code", "")),
                ea_area_type   = str(row.get("ea_area_type", "")),
                ea_area_name   = str(row.get("ea_area_name", "")),
                source         = str(row.get("source", "")),
                risk_level_min = int(row.get("risk_level_min", 0)),
                risk_level_max = int(row.get("risk_level_max", 0)),
                intersection_pct = float(row.get("intersection_pct", 0.0)),
                computed_at    = now_iso
            )
            for _, row in ea_filtered.iterrows()
        ]

        if ea_rows:
            spark.createDataFrame(ea_rows).write \
                .format("delta").mode("append") \
                .option("mergeSchema", "true") \
                .saveAsTable(TBL_FGS_EA_INTERSECT)
            print(f"  Written {len(ea_rows)} EA intersection rows.")
        else:
            print(f"  No EA intersections met the {EA_INTERSECTION_MIN_PCT*100:.0f}% threshold.")
    else:
        print("  No EA area intersections found for this statement.")

    # -------------------------------------------------------------------------
    # CONSTITUENCY INTERSECTIONS
    # Any overlap qualifies -- no minimum threshold.
    # Intersection percentage stored for reference only.
    # -------------------------------------------------------------------------

    const_overlay = gpd.overlay(const_bng, stmt_bng, how="intersection")

    if len(const_overlay) > 0:
        const_overlay["const_area_m2"]         = const_bng.loc[
            const_overlay.index, "geometry"
        ].area.values
        const_overlay["intersection_area_m2"]  = const_overlay.geometry.area
        const_overlay["intersection_pct"]      = (
            const_overlay["intersection_area_m2"] / const_overlay["const_area_m2"]
        )

        const_rows = [
            Row(
                statement_id     = int(statement_id),
                issued_at        = str(row.get("issued_at", "")),
                poly_id          = int(row.get("poly_id", 0)),
                day_index        = int(row.get("day_index", 0)),
                forecast_date    = str(row.get("forecast_date", "")),
                constituency_id  = str(row.get("constituency_id", "")),
                constituency_name= str(row.get("name", "")),
                source           = str(row.get("source", "")),
                risk_level_min   = int(row.get("risk_level_min", 0)),
                risk_level_max   = int(row.get("risk_level_max", 0)),
                intersection_pct = float(row.get("intersection_pct", 0.0)),
                computed_at      = now_iso
            )
            for _, row in const_overlay.iterrows()
        ]

        if const_rows:
            spark.createDataFrame(const_rows).write \
                .format("delta").mode("append") \
                .option("mergeSchema", "true") \
                .saveAsTable(TBL_FGS_CONST_INTERSECT)
            print(f"  Written {len(const_rows)} constituency intersection rows.")
    else:
        print("  No constituency intersections found for this statement.")

print("Intersection computation complete.")
dbutils.notebook.exit("success")
