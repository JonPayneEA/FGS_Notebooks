# =============================================================================
# utils/grid100.py
# The 100 m British National Grid that the exposure and risk product is built on. Pure numpy, pandas and shapely:
# no Spark, so it can be tested without Databricks.
#
# A CELL is a 100 m square of the British National Grid, named by an integer key:
#     key = (northing // 100) * 10_000 + (easting // 100)
# England's eastings are below 700,000 m, so easting // 100 is below 10,000 and the key is unique and easy to decode.
# A polygon OWNS the cells whose centre lies inside it. That is a rule with a known error of at most half a cell (50 m) at
# a boundary; it is used for warning areas, risk bands and output areas alike so that every layer agrees.
#
# WHAT IS HERE
#   cell_key / cell_xy / cell_centre   the key and its coordinates
#   polygon_cells / polygons_to_cells  which cells a polygon (or many) owns; a polygon too small to own any owns the cell of its
#                                      representative point, so nothing vanishes
#   apportion_to_cells                 spread a total (a population) evenly over the cells its polygon owns; totals are conserved
#   worst_band_by_cell                 the highest band where polygons overlap
#   band_rank                          "High", "Medium", "Low", "Very Low" (any case) to 4, 3, 2, 1; anything else 0
#   aggregate_to_areas                 add cell columns up to areas, optionally split by a band
#   covered_cells / union_total        the cells covered by ANY of a set of areas, and a total over them: each person counted once
#   primary_area                       one owning area per cell when areas overlap, by a stated precedence
#   overlap_depth / pair_overlaps      how much areas overlap: cells by how many cover them, and each overlapping pair with its shared people
#   area_overlap_stats                 per area: gross (all its cells, may overlap), unique (cells it owns, additive), shared, and an equal split
#   risk_score                         the adjustable composite score, with its weights in config.RISK_SCORE_WEIGHTS
# =============================================================================

import numpy as np
import pandas as pd
import shapely

CELL_M = 100
KEY_STRIDE = 10_000

# The NaFRA2 chance-of-flooding-in-any-year bands, as a rank. 0 is no band (not in a risk area).
BAND_RANKS = {"very low": 1, "low": 2, "medium": 3, "high": 4}
BAND_LABELS = {0: "None", 1: "Very Low", 2: "Low", 3: "Medium", 4: "High"}


def band_rank(label):
    """The rank of a band label (any case, extra spaces and underscores ignored); 0 for a missing or unknown label."""
    if label is None or (not isinstance(label, str) and pd.isna(label)):
        return 0
    return BAND_RANKS.get(" ".join(str(label).replace("_", " ").lower().split()), 0)


def cell_key(easting, northing):
    """The key of the cell containing each point (arrays or scalars)."""
    e = np.floor(np.asarray(easting, dtype="float64") / CELL_M).astype("int64")
    n = np.floor(np.asarray(northing, dtype="float64") / CELL_M).astype("int64")
    return n * KEY_STRIDE + e


def cell_xy(key):
    """The south-west corner (easting, northing) of each cell."""
    k = np.asarray(key, dtype="int64")
    return (k % KEY_STRIDE) * CELL_M, (k // KEY_STRIDE) * CELL_M


def cell_centre(key):
    x, y = cell_xy(key)
    return x + CELL_M / 2, y + CELL_M / 2


def polygon_cells(geom) -> np.ndarray:
    """The keys of the cells whose centre lies inside `geom` (a shapely polygon in British National Grid metres)."""
    if geom is None or geom.is_empty:
        return np.array([], dtype="int64")
    minx, miny, maxx, maxy = geom.bounds
    xs = np.arange(np.floor(minx / CELL_M) * CELL_M + CELL_M / 2, maxx + CELL_M, CELL_M)
    ys = np.arange(np.floor(miny / CELL_M) * CELL_M + CELL_M / 2, maxy + CELL_M, CELL_M)
    gx, gy = np.meshgrid(xs, ys)
    inside = shapely.contains_xy(geom, gx.ravel(), gy.ravel())
    if inside.any():
        return np.unique(cell_key(gx.ravel()[inside], gy.ravel()[inside]))
    p = geom.representative_point()                  # smaller than a cell: it still owns one, so its totals are not lost
    return np.array([cell_key(p.x, p.y)], dtype="int64")


def polygons_to_cells(geoms, ids) -> pd.DataFrame:
    """A long table (id, cell_key): every cell each polygon owns. `geoms` and `ids` are parallel sequences."""
    parts = []
    for g, i in zip(geoms, ids):
        k = polygon_cells(g)
        if len(k):
            parts.append(pd.DataFrame({"id": i, "cell_key": k}))
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame({"id": [], "cell_key": np.array([], dtype="int64")})


def apportion_to_cells(cells: pd.DataFrame, values: pd.DataFrame, cols) -> pd.DataFrame:
    """
    Spread each polygon's totals evenly over the cells it owns. `cells` is polygons_to_cells() output (id, cell_key); `values` has
    an `id` column and the numeric `cols`. Returns one row per cell with the summed share of each column. The sum over all cells
    of a column equals the sum of its input for every polygon that owns at least one cell.
    """
    n = cells.groupby("id")["cell_key"].transform("size").to_numpy()
    d = cells.merge(values[["id", *cols]], on="id", how="left")
    for c in cols:
        d[c] = d[c].fillna(0).to_numpy() / n
    return d.groupby("cell_key", as_index=False)[list(cols)].sum()


def worst_band_by_cell(cells: pd.DataFrame, band_col: str = "band") -> pd.DataFrame:
    """The highest band rank in each cell from a long table (cell_key, band): where bands overlap, the worse one wins."""
    d = cells.copy()
    d["rank"] = d[band_col].astype(int) if pd.api.types.is_numeric_dtype(d[band_col]) else d[band_col].map(band_rank)
    return d.groupby("cell_key", as_index=False)["rank"].max()


def aggregate_to_areas(grid: pd.DataFrame, area_cells: pd.DataFrame, sum_cols, by=None) -> pd.DataFrame:
    """
    Add the grid's `sum_cols` up to each area. `area_cells` is polygons_to_cells() output for the areas (id, cell_key). With `by`
    (a grid column such as a band rank) the sums are split by its values: one row per area and value.
    """
    d = area_cells.merge(grid, on="cell_key", how="left")
    keys = ["id"] + ([by] if by else [])
    out = d.groupby(keys, dropna=False)[list(sum_cols)].sum().reset_index()
    out["cells"] = d.groupby(keys, dropna=False).size().to_numpy()
    return out


# -----------------------------------------------------------------------------
# Overlapping areas. Flood alert areas contain flood warning areas, and warning areas can overlap each other, so adding polygon totals
# counts people twice. The cell is the unit of truth: a person lives in one cell, so
#   - a total over many areas is taken over the DISTINCT cells they cover (union_total), never as a sum of area totals;
#   - where an additive per-area figure is needed, each cell is given to one primary area by a stated precedence (primary_area), or split
#     equally among the areas that cover it, so the parts add up to the whole;
#   - an area's own total (gross) is still reported, labelled as possibly overlapping.
# -----------------------------------------------------------------------------

def covered_cells(area_cells: pd.DataFrame, ids=None) -> np.ndarray:
    """The distinct cells covered by any of the areas (all of them, or just `ids`)."""
    d = area_cells if ids is None else area_cells[area_cells["id"].isin(list(ids))]
    return np.sort(d["cell_key"].unique())


def union_total(grid: pd.DataFrame, area_cells: pd.DataFrame, sum_cols, ids=None) -> pd.Series:
    """The grid's `sum_cols` over the distinct cells covered by any of the areas: each person, property and site counted once."""
    k = covered_cells(area_cells, ids)
    return grid[grid["cell_key"].isin(k)][list(sum_cols)].sum()


def primary_area(area_cells: pd.DataFrame, meta: pd.DataFrame, order=None) -> pd.DataFrame:
    """
    One owning area for each cell, with n_areas (how many cover it). `meta` has an `id` and the columns named in `order`, a list of
    (column, ascending) pairs, best first. The default precedence is: in force now first, then the higher severity (a rank: 3 severe, 2 warning,
    1 alert; the feed's severity_level runs the other way, 1 severe to 3 alert, so use 4 minus the level), then a warning area before an alert area, then the smaller area (the more specific), then the lowest id so that the
    answer is always the same. Columns missing from `meta` are skipped.
    """
    order = order or [("in_force", False), ("severity", False), ("is_warning", False), ("area_km2", True), ("id", True)]
    d = area_cells.merge(meta, on="id", how="left")
    cols = [(c, a) for c, a in order if c in d.columns]
    d = d.sort_values([c for c, _ in cols], ascending=[a for _, a in cols], kind="mergesort")
    n = d.groupby("cell_key").size().rename("n_areas")
    first = d.drop_duplicates("cell_key", keep="first")[["cell_key", "id"]].rename(columns={"id": "primary_id"})
    return first.merge(n, left_on="cell_key", right_index=True)


def overlap_depth(area_cells: pd.DataFrame, grid: pd.DataFrame = None, sum_cols=()) -> pd.DataFrame:
    """
    How many cells are covered by one area, two, three ...: n_areas, cells, and the grid's `sum_cols` in those cells. Run it on the warning
    areas alone (filter `area_cells` to warnings first) to see how big the warning-over-warning problem is before choosing a rule. A row with
    n_areas 1 is clean; every other row is people counted more than once if polygon totals are added.
    """
    n = area_cells.groupby("cell_key").size().rename("n_areas").reset_index()
    if grid is not None and len(sum_cols):
        n = n.merge(grid[["cell_key", *sum_cols]], on="cell_key", how="left")
    out = n.groupby("n_areas").agg(cells=("cell_key", "size"), **{c: (c, "sum") for c in sum_cols}).reset_index()
    return out


def pair_overlaps(area_cells: pd.DataFrame, grid: pd.DataFrame, sum_cols, min_cells: int = 1) -> pd.DataFrame:
    """
    Every pair of areas that share cells: id_a, id_b, shared_cells, the `sum_cols` in the shared cells, and what share of each area's cells
    is shared (share_of_a, share_of_b). Sorted with the largest overlaps first. A share near 1 for both means two codes drawn over the
    same ground.
    """
    cells = area_cells.drop_duplicates(["id", "cell_key"])
    size = cells.groupby("id").size()
    pairs = cells.merge(cells, on="cell_key", suffixes=("_a", "_b"))
    pairs = pairs[pairs["id_a"] < pairs["id_b"]]
    if pairs.empty:
        return pd.DataFrame(columns=["id_a", "id_b", "shared_cells", *sum_cols, "share_of_a", "share_of_b"])
    pairs = pairs.merge(grid[["cell_key", *sum_cols]], on="cell_key", how="left")
    out = pairs.groupby(["id_a", "id_b"]).agg(shared_cells=("cell_key", "size"), **{c: (c, "sum") for c in sum_cols}).reset_index()
    out = out[out["shared_cells"] >= min_cells]
    out["share_of_a"] = (out["shared_cells"] / out["id_a"].map(size)).round(4)
    out["share_of_b"] = (out["shared_cells"] / out["id_b"].map(size)).round(4)
    return out.sort_values("shared_cells", ascending=False).reset_index(drop=True)


def area_overlap_stats(grid: pd.DataFrame, area_cells: pd.DataFrame, meta: pd.DataFrame, sum_cols, order=None) -> pd.DataFrame:
    """
    For each area and each of `sum_cols`: <col>_gross (all its cells, so areas that overlap double count), <col>_unique (only the cells it is the
    primary area of: these add up across areas to the union total), <col>_split (each cell shared equally among the areas that cover it:
    these also add up), and <col>_shared (gross minus unique, what it shares with a higher-precedence area).
    """
    prim = primary_area(area_cells, meta, order)
    d = area_cells.merge(grid[["cell_key", *sum_cols]], on="cell_key", how="left").merge(prim, on="cell_key")
    d["is_primary"] = d["id"] == d["primary_id"]
    out = pd.DataFrame({"id": sorted(area_cells["id"].unique())}).set_index("id")
    for c in sum_cols:
        out[f"{c}_gross"] = d.groupby("id")[c].sum()
        out[f"{c}_unique"] = d[d["is_primary"]].groupby("id")[c].sum()
        out[f"{c}_split"] = (d[c] / d["n_areas"]).groupby(d["id"]).sum()
    out = out.fillna(0.0)
    for c in sum_cols:
        out[f"{c}_shared"] = out[f"{c}_gross"] - out[f"{c}_unique"]
    out["cells"] = d.groupby("id").size()
    out["cells_unique"] = d[d["is_primary"]].groupby("id").size()
    return out.fillna(0).reset_index()


def risk_score(grid: pd.DataFrame, weights: dict, crit_cap: int = 3) -> pd.Series:
    """
    The composite score, 0 to 100, for each cell. The hazard (the worse of the rivers and sea band and the surface water band, 0 to 4)
    scales everything: no hazard, no score. Inside it three things add, weighted by `weights` (pop, vulnerability, critical; they are
    normalised to sum to one):
      pop             the cell's population as a share of the busiest populated cell's percentile (0 to 1)
      vulnerability   the deprivation decile turned round, so decile 1 (most deprived) is 1 and decile 10 is 0.1
      critical        critical sites in the cell, capped at crit_cap, as a share of the cap
    Columns used: haz_rs, haz_sw, population, imd_decile, critical_sites. A missing column counts as zero.
    """
    def col(name):
        return pd.to_numeric(grid[name], errors="coerce").fillna(0) if name in grid else pd.Series(0.0, index=grid.index)
    w = {k: float(weights.get(k, 0)) for k in ("pop", "vulnerability", "critical")}
    total = sum(w.values()) or 1.0
    haz = np.maximum(col("haz_rs"), col("haz_sw")).clip(0, 4) / 4.0
    pop = col("population")
    pop_pct = pop.rank(pct=True).where(pop > 0, 0.0)
    dec = col("imd_decile")
    vuln = ((11 - dec) / 10.0).where(dec > 0, 0.0)
    crit = (col("critical_sites").clip(0, crit_cap) / float(crit_cap))
    inner = (w["pop"] * pop_pct + w["vulnerability"] * vuln + w["critical"] * crit) / total
    return (100.0 * haz * inner).round(2)
