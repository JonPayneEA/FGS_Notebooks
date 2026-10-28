# =============================================================================
# utils/address_points.py
# Address points for the exposure product, built from the ONS National Statistics UPRN Lookup (NSUL). Pure pandas and numpy, so it is
# tested without Spark. The notebook (jobs/33_build_address_points) reads NSUL tile by tile and calls these.
#
# THE IDEA: each UPRN is a point. Census 2021 people, households and people aged 65 and over for its output area are shared equally among
# the output area's points, so people sit where the addresses are. The point then picks up its deprivation decile and the flood band at
# its position, looked up in the rasters that job 30 painted. A point is never given the worst of a whole cell.
#
# KNOWN LIMIT: NSUL holds every addressable property, homes and businesses alike, so population is shared a little too widely in town
# centres. OS AddressBase Premium classification would fix it (see docs/EXPOSURE_AND_RISK_GRID_PLAN.md).
#
#   clean_nsul           rename and type the NSUL columns, drop rows with no usable grid reference, keep England
#   oa_point_counts      how many points each output area has (computed once over all of England, so tiles can be built independently)
#   oa_shares            per output area, the share of each Census total that each of its points carries
#   build_points         one tile's points: shares, deprivation, flood bands
#   centroid_points      output areas that have Census people but no point: one point at the output area centre, carrying all of them
#   sample_hazard        flood band ranks at points, from the rasters; a tile with no raster is no flood
# =============================================================================

import os

import numpy as np
import pandas as pd

from .grid100 import cell_key
from .hazard_raster import load_raster, sample_ranks, tile_code_at

ENGLAND = "E92000001"
UNITARY_COUNTY = "E99999999"      # NSUL's code for "no county" (the district is the unitary authority)

NSUL_COLUMNS = {
    "UPRN": "uprn", "GRIDGB1E": "easting", "GRIDGB1N": "northing", "OA21CD": "oa", "LSOA21CD": "lsoa", "LAD25CD": "lad",
    "CTY25CD": "cty", "WD25CD": "ward", "PCON24CD": "pcon", "PFA23CD": "pfa", "RGN25CD": "rgn", "CTRY25CD": "ctry",
}
SHARE_COLS = ("population", "households", "age65plus")
POINT_COLS = {"population": "people", "households": "households", "age65plus": "over65"}

# the raster source folder and the column each becomes
HAZARD_SOURCES = {
    "rofrs": "rofrs_rank",
    "flood_zones": "fz_rank",
    "flood_zones_sea": "fz_sea_rank",
    "flood_zones_river": "fz_river_rank",
    "flood_zones_river_and_sea": "fz_river_and_sea_rank",
}


def clean_nsul(raw: pd.DataFrame, england_only: bool = True):
    """Rename and type NSUL columns. Returns (points, report). Rows with no grid reference (blank, zero or not a number) are dropped
    and counted: they cannot be placed. `ctyua` is the county, or the district where NSUL gives no county (a unitary authority)."""
    d = raw.rename(columns=NSUL_COLUMNS)[[c for c in NSUL_COLUMNS.values() if c in raw.rename(columns=NSUL_COLUMNS)]].copy()
    n0 = len(d)
    for c in ("easting", "northing"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    unplaced = d["easting"].isna() | d["northing"].isna() | (d["easting"] <= 0) | (d["northing"] <= 0)
    countries = d["ctry"].value_counts().to_dict() if "ctry" in d else {}
    d = d[~unplaced]
    if england_only and "ctry" in d:
        d = d[d["ctry"] == ENGLAND]
    d["ctyua"] = np.where(d["cty"].fillna(UNITARY_COUNTY).eq(UNITARY_COUNTY), d["lad"], d["cty"])
    d = d.drop(columns=["cty", "ctry"], errors="ignore").reset_index(drop=True)
    d["cell_key"] = cell_key(d["easting"].to_numpy(), d["northing"].to_numpy())
    return d, {"rows_in": n0, "unplaced": int(unplaced.sum()), "rows_kept": len(d), "countries": countries}


def oa_point_counts(oa_codes) -> pd.DataFrame:
    """Points per output area, from a series or array of output area codes."""
    return pd.Series(np.asarray(oa_codes)).value_counts().rename_axis("oa").reset_index(name="n_points")


def oa_shares(census: pd.DataFrame, counts: pd.DataFrame) -> pd.DataFrame:
    """For each output area with points: what each of its points carries of the Census totals (the total divided by the number of points).
    `census` has oa, population, households, age65plus; `counts` has oa, n_points."""
    d = census.merge(counts, on="oa", how="inner")
    for c in SHARE_COLS:
        d[POINT_COLS[c]] = d[c].astype("float64") / d["n_points"]
    return d[["oa", "n_points", *POINT_COLS.values()]]


def sample_hazard(points: pd.DataFrame, raster_dir: str, sources=None) -> pd.DataFrame:
    """Add a rank column (0 none, 1 to 4 band, or 1 to 2 for a Flood Zone) for each hazard source. Points are grouped by 50 km tile and the
    tile's raster read once. A missing raster means the tile has none of that hazard."""
    sources = sources or HAZARD_SOURCES
    d = points.copy()
    d["tile"] = tile_code_at(d["easting"].to_numpy(), d["northing"].to_numpy())
    for src, col in sources.items():
        d[col] = np.zeros(len(d), dtype="int8")
    for tile, idx in d.groupby("tile").indices.items():
        for src, col in sources.items():
            path = os.path.join(raster_dir, src, f"{tile}.npz")
            if not tile or not os.path.exists(path):
                continue
            arr, bounds, res = load_raster(path)
            d.iloc[idx, d.columns.get_loc(col)] = sample_ranks(arr, bounds, res, d["easting"].to_numpy()[idx], d["northing"].to_numpy()[idx])
    return d


def build_points(nsul_tile: pd.DataFrame, shares: pd.DataFrame, imd: pd.DataFrame, raster_dir: str, sources=None) -> pd.DataFrame:
    """One tile's address points: Census shares by output area, deprivation by LSOA, flood bands from the rasters.
    `imd` has lsoa, imd_score, imd_decile. A point whose output area is not in the Census table carries zeros."""
    d = nsul_tile.merge(shares[["oa", *POINT_COLS.values()]], on="oa", how="left")
    for c in POINT_COLS.values():
        d[c] = d[c].fillna(0.0)
    d = d.merge(imd[["lsoa", "imd_score", "imd_decile"]], on="lsoa", how="left")
    d["point_source"] = "nsul"
    return sample_hazard(d, raster_dir, sources)


def centroid_points(census: pd.DataFrame, counts: pd.DataFrame, centres: pd.DataFrame, imd: pd.DataFrame, raster_dir: str, sources=None):
    """Output areas that have Census people but no NSUL point. Each gets one point at its centre carrying the whole total, so national totals
    are conserved. `centres` has oa, lsoa, easting, northing. Returns (points, report): the report counts areas and people placed, and
    areas with no centre either (their people are lost and are reported)."""
    missing = census[~census["oa"].isin(counts["oa"])]
    if missing.empty:
        # nothing to place: every output area has at least one point
        cols = ["uprn", "easting", "northing", "oa", "lsoa", "lad", "ctyua", "ward", "pcon", "pfa", "rgn", "people", "households", "over65",
                "cell_key", "imd_score", "imd_decile", "point_source", "tile", *(sources or HAZARD_SOURCES).values()]
        return pd.DataFrame(columns=cols), {"areas_without_points": 0, "placed_at_centre": 0, "people_placed": 0.0,
                                            "areas_with_no_centre": 0, "people_lost": 0.0}
    centres = centres.assign(oa=centres["oa"].astype(str))
    missing = missing.assign(oa=missing["oa"].astype(str))
    placed = missing.drop(columns=["lsoa"], errors="ignore").merge(centres[["oa", "lsoa", "easting", "northing"]], on="oa", how="inner")
    lost = missing[~missing["oa"].isin(centres["oa"])]
    d = pd.DataFrame({
        "uprn": None, "easting": placed["easting"].to_numpy(dtype="float64"), "northing": placed["northing"].to_numpy(dtype="float64"),
        "oa": placed["oa"].to_numpy(), "lsoa": placed["lsoa"].to_numpy(), "lad": None, "ctyua": None, "ward": None, "pcon": None,
        "pfa": None, "rgn": None,
        "people": placed["population"].to_numpy(dtype="float64"), "households": placed["households"].to_numpy(dtype="float64"),
        "over65": placed["age65plus"].to_numpy(dtype="float64"),
    })
    d["cell_key"] = cell_key(d["easting"].to_numpy(), d["northing"].to_numpy())
    d = d.merge(imd[["lsoa", "imd_score", "imd_decile"]], on="lsoa", how="left")
    d["point_source"] = "oa_centroid"
    report = {"areas_without_points": len(missing), "placed_at_centre": len(placed), "people_placed": float(placed["population"].sum()),
              "areas_with_no_centre": len(lost), "people_lost": float(lost["population"].sum())}
    return sample_hazard(d, raster_dir, sources), report
