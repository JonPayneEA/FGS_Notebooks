# =============================================================================
# utils/helpers.py
# Shared utility functions used by more than one job.
# Import these rather than copying code between scripts.
# =============================================================================

import json
import requests
from datetime import datetime, timezone, date, timedelta

from pyspark.sql import SparkSession, Row


# -----------------------------------------------------------------------------
# get_ffc_api_key
# Retrieves the FFC API key from Databricks Secrets.
# Never hard-code the key in any script. Always fetch it at runtime this way.
#
# Now superseded
#
# -----------------------------------------------------------------------------
#def get_ffc_api_key() -> str:
#    """Fetch the FFC API key from environment variables loaded via .env"""
#    import os
#    key = os.getenv("FFC_API_KEY")
#    if not key:
#        raise ValueError("FFC_API_KEY not found -- check your .env file is present and loaded")
#    return key



# -----------------------------------------------------------------------------
# get_spark
# Returns the active SparkSession.
# In a Databricks notebook or job, Spark is already running.
# This just retrieves the existing session rather than starting a new one.
# -----------------------------------------------------------------------------
def get_spark() -> SparkSession:
    """Return the active SparkSession."""
    return SparkSession.builder.getOrCreate()


# -----------------------------------------------------------------------------
# ffc_get
# Makes an authenticated GET request to the FFC API.
# All API calls should go through this function so that authentication,
# error handling, and timeout logic are consistent across jobs.
# -----------------------------------------------------------------------------
def ffc_get(url: str, api_key: str, params: dict = None) -> dict:
    """
    Make an authenticated GET request to the FFC API.

    Args:
        url:     Full URL to request.
        api_key: The API key retrieved from Databricks Secrets.
        params:  Optional dictionary of query string parameters.

    Returns:
        Parsed JSON response as a Python dictionary.

    Raises:
        Exception if the response status is not 200.
    """
    # The FFC API authenticates via a Bearer token in the Authorization header.
    # Check the API docs if this header format changes in a future API version.
    headers = {"X-API-Key": api_key}

    response = requests.get(url, headers=headers, params=params, timeout=30)

    if response.status_code != 200:
        raise Exception(
            f"FFC API request failed: {response.status_code} -- {response.text}"
        )

    return response.json()


# -----------------------------------------------------------------------------
# utc_now
# Returns the current UTC timestamp.
# Used consistently across all jobs so every timestamp in the pipeline
# is in the same timezone.
# -----------------------------------------------------------------------------
def utc_now() -> datetime:
    """Return the current UTC datetime."""
    return datetime.now(timezone.utc)


# -----------------------------------------------------------------------------
# table_exists
# Checks whether a Unity Catalog table exists before trying to read from it.
# Used in jobs that need to check prior state (e.g. what was last ingested).
# -----------------------------------------------------------------------------
def table_exists(spark: SparkSession, table_name: str) -> bool:
    """
    Return True if the given fully qualified table exists in Unity Catalog.

    Args:
        spark:      Active SparkSession.
        table_name: Fully qualified name, e.g. "catalog.schema.table".
    """
    try:
        spark.sql(f"DESCRIBE TABLE {table_name}")
        return True
    except Exception:
        return False


# -----------------------------------------------------------------------------
# build_geojson_polygon
# The FFC API returns polygon coordinates as a nested list of rings.
# This wraps them into a valid GeoJSON geometry string that GeoPandas,
# Shapely, and Leaflet all understand natively.
#
# Example input:  [[[-1.5, 53.2], [-1.3, 53.2], [-1.5, 53.2]]]
# Example output: '{"type": "Polygon", "coordinates": [[[-1.5, 53.2], ...]]}'
# -----------------------------------------------------------------------------
def build_geojson_polygon(coordinates: list) -> str:
    """
    Wrap raw coordinate rings from the FFC API into a GeoJSON geometry string.

    Args:
        coordinates: List of rings, each ring a list of [lon, lat] pairs.
                     The first ring is the outer boundary; subsequent rings
                     are holes (rare in FGS data but valid GeoJSON).

    Returns:
        A GeoJSON Polygon geometry as a JSON string.
    """
    return json.dumps({"type": "Polygon", "coordinates": coordinates})


# -----------------------------------------------------------------------------
# parse_sources
# The FFC API returns flood sources as a list of single-key dicts, e.g.:
#   [{"coastal": "text"}, {"river": "text"}]
# This flattens that into a plain dict for easy column mapping.
# -----------------------------------------------------------------------------
def parse_sources(sources_list: list) -> dict:
    """
    Flatten the API sources list into a simple key-value dict.

    Args:
        sources_list: List of single-key dicts from the API response.

    Returns:
        Dict with source names as keys and summary text as values.
    """
    sources = {}
    for item in sources_list:
        sources.update(item)
    return sources


# -----------------------------------------------------------------------------
# parse_statement_row
# Parses the top-level metadata from a single FGS statement dict into a
# Spark Row for writing to fgs_statements.
# Defined here so both the live ingest and backfill jobs use identical logic.
# -----------------------------------------------------------------------------
def parse_statement_row(statement: dict, ingested_at: str) -> Row:
    """
    Parse top-level FGS metadata into a Spark Row.

    Args:
        statement:   A single statement dict from the FFC API response.
        ingested_at: UTC timestamp string for when this record was ingested.

    Returns:
        A Spark Row ready to write to fgs_statements.
    """
    sources = parse_sources(statement.get("sources", []))
    trend   = statement.get("flood_risk_trend", {})
    pub     = statement.get("public_forecast", {})

    return Row(
        statement_id           = statement["id"],
        issued_at              = statement["issued_at"],
        last_modified_at       = statement.get("last_modified_at"),
        next_issue_due_at      = statement.get("next_issue_due_at"),
        headline               = statement.get("headline"),
        amendments             = statement.get("amendments"),
        future_forecast        = statement.get("future_forecast"),
        flood_risk_trend_day1  = trend.get("day1"),
        flood_risk_trend_day2  = trend.get("day2"),
        flood_risk_trend_day3  = trend.get("day3"),
        flood_risk_trend_day4  = trend.get("day4"),
        flood_risk_trend_day5  = trend.get("day5"),
        source_coastal         = sources.get("coastal"),
        source_ground          = sources.get("ground"),
        source_river           = sources.get("river"),
        source_surface         = sources.get("surface"),
        england_forecast       = pub.get("england_forecast"),
        wales_forecast_english = pub.get("wales_forecast_english"),
        pdf_url                = statement.get("pdf_url"),
        detailed_csv_url       = statement.get("detailed_csv_url"),
        ingested_at            = ingested_at
    )


# -----------------------------------------------------------------------------
# parse_risk_rows
# Explodes the nested risk_areas -> risk_area_blocks -> polys structure
# into flat Spark Rows, one per polygon per source per day.
# This is the core parsing logic for fgs_risk_polygons.
# -----------------------------------------------------------------------------
def parse_risk_rows(statement: dict, ingested_at: str) -> list:
    """
    Parse FGS risk polygons into flat Spark Rows.

    The nesting in the API is:
        statement -> risk_areas -> risk_area_blocks -> polys

    We explode this to one row per polygon per source per day so the
    resulting table is fully flat and queryable without unpacking arrays.

    Args:
        statement:   A single statement dict from the FFC API response.
        ingested_at: UTC timestamp string for when this record was ingested.

    Returns:
        List of Spark Rows ready to write to fgs_risk_polygons.
    """
    rows = []
    issued_date = date.fromisoformat(statement["issued_at"][:10])

    for risk_area in statement.get("risk_areas", []):
        for block in risk_area.get("risk_area_blocks", []):

            # risk_levels is a dict like {"river": [2, 3], "surface": [1, 2]}
            risk_levels = block.get("risk_levels", {})

            # days is a sparse list -- not necessarily [1, 2, 3, 4, 5]
            for day_index in block.get("days", []):

                # Derive the calendar date for this forecast day.
                # day_index 1 = the day the FGS was issued, so subtract 1.
                forecast_date = (
                    issued_date + timedelta(days=day_index - 1)
                ).isoformat()

                for poly in block.get("polys", []):

                    # Store geometry as a GeoJSON string.
                    # Leaflet, GeoPandas, and Shapely all read this natively.
                    geojson_str = build_geojson_polygon(poly["coordinates"])

                    # One row per source so risk level is always a simple
                    # integer pair, never a nested structure.
                    for source, levels in risk_levels.items():
                        rows.append(Row(
                            statement_id       = statement["id"],
                            issued_at          = statement["issued_at"],
                            risk_area_id       = risk_area["id"],
                            risk_area_block_id = block["id"],
                            poly_id            = poly["id"],
                            day_index          = day_index,
                            forecast_date      = forecast_date,
                            source             = source,
                            risk_level_min     = levels[0],
                            risk_level_max     = levels[1],
                            poly_type          = poly.get("poly_type"),
                            beyond_five_days   = risk_area.get("beyond_five_days", False),
                            geometry           = geojson_str,
                            ingested_at        = ingested_at
                        ))
    return rows


# -----------------------------------------------------------------------------
# parse_aoc_rows
# Parses the aoc_maps polygons into flat Spark Rows for fgs_aoc_polygons.
# AOC polygons are cartographic reference shapes with no risk attribution.
# -----------------------------------------------------------------------------
def parse_aoc_rows(statement: dict, ingested_at: str) -> list:
    """
    Parse FGS Area of Concern polygons into Spark Rows.

    Args:
        statement:   A single statement dict from the FFC API response.
        ingested_at: UTC timestamp string for when this record was ingested.

    Returns:
        List of Spark Rows ready to write to fgs_aoc_polygons.
    """
    rows = []

    for aoc_map in statement.get("aoc_maps", []):
        for poly in aoc_map.get("polys", []):
            rows.append(Row(
                statement_id = statement["id"],
                issued_at    = statement["issued_at"],
                aoc_map_id   = aoc_map["id"],
                poly_id      = poly["id"],
                poly_type    = poly.get("poly_type"),
                geometry     = build_geojson_polygon(poly["coordinates"]),
                ingested_at  = ingested_at
            ))

    return rows
