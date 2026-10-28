# =============================================================================
# utils/grid_build.py
# The 100 m exposure grid, built by adding the address points up into cells and joining the flood band shares of each cell.
# Pure pandas and numpy, so it is tested without Spark. The notebook (jobs/34_build_exposure_grid) feeds it one 50 km tile at a time.
#
# A CELL carries three kinds of number, kept apart:
#   people            who is there: people, households, over-65s, addresses, deprivation (from the points)
#   exposure          who is in which band: people and households by rivers and sea band; people by Flood Zone, split coastal / fluvial
#   hazard            how much of the cell's AREA is in each band (from job 30): never "worst band wins" for a cell
# and then the critical sites in it, if the critical sites table exists (job 31).
#
# haz_rs is the hazard for the score: the area-weighted band, sum of (band rank x share of the cell in that band), 0 to 4. A cell with a
# quarter of its area High and the rest dry is 1.0, not 4.0, so one patch of water does not make a cell High. No surface water here.
#
#   points_to_cells      add address points up into cells: people, households, over-65s, deprivation, people and households by band
#   shares_wide          the long (source, band, cell_key, share) table of job 30 as one column per band
#   critical_to_cells    count critical sites per cell, by group and in the higher rivers and sea bands
#   build_grid           join them, add the hazard and the coordinates
#   add_score            the national population percentile and the composite score (needs every tile, so it runs once at the end)
# =============================================================================

import numpy as np
import pandas as pd

from .grid100 import cell_xy, risk_score

RS_BANDS = {1: "very_low", 2: "low", 3: "medium", 4: "high"}
# the point rank column -> the people column for people in Flood Zone 3 by mapping source (sea is coastal, river is fluvial)
FZ3_PEOPLE = {"fz_rank": "people_fz3", "fz_sea_rank": "people_fz3_sea", "fz_river_rank": "people_fz3_river", "fz_river_and_sea_rank": "people_fz3_both"}

# (job 30 source, band) -> the share column it becomes
SHARE_COLUMNS = {
    ("rofrs", "Very Low"): "share_rs_very_low", ("rofrs", "Low"): "share_rs_low", ("rofrs", "Medium"): "share_rs_medium", ("rofrs", "High"): "share_rs_high",
    ("flood_zones", "FZ2"): "share_fz2", ("flood_zones", "FZ3"): "share_fz3",
    ("flood_zones_sea", "FZ3"): "share_fz3_sea", ("flood_zones_river", "FZ3"): "share_fz3_river", ("flood_zones_river_and_sea", "FZ3"): "share_fz3_both",
}
CRITICAL_GROUPS = ("health", "education", "emergency", "utilities")
CRITICAL_COLUMNS = ("critical_sites", *(f"critical_{g}" for g in CRITICAL_GROUPS), "critical_in_rs_high_medium")


def points_to_cells(points: pd.DataFrame) -> pd.DataFrame:
    """One row per cell that has a point. Sums people, households, over-65s and addresses; the deprivation score and decile are
    people-weighted means (unweighted where a cell has no people); people and households are also split by rivers and sea band and
    people by Flood Zone (FZ3 split into sea, river and mapped together)."""
    d = points.copy()
    for c in ("people", "households", "over65"):
        d[c] = pd.to_numeric(d[c], errors="coerce").fillna(0.0)
    d["n_points"] = (d["point_source"] == "nsul").astype("int64") if "point_source" in d else 1
    w = d["people"]
    for c in ("imd_score", "imd_decile"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
        d[f"_w_{c}"] = np.where(d[c].notna(), w * d[c].fillna(0), 0.0)
        d[f"_ws_{c}"] = np.where(d[c].notna(), w, 0.0)
        d[f"_u_{c}"] = d[c].fillna(0)
        d[f"_n_{c}"] = d[c].notna().astype("int64")
    sums = {"people": "sum", "households": "sum", "over65": "sum", "n_points": "sum"}
    for r, name in RS_BANDS.items():
        d[f"people_rs_{name}"] = d["people"].where(d["rofrs_rank"] == r, 0.0)
        d[f"households_rs_{name}"] = d["households"].where(d["rofrs_rank"] == r, 0.0)
        sums[f"people_rs_{name}"] = sums[f"households_rs_{name}"] = "sum"
    for col, name in FZ3_PEOPLE.items():
        if col in d:
            d[name] = d["people"].where(d[col] == 2, 0.0)
            sums[name] = "sum"
    d["people_fz2"] = d["people"].where(d["fz_rank"] == 1, 0.0) if "fz_rank" in d else 0.0
    sums["people_fz2"] = "sum"
    for c in ("imd_score", "imd_decile"):
        for p in ("_w_", "_ws_", "_u_", "_n_"):
            sums[f"{p}{c}"] = "sum"
    g = d.groupby("cell_key").agg(sums).reset_index()
    for c in ("imd_score", "imd_decile"):
        weighted = g[f"_w_{c}"] / g[f"_ws_{c}"].where(g[f"_ws_{c}"] > 0)
        plain = g[f"_u_{c}"] / g[f"_n_{c}"].where(g[f"_n_{c}"] > 0)
        g[c] = weighted.fillna(plain)
        g = g.drop(columns=[f"{p}{c}" for p in ("_w_", "_ws_", "_u_", "_n_")])
    g["imd_decile"] = g["imd_decile"].round()
    return g


def shares_wide(long: pd.DataFrame) -> pd.DataFrame:
    """Job 30's long table (source, band, cell_key, share) as one row per cell with a column per band. Shares are of the cell's area."""
    d = long.copy()
    d["col"] = [SHARE_COLUMNS.get((s, b)) for s, b in zip(d["source"], d["band"])]
    d = d[d["col"].notna()]
    if d.empty:
        return pd.DataFrame({"cell_key": np.array([], dtype="int64")})
    w = d.pivot_table(index="cell_key", columns="col", values="share", aggfunc="sum", fill_value=0.0)
    w.columns.name = None
    return w.reset_index()


def critical_to_cells(sites: pd.DataFrame) -> pd.DataFrame:
    """Critical sites per cell: all, by group (health, education, emergency, utilities) and, where the sites carry a rivers and sea
    rank, those in Medium or High. `sites` has easting, northing, site_group and optionally rofrs_rank."""
    from .grid100 import cell_key
    d = sites.copy()
    d["cell_key"] = cell_key(d["easting"].to_numpy(), d["northing"].to_numpy())
    d["critical_sites"] = 1
    cols = ["critical_sites"]
    for g in CRITICAL_GROUPS:
        d[f"critical_{g}"] = (d["site_group"].astype(str).str.lower() == g).astype("int64")
        cols.append(f"critical_{g}")
    if "rofrs_rank" in d:
        d["critical_in_rs_high_medium"] = (pd.to_numeric(d["rofrs_rank"], errors="coerce").fillna(0) >= 3).astype("int64")
        cols.append("critical_in_rs_high_medium")
    return d.groupby("cell_key")[cols].sum().reset_index()


def build_grid(people: pd.DataFrame, shares: pd.DataFrame, critical: pd.DataFrame = None) -> pd.DataFrame:
    """Join people, band shares and critical sites on the cell key (a cell is kept if it has any of them). Adds the cell's south-west
    corner and haz_rs, the area-weighted rivers and sea band."""
    g = people.merge(shares, on="cell_key", how="outer")
    if critical is not None and len(critical):
        g = g.merge(critical, on="cell_key", how="outer")
    # the critical site columns always exist, zero where there are no sites (or no critical sites table yet), so the layout never changes
    for c in CRITICAL_COLUMNS:
        if c not in g:
            g[c] = 0.0
    for c in g.columns:
        if c not in ("cell_key", "imd_score", "imd_decile"):
            g[c] = g[c].fillna(0.0)
    haz = np.zeros(len(g))
    for r, name in RS_BANDS.items():
        col = f"share_rs_{name}"
        if col in g:
            haz += r * g[col].to_numpy()
    g["haz_rs"] = np.minimum(haz, 4.0)
    x, y = cell_xy(g["cell_key"].to_numpy())
    g.insert(1, "easting", x.astype("int64"))
    g.insert(2, "northing", y.astype("int64"))
    return g


def add_score(grid: pd.DataFrame, weights: dict, crit_cap: int = 3, include_surface: bool = False) -> pd.DataFrame:
    """Add pop_pct (the cell's population as a national percentile among populated cells) and risk_score (see grid100.risk_score). Run on
    the whole of England at once: a percentile over one tile would not be national."""
    g = grid.copy()
    pop = pd.to_numeric(g["people"], errors="coerce").fillna(0)
    g["pop_pct"] = pop.rank(pct=True).where(pop > 0, 0.0).round(4)
    # grid100.risk_score reads the population from a column named "population"; the grid calls it "people". Without this mapping the
    # population term silently counted as zero (a missing column is zero), and every flooded deprived cell scored the same.
    g["risk_score"] = risk_score(g.assign(population=pop), weights, crit_cap, include_surface)
    return g
