# =============================================================================
# utils/geo.py
# Spatial helpers shared by the lookup and intersection jobs (06 and 07).
# Needs GeoPandas and Shapely on the cluster. Kept apart from helpers.py so
# notebooks that do no spatial work (01, 02) do not import them.
# =============================================================================

import json

import geopandas as gpd
import pandas as pd
from shapely.geometry import shape

POLYGONAL = ("Polygon", "MultiPolygon")


def geojson_str_to_geodataframe(pdf: pd.DataFrame, crs: str = "EPSG:4326") -> gpd.GeoDataFrame:
    """
    Convert a pandas DataFrame with a GeoJSON geometry string column into a
    GeoDataFrame. None, NaN, non-string and unparseable values become None
    and are removed later by clean_geodataframe.
    """
    def parse_geom(s):
        if s is None or not isinstance(s, str):
            return None
        try:
            return shape(json.loads(s))
        except Exception:
            return None

    geometries = pdf["geometry"].apply(parse_geom)
    return gpd.GeoDataFrame(pdf.drop(columns=["geometry"]), geometry=geometries, crs=crs)


def clean_geodataframe(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Repair invalid geometries with buffer(0) and drop rows with no geometry.

    Lines are always valid for this purpose and are left alone, because
    buffer(0) would turn them into empty polygons. Call this after
    reprojection, which can introduce small invalidity.
    """
    def fix_geom(g):
        if g is None or g.is_empty:
            return None
        try:
            if g.is_valid or g.geom_type not in POLYGONAL:
                return g
            fixed = g.buffer(0)
            return None if fixed.is_empty else fixed
        except Exception:
            return None

    gdf = gdf.copy()
    gdf["geometry"] = gdf["geometry"].apply(fix_geom)
    before = len(gdf)
    gdf = gdf[gdf["geometry"].notna()].reset_index(drop=True)
    if before != len(gdf):
        print(f"  Dropped {before - len(gdf)} empty or unrepairable geometries.")
    return gdf


def statements_to_process(current, done, recompute_all=False, coastal_ids=None):
    """
    The statement ids job 07 should intersect, oldest first.

    Args:
        current:      {statement_id: last_modified_at} for every statement.
        done:         {statement_id: last_modified_at} already intersected and logged.
        recompute_all: Every statement, whether or not it is done.
        coastal_ids:  When given (coastal_only mode), the ids of statements that hold a line
                      geometry. Only those are recomputed, even if already done, because
                      only they can change when COASTAL_LINE_BUFFER_M does. Statements that
                      have not been intersected at all are still included, so a coastal-only
                      run never leaves a new statement behind.
    """
    if recompute_all:
        return sorted(current)
    wanted = set(coastal_ids) if coastal_ids is not None else None
    ids = []
    for sid, modified in current.items():
        pending = done.get(sid) != modified
        if pending or (wanted is not None and sid in wanted):
            ids.append(sid)
    return sorted(ids)


def buffer_lines(gdf: gpd.GeoDataFrame, metres: float) -> gpd.GeoDataFrame:
    """
    A copy of `gdf` (in a metric CRS) with every line geometry replaced by its buffer of
    `metres` on each side. Polygons are left alone. With metres <= 0 nothing changes.

    A coastal FGS "polygon" is really a line along the coast. A constituency is a
    polygon that stops at the coast, so a line lying on or just off that edge rarely
    crosses it. Buffering the line gives it a width, so the constituencies it runs
    beside are found. Use it for finding overlaps only: keep the original line for the
    geometry type, and report no percentage for it, because the share of a
    constituency covered by a buffer is not a meaningful number.
    """
    gdf = gdf.copy()
    if metres is None or metres <= 0:
        return gdf
    gdf["geometry"] = gdf["geometry"].apply(
        lambda g: g if g is None or g.geom_type in POLYGONAL else g.buffer(metres))
    return gdf


def coverage(base_geom, other_geom, min_area_m2: float = 1.0):
    """
    Measure how much of `base_geom` (an EA area or constituency polygon, in a
    metric CRS) is covered by `other_geom` (an FGS geometry).

    Returns:
        (overlaps, pct)
        overlaps: True if there is a real overlap (area above min_area_m2 for
                  polygons, positive length for lines). Geometries that only
                  touch return False.
        pct:      overlapping area / base area for polygon geometries, or
                  None for line geometries, which have no area.

    Raises:
        Exception from GEOS if the intersection cannot be computed even after
        a buffer(0) repair. Callers should count and log these.
    """
    try:
        inter = base_geom.intersection(other_geom)
    except Exception:
        inter = base_geom.buffer(0).intersection(other_geom.buffer(0))

    if other_geom.geom_type in POLYGONAL:
        area = inter.area
        if area < min_area_m2 or base_geom.area <= 0:
            return False, 0.0
        return True, float(area / base_geom.area)

    return inter.length > 0, None
