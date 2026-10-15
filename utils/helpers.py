# =============================================================================
# utils/helpers.py
# Shared utility functions used by more than one job.
# Import these rather than copying code between scripts.
# Spatial helpers (which need GeoPandas) live in utils/geo.py.
# =============================================================================

import json
import time
import requests
from datetime import datetime, timezone, date, timedelta

import pandas as pd
from pyspark.sql import SparkSession, Row
from pyspark.sql.types import (
    StructType, StructField, StringType, LongType, BooleanType
)

# =============================================================================
# RISK MATRIX LOOKUPS
# The FGS risk matrix is 4x4. The API gives each position as [x, y].
# Source: FGS Public API documentation v3 ("Risk Level Matrix") and the
# FGS User Guide (https://www.gov.uk/government/publications/
# flood-forecasting-centre-services-flood-guidance-statement/
# flood-guidance-statement-user-guide)
#
# X axis (horizontal) = Impact:     1=Minor, 2=Moderate, 3=Significant, 4=Extreme
# Y axis (vertical)   = Likelihood: 1=Low (10-25%), 2=Medium (25-50%),
#                                   3=High (50-75%), 4=Very High (>75%)
# Example from the API docs: (2, 3) = moderate impacts, high likelihood.
#
# The API documentation gives matrix POSITIONS only. The overall risk level and
# colour below (RISK_MATRIX) come from the FGS User Guide table. Check them
# against the published table before relying on them.
# =============================================================================

IMPACT_LABELS = {
    1: "Minor",
    2: "Moderate",
    3: "Significant",
    4: "Extreme",
}

LIKELIHOOD_LABELS = {
    1: "Low",
    2: "Medium",
    3: "High",
    4: "Very High",
}

# Risk level derived from (x=impact, y=likelihood) matrix position.
RISK_MATRIX = {
    (1, 1): ("Very Low", "Green"),
    (2, 1): ("Very Low", "Green"),
    (3, 1): ("Low",      "Yellow"),
    (4, 1): ("Low",      "Yellow"),
    (1, 2): ("Very Low", "Green"),
    (2, 2): ("Very Low", "Green"),
    (3, 2): ("Low",      "Yellow"),
    (4, 2): ("Medium",   "Amber"),
    (1, 3): ("Very Low", "Green"),
    (2, 3): ("Low",      "Yellow"),
    (3, 3): ("Medium",   "Amber"),
    (4, 3): ("Medium",   "Amber"),
    (1, 4): ("Very Low", "Green"),
    (2, 4): ("Low",      "Yellow"),
    (3, 4): ("Medium",   "Amber"),
    (4, 4): ("High",     "Red"),
}

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
# get_with_retry
# GET with exponential backoff on connection errors, 429 and 5xx responses.
# Other status codes are returned to the caller to handle.
# -----------------------------------------------------------------------------
def get_with_retry(url: str, headers: dict = None, params: dict = None,
                   timeout: int = 30, attempts: int = 4) -> requests.Response:
    """
    GET a URL, retrying transient failures with backoff (2s, 4s, 8s ...).

    Returns:
        The final Response. Raises the last connection error if every attempt
        failed to get any response.
    """
    last_error = None
    for attempt in range(attempts):
        try:
            response = requests.get(url, headers=headers, params=params, timeout=timeout)
            if response.status_code != 429 and response.status_code < 500:
                return response
            last_error = RuntimeError(f"HTTP {response.status_code} from {url}")
            final_response = response
        except (requests.ConnectionError, requests.Timeout) as e:
            last_error = e
            final_response = None

        if attempt < attempts - 1:
            time.sleep(2 ** (attempt + 1))

    if final_response is not None:
        return final_response
    raise last_error


# -----------------------------------------------------------------------------
# ffc_get
# Makes an authenticated GET request to the FFC API.
# All API calls should go through this function so that authentication,
# error handling, and timeout logic are consistent across jobs.
# -----------------------------------------------------------------------------
def ffc_get(url: str, api_key: str, params: dict = None, timeout: int = 30,
            attempts: int = 4) -> dict:
    """
    Make an authenticated GET request to the FFC API.

    Args:
        url:     Full URL to request.
        api_key: The API key (see config.get_ffc_api_key).
        params:  Optional dictionary of query string parameters.
        timeout: Seconds to wait for a response, per attempt. Large statement
                 pages can be slow: the backfill raises this.
        attempts: Tries before giving up (backoff of 2, 4, 8 seconds between).

    Returns:
        Parsed JSON response as a Python dictionary.

    Raises:
        Exception if the response status is not 200 after retries.
    """
    if not api_key:
        raise RuntimeError("FFC API key is empty.")

    # The FFC public API v3 authenticates with an X-Api-Key header.
    headers = {"X-Api-Key": api_key}

    response = get_with_retry(url, headers=headers, params=params,
                              timeout=timeout, attempts=attempts)

    if response.status_code != 200:
        raise Exception(
            f"FFC API request failed: {response.status_code} -- {response.text[:500]}"
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
# Uses the catalog API so that a permissions or connectivity error is raised
# rather than being read as "the table does not exist".
# -----------------------------------------------------------------------------
def table_exists(spark: SparkSession, table_name: str) -> bool:
    """
    Return True if the given fully qualified table exists in Unity Catalog.

    Args:
        spark:      Active SparkSession.
        table_name: Fully qualified name, e.g. "catalog.schema.table".
    """
    return spark.catalog.tableExists(table_name)


# -----------------------------------------------------------------------------
# guard_row_count
# Refuse to overwrite a reference table with a suspiciously small refresh.
# -----------------------------------------------------------------------------
def guard_row_count(spark: SparkSession, table_name: str, new_count: int,
                    min_ratio: float = 0.8, min_rows: int = 1) -> None:
    """
    Raise if new_count is below min_rows, or below min_ratio of the existing
    row count. Call this before an overwrite.
    """
    if new_count < min_rows:
        raise Exception(
            f"Refusing to overwrite {table_name}: only {new_count} rows "
            f"(minimum {min_rows})."
        )
    if table_exists(spark, table_name):
        existing = spark.table(table_name).count()
        if existing > 0 and new_count < existing * min_ratio:
            raise Exception(
                f"Refusing to overwrite {table_name}: new data has {new_count} rows "
                f"against {existing} existing (minimum ratio {min_ratio})."
            )


# -----------------------------------------------------------------------------
# to_native
# Convert pandas / numpy scalars to plain Python values so Spark can accept
# them against an explicit schema. NaN, NaT and None all become None.
# -----------------------------------------------------------------------------
def to_native(value):
    """Return a plain Python value (or None) for a pandas/numpy scalar."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    if hasattr(value, "item"):
        return value.item()
    return value


# -----------------------------------------------------------------------------
# build_geojson_geometry
# The FFC API returns inland risk areas as polygons and coastal risk areas as
# polylines. Each poly carries its own "type". This wraps the coordinates into
# a valid GeoJSON geometry string of the right kind.
# -----------------------------------------------------------------------------
_GEOJSON_TYPES = {
    "polygon":         "Polygon",
    "multipolygon":    "MultiPolygon",
    "linestring":      "LineString",
    "multilinestring": "MultiLineString",
    "polyline":        "LineString",
    "line":            "LineString",
}

# Coordinate nesting depth expected for each GeoJSON type (Point = 1).
_EXPECTED_DEPTH = {
    "LineString":      2,
    "Polygon":         3,
    "MultiLineString": 3,
    "MultiPolygon":    4,
}


def _nesting_depth(coordinates) -> int:
    depth = 0
    while isinstance(coordinates, list) and coordinates:
        coordinates = coordinates[0]
        depth += 1
    return depth


def build_geojson_geometry(poly: dict) -> tuple:
    """
    Build a GeoJSON geometry string from an FFC API poly dict.

    The declared "type" is used when it agrees with the nesting of the
    coordinates. Otherwise the type is inferred from the nesting (depth 2 is a
    LineString, 3 a Polygon, 4 a MultiPolygon).

    Args:
        poly: A poly dict with "coordinates" and, normally, "type".

    Returns:
        (geojson_string, geojson_type)
    """
    coordinates = poly["coordinates"]
    declared    = _GEOJSON_TYPES.get(str(poly.get("type") or "").strip().lower())
    depth       = _nesting_depth(coordinates)

    if declared and _EXPECTED_DEPTH[declared] == depth:
        geojson_type = declared
    else:
        inferred = {2: "LineString", 3: "Polygon", 4: "MultiPolygon"}.get(depth)
        if inferred is None:
            raise ValueError(
                f"Cannot determine geometry type: declared={poly.get('type')!r}, "
                f"coordinate depth={depth}"
            )
        geojson_type = inferred

    return json.dumps({"type": geojson_type, "coordinates": coordinates}), geojson_type


def build_geojson_polygon(coordinates: list) -> str:
    """Wrap polygon rings into a GeoJSON Polygon string (kept for compatibility)."""
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
    for item in sources_list or []:
        sources.update(item)
    return sources


# =============================================================================
# EXPLICIT SCHEMAS
# Defined once so every write uses identical types and so an empty or
# all-null column never breaks schema inference.
# =============================================================================

def _string_fields(*names):
    return [StructField(n, StringType(), True) for n in names]


STATEMENT_SCHEMA = StructType(
    [StructField("statement_id", LongType(), True)]
    + _string_fields(
        "issued_at", "last_modified_at", "next_issue_due_at", "headline",
        "amendments", "future_forecast",
        "flood_risk_trend_day1", "flood_risk_trend_day2", "flood_risk_trend_day3",
        "flood_risk_trend_day4", "flood_risk_trend_day5",
        "source_coastal", "source_ground", "source_river", "source_surface",
        "england_forecast", "wales_forecast_english",
        "pdf_url", "detailed_csv_url", "ingested_at",
    )
)

RISK_SCHEMA = StructType([
    StructField("statement_id",       LongType(),    True),
    StructField("issued_at",          StringType(),  True),
    StructField("risk_area_id",       LongType(),    True),
    StructField("risk_area_block_id", LongType(),    True),
    StructField("poly_id",            LongType(),    True),
    StructField("day_index",          LongType(),    True),
    StructField("forecast_date",      StringType(),  True),
    StructField("source",             StringType(),  True),
    StructField("risk_x",             LongType(),    True),
    StructField("risk_y",             LongType(),    True),
    StructField("impact_label",       StringType(),  True),
    StructField("likelihood_label",   StringType(),  True),
    StructField("risk_level",         StringType(),  True),
    StructField("risk_colour",        StringType(),  True),
    StructField("poly_type",          StringType(),  True),
    StructField("beyond_five_days",   BooleanType(), True),
    StructField("geometry_type",      StringType(),  True),
    StructField("geometry",           StringType(),  True),
    StructField("ingested_at",        StringType(),  True),
])

AOC_SCHEMA = StructType([
    StructField("statement_id",        LongType(),   True),
    StructField("issued_at",           StringType(), True),
    StructField("aoc_map_id",          LongType(),   True),
    StructField("poly_id",             LongType(),   True),
    # The risk area block this AOC polygon belongs to. It carries the flood
    # source, forecast days and impact/likelihood (see fgs_risk_blocks).
    StructField("risk_area_block_id",  LongType(),   True),
    StructField("poly_type",           StringType(), True),
    StructField("geometry_type",       StringType(), True),
    StructField("geometry",            StringType(), True),
    StructField("ingested_at",         StringType(), True),
])

# One row per risk area block, forecast day and flood source. No geometry: this
# is the link table between risk polygons (fgs_risk_polygons) and Area of
# Concern polygons (fgs_aoc_polygons), which both carry risk_area_block_id.
BLOCK_SCHEMA = StructType([
    StructField("statement_id",       LongType(),    True),
    StructField("issued_at",          StringType(),  True),
    StructField("risk_area_id",       LongType(),    True),
    StructField("risk_area_block_id", LongType(),    True),
    StructField("day_index",          LongType(),    True),
    StructField("forecast_date",      StringType(),  True),
    StructField("source",             StringType(),  True),
    StructField("risk_x",             LongType(),    True),
    StructField("risk_y",             LongType(),    True),
    StructField("impact_label",       StringType(),  True),
    StructField("likelihood_label",   StringType(),  True),
    StructField("risk_level",         StringType(),  True),
    StructField("risk_colour",        StringType(),  True),
    StructField("beyond_five_days",   BooleanType(), True),
    StructField("ingested_at",        StringType(),  True),
])


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
    trend   = statement.get("flood_risk_trend") or {}
    pub     = statement.get("public_forecast") or {}

    return Row(
        statement_id           = int(statement["id"]),
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
# _risk_fields
# The risk attributes of one (risk area block, forecast day, flood source).
# Shared by parse_risk_rows and parse_block_rows so both always agree.
# -----------------------------------------------------------------------------
def _risk_fields(statement: dict, risk_area: dict, block: dict,
                 day_index: int, source: str, levels) -> dict:
    """
    Return the derived risk fields for one block/day/source, or None when
    risk_levels for that source is unusable (a warning is printed).

    levels is [x, y]: impact and likelihood matrix coordinates.
    """
    if not isinstance(levels, (list, tuple)) or len(levels) < 2:
        print(
            f"WARNING: statement {statement['id']} block {block.get('id')} "
            f"source {source!r} has unusable risk_levels {levels!r}; skipped."
        )
        return None

    risk_x = int(levels[0])
    risk_y = int(levels[1])
    issued_date = date.fromisoformat(statement["issued_at"][:10])

    # day_index 1 = the day the FGS was issued, so subtract 1.
    forecast_date = (issued_date + timedelta(days=day_index - 1)).isoformat()
    risk_level, risk_colour = RISK_MATRIX.get((risk_x, risk_y), ("Unknown", "Unknown"))

    return {
        "risk_area_id":       risk_area["id"],
        "risk_area_block_id": block["id"],
        "day_index":          day_index,
        "forecast_date":      forecast_date,
        "source":             source,
        "risk_x":             risk_x,
        "risk_y":             risk_y,
        "impact_label":       IMPACT_LABELS.get(risk_x, "Unknown"),
        "likelihood_label":   LIKELIHOOD_LABELS.get(risk_y, "Unknown"),
        "risk_level":         risk_level,
        "risk_colour":        risk_colour,
        "beyond_five_days":   bool(risk_area.get("beyond_five_days", False)),
    }


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

    for risk_area in statement.get("risk_areas", []):
        for block in risk_area.get("risk_area_blocks", []):

            # risk_levels is a dict like {"river": [2, 3], "surface": [1, 2]}
            risk_levels = block.get("risk_levels") or {}

            # days is a sparse list -- not necessarily [1, 2, 3, 4, 5]
            for day_index in block.get("days", []):
                for poly in block.get("polys", []):

                    # Inland risk areas are polygons, coastal ones polylines.
                    # Store whichever the API sent as a GeoJSON string.
                    geojson_str, geometry_type = build_geojson_geometry(poly)

                    # One row per source so risk level is always a simple
                    # integer pair, never a nested structure.
                    for source, levels in risk_levels.items():
                        f = _risk_fields(statement, risk_area, block, day_index, source, levels)
                        if f is None:
                            continue

                        rows.append(Row(
                            statement_id       = int(statement["id"]),
                            issued_at          = statement["issued_at"],
                            risk_area_id       = f["risk_area_id"],
                            risk_area_block_id = f["risk_area_block_id"],
                            poly_id            = poly["id"],
                            day_index          = f["day_index"],
                            forecast_date      = f["forecast_date"],
                            source             = f["source"],
                            risk_x             = f["risk_x"],
                            risk_y             = f["risk_y"],
                            impact_label       = f["impact_label"],
                            likelihood_label   = f["likelihood_label"],
                            risk_level         = f["risk_level"],
                            risk_colour        = f["risk_colour"],
                            poly_type          = poly.get("poly_type"),
                            beyond_five_days   = f["beyond_five_days"],
                            geometry_type      = geometry_type,
                            geometry           = geojson_str,
                            ingested_at        = ingested_at
                        ))
    return rows


# -----------------------------------------------------------------------------
# parse_block_rows
# One row per risk area block, forecast day and flood source, with no geometry.
# This is what links the two geometry sets: a risk polygon and an AOC polygon
# each carry a risk_area_block_id, and this table says what that block means
# (source, day, impact, likelihood, overall risk).
# -----------------------------------------------------------------------------
def parse_block_rows(statement: dict, ingested_at: str) -> list:
    """
    Parse FGS risk area blocks into Spark Rows for fgs_risk_blocks.

    Args:
        statement:   A single statement dict from the FFC API response.
        ingested_at: UTC timestamp string for when this record was ingested.

    Returns:
        List of Spark Rows ready to write to fgs_risk_blocks.
    """
    rows = []

    for risk_area in statement.get("risk_areas", []):
        for block in risk_area.get("risk_area_blocks", []):
            risk_levels = block.get("risk_levels") or {}

            for day_index in block.get("days", []):
                for source, levels in risk_levels.items():
                    f = _risk_fields(statement, risk_area, block, day_index, source, levels)
                    if f is None:
                        continue

                    rows.append(Row(
                        statement_id       = int(statement["id"]),
                        issued_at          = statement["issued_at"],
                        risk_area_id       = f["risk_area_id"],
                        risk_area_block_id = f["risk_area_block_id"],
                        day_index          = f["day_index"],
                        forecast_date      = f["forecast_date"],
                        source             = f["source"],
                        risk_x             = f["risk_x"],
                        risk_y             = f["risk_y"],
                        impact_label       = f["impact_label"],
                        likelihood_label   = f["likelihood_label"],
                        risk_level         = f["risk_level"],
                        risk_colour        = f["risk_colour"],
                        beyond_five_days   = f["beyond_five_days"],
                        ingested_at        = ingested_at
                    ))
    return rows


# -----------------------------------------------------------------------------
# parse_aoc_rows
# Parses the aoc_maps polygons into flat Spark Rows for fgs_aoc_polygons.
# One row per AOC polygon. Each carries risk_area_block_id, the link to the
# risk area block that gives it its flood source, days and risk (fgs_risk_blocks).
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
            geojson_str, geometry_type = build_geojson_geometry(poly)
            block_id = poly.get("risk_area_block_id")
            rows.append(Row(
                statement_id       = int(statement["id"]),
                issued_at          = statement["issued_at"],
                aoc_map_id         = aoc_map["id"],
                poly_id            = poly["id"],
                risk_area_block_id = int(block_id) if block_id is not None else None,
                poly_type          = poly.get("poly_type"),
                geometry_type      = geometry_type,
                geometry           = geojson_str,
                ingested_at        = ingested_at
            ))

    return rows


# =============================================================================
# CONSTITUENCY NAME KEY
# The Parliament API gives no ONS code, so MPs are matched to constituencies on
# name. The two sources do not always spell a name the same way, so both sides
# are reduced to the same key first. Used by the two contact tables (13, 14).
# =============================================================================

# Accented letters folded to plain ones ("Ynys M\u00f4n" and "Ynys Mon" match).
_ACCENTS_FROM = "\u00e0\u00e1\u00e2\u00e4\u00e3\u00e5\u00e8\u00e9\u00ea\u00eb\u00ec\u00ed\u00ee\u00ef\u00f2\u00f3\u00f4\u00f6\u00f5\u00f9\u00fa\u00fb\u00fc\u00fd\u00ff\u00e7\u00f1\u0175\u0177"
_ACCENTS_TO   = "aaaaaaeeeeiiiiooooouuuuyycnwy"
assert len(_ACCENTS_FROM) == len(_ACCENTS_TO)


def constituency_name_key(column: str) -> str:
    """
    SQL expression that reduces a constituency name column to a match key:
    lower case, accents folded, "&" as "and", letters and digits only.
    """
    folded = f"translate(lower({column}), '{_ACCENTS_FROM}', '{_ACCENTS_TO}')"
    return f"regexp_replace(regexp_replace({folded}, '&', 'and'), '[^a-z0-9]', '')"


# =============================================================================
# IDEMPOTENT WRITES KEYED BY STATEMENT
# =============================================================================

def source_predicate(spark: SparkSession, table_name: str, source: str,
                     alias: str = "", legacy_source: str = "risk_areas") -> str:
    """
    SQL condition selecting rows of one geometry source from a table that has a
    geometry_source column. Rows written before the column existed (column
    absent, or NULL) count as legacy_source. Returns "1 = 1" / "1 = 0" when the
    column is absent, so the result is always a valid predicate.

    Example:  f"WHERE {source_predicate(spark, TBL, 'aoc', alias='i')}"
    """
    prefix = f"{alias}." if alias else ""
    if "geometry_source" in spark.table(table_name).columns:
        return f"COALESCE({prefix}geometry_source, '{legacy_source}') = '{source}'"
    return "1 = 1" if source == legacy_source else "1 = 0"


def risk_rank_sql(column: str) -> str:
    """SQL expression turning a risk_level text into its rank: 1 Very Low (Green),
    2 Low (Yellow), 3 Medium (Amber), 4 High (Red), 0 unknown or none."""
    return (f"CASE {column} WHEN 'Very Low' THEN 1 WHEN 'Low' THEN 2 "
            f"WHEN 'Medium' THEN 3 WHEN 'High' THEN 4 ELSE 0 END")


def risk_level_sql(rank_expr: str) -> str:
    """Inverse of risk_rank_sql: rank back to the level text (NULL for 0)."""
    return (f"CASE {rank_expr} WHEN 1 THEN 'Very Low' WHEN 2 THEN 'Low' "
            f"WHEN 3 THEN 'Medium' WHEN 4 THEN 'High' END")


def latest_complete_statements(spark: SparkSession, tbl_statements: str,
                               tbl_log: str, geometry_source: str,
                               n: int = 1) -> list:
    """
    The newest n statements whose intersections are complete and up to date for
    one geometry source, newest first. A statement counts as complete when
    fgs_intersections_log holds a row for it at the statement's current
    last_modified_at, so a statement amended upstream but not yet re-intersected
    is skipped until 07 catches up.

    Returns a list of dicts: statement_id, issued_at, last_modified_at,
    intersections_computed_at. An empty list means nothing is complete yet.
    """
    in_source = source_predicate(spark, tbl_log, geometry_source, alias="l")
    rows = spark.sql(f"""
        SELECT l.statement_id,
               s.issued_at,
               s.last_modified_at,
               l.computed_at AS intersections_computed_at
        FROM {tbl_log} l
        JOIN {tbl_statements} s
          ON s.statement_id = l.statement_id
         AND COALESCE(s.last_modified_at, '') = COALESCE(l.last_modified_at, '')
        WHERE {in_source}
        ORDER BY s.issued_at DESC, l.statement_id DESC
        LIMIT {int(n)}
    """).collect()
    return [r.asDict() for r in rows]


def replace_statement_rows(spark: SparkSession, table_name: str,
                           statement_ids: list, rows: list,
                           schema: StructType,
                           source_column: str = None,
                           source_value: str = None,
                           legacy_source: str = None) -> int:
    """
    Replace every row for the given statement ids in table_name with `rows`.

    Existing rows for those ids are deleted first, so running this twice for
    the same statement never duplicates data. An empty `rows` list clears the
    statement's rows. The table is created on first write.

    Args:
        spark:         Active SparkSession.
        table_name:    Fully qualified Delta table.
        statement_ids: Statement ids whose rows are being replaced.
        rows:          Rows (Row or dict) matching `schema`.
        schema:        Explicit schema for the write.
        source_column: Optional column that partitions the table by geometry
                       source (for example "geometry_source"). When given, only
                       rows with source_column = source_value are deleted, so
                       replacing one source never touches another.
        source_value:  The source being written (needed with source_column).
        legacy_source: The source that rows written before source_column existed
                       belong to. Such rows (column absent, or NULL) count as
                       this source.

    Returns:
        Number of rows written.
    """
    ids = sorted({int(i) for i in statement_ids})
    if not ids:
        return 0

    if table_exists(spark, table_name):
        id_list = ", ".join(str(i) for i in ids)
        where = f"statement_id IN ({id_list})"

        if source_column is None:
            spark.sql(f"DELETE FROM {table_name} WHERE {where}")
        elif source_column not in spark.table(table_name).columns:
            # Table predates the column: every existing row is the legacy source.
            if source_value == legacy_source:
                spark.sql(f"DELETE FROM {table_name} WHERE {where}")
        else:
            spark.sql(
                f"DELETE FROM {table_name} WHERE {where} "
                f"AND COALESCE({source_column}, '{legacy_source}') = '{source_value}'"
            )

    if not rows:
        return 0

    data = [tuple(r[f.name] for f in schema.fields) for r in rows]
    (
        spark.createDataFrame(data, schema).write
        .format("delta")
        .mode("append")
        .option("mergeSchema", "true")   # Tolerate new columns without failing
        .saveAsTable(table_name)
    )
    return len(rows)


def write_statements(spark: SparkSession, statements: list, ingested_at: str,
                     tbl_statements: str, tbl_risk: str, tbl_aoc: str,
                     tbl_blocks: str = None) -> dict:
    """
    Parse and write FGS statements idempotently.

    Child tables (risk area blocks, risk polygons, AOC polygons) are written
    first and the statement row last. The statement row is what the ingest job
    checks to decide whether a statement is already stored, so a failure part
    way through leaves the statement looking un-ingested and the next run
    repeats it cleanly instead of skipping it with half the data missing.

    Returns:
        Dict of counts: statements, risk_rows, aoc_rows, block_rows.
    """
    ids = [s["id"] for s in statements]

    risk_rows, aoc_rows, block_rows = [], [], []
    for s in statements:
        risk_rows.extend(parse_risk_rows(s, ingested_at))
        aoc_rows.extend(parse_aoc_rows(s, ingested_at))
        block_rows.extend(parse_block_rows(s, ingested_at))
    stmt_rows = [parse_statement_row(s, ingested_at) for s in statements]

    n_block = 0
    if tbl_blocks:
        n_block = replace_statement_rows(spark, tbl_blocks, ids, block_rows, BLOCK_SCHEMA)
    n_risk = replace_statement_rows(spark, tbl_risk, ids, risk_rows, RISK_SCHEMA)
    n_aoc  = replace_statement_rows(spark, tbl_aoc,  ids, aoc_rows,  AOC_SCHEMA)
    n_stmt = replace_statement_rows(spark, tbl_statements, ids, stmt_rows, STATEMENT_SCHEMA)

    return {"statements": n_stmt, "risk_rows": n_risk, "aoc_rows": n_aoc, "block_rows": n_block}
