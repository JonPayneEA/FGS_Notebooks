# =============================================================================
# utils/area_exposure.py
# Exposure of flood warning and alert areas, counted from the address points (plan: docs/EXPOSURE_AND_RISK_GRID_PLAN.md). Pure numpy, pandas,
# shapely and pyproj, so it is tested without Spark. The notebook (jobs/35_build_warning_area_exposure) feeds it one 50 km tile of points at a time.
#
# WHY POINTS: a point is in an area or it is not (no 50 m cell edge error, which matters for small areas), and the band is looked up at the
# point. Areas overlap, so a person can be in several: warnings within warnings and alerts within alerts. Each KIND (flood_warning,
# flood_alert) is counted separately, and within a kind a point has a DEPTH, the number of areas of that kind that cover it.
#
# FOR EACH AREA, FOUR VIEWS OF A MEASURE (people, households, addresses, over-65s, people in the High or Medium band):
#   gross    everyone in the area. Summing gross over areas counts a person in two overlapping areas twice, so it is for one area, not for a total.
#   unique   those covered by this area alone (depth 1). Unique figures can be added across areas without double counting.
#   split    each person shared equally among the areas that cover them (1 / depth). Split figures add up to the true total.
#   shared   gross minus unique: who else is also covered.
# And per kind: the distinct total covered by any area (the union), and the people the areas miss.
#
#   point_values        the measures per point: people, households, over-65s, deprivation, and by rivers and sea band and Flood Zone
#   to_bng_polygons     GeoJSON text (WGS84) -> valid polygons in British National Grid, with the reason for any dropped
#   tile_stats          one tile's points against all areas: per-area sums, overlapping pairs, area by constituency, coverage, constituency totals
#   finish_areas        add the derived columns (mean deprivation, shared, shares of the area's people)
#   site_stats          critical sites per area, by group, and in the higher bands
# =============================================================================

import numpy as np
import pandas as pd
import shapely

RS_BANDS = {1: "very_low", 2: "low", 3: "medium", 4: "high"}
BASE = ["addresses", "people", "households", "over65"]
BAND_PEOPLE = [f"people_rs_{b}" for b in RS_BANDS.values()]
BAND_HOUSEHOLDS = [f"households_rs_{b}" for b in RS_BANDS.values()]
FZ_PEOPLE = ["people_fz2", "people_fz3", "people_fz3_sea", "people_fz3_river", "people_fz3_both"]
OTHER = ["people_rs_high_medium", "households_rs_high_medium", "people_imd_1_2", "imd_w", "imd_wsum"]
VALUE_COLS = BASE + BAND_PEOPLE + BAND_HOUSEHOLDS + FZ_PEOPLE + OTHER
# the measures that also get a unique and a split view
UNIQUE_SPLIT = ["addresses", "people", "households", "over65", "people_rs_high_medium"]
KINDS = ("flood_warning", "flood_alert")
SOURCE_BY_CODE = {"F": "fluvial", "C": "coastal", "T": "tidal", "G": "groundwater", "B": "fluvial and coastal"}


def point_values(points: pd.DataFrame) -> pd.DataFrame:
    """The measures carried by each address point, one column each (float). `points` has people, households, over65, imd_decile, rofrs_rank and
    the fz_*_rank columns. `addresses` is 1 for a real address and 0 for an output area centre point (which carries people but is not an address)."""
    d = pd.DataFrame(index=points.index)
    people = pd.to_numeric(points["people"], errors="coerce").fillna(0.0)
    hh = pd.to_numeric(points["households"], errors="coerce").fillna(0.0)
    d["addresses"] = (points["point_source"] == "nsul").astype(float) if "point_source" in points else 1.0
    d["people"], d["households"] = people, hh
    d["over65"] = pd.to_numeric(points["over65"], errors="coerce").fillna(0.0)
    rank = pd.to_numeric(points["rofrs_rank"], errors="coerce").fillna(0)
    for r, name in RS_BANDS.items():
        d[f"people_rs_{name}"] = people.where(rank == r, 0.0)
        d[f"households_rs_{name}"] = hh.where(rank == r, 0.0)
    d["people_rs_high_medium"] = people.where(rank >= 3, 0.0)
    d["households_rs_high_medium"] = hh.where(rank >= 3, 0.0)
    fz = pd.to_numeric(points["fz_rank"], errors="coerce").fillna(0)
    d["people_fz2"] = people.where(fz == 1, 0.0)
    d["people_fz3"] = people.where(fz == 2, 0.0)
    for col, name in (("fz_sea_rank", "people_fz3_sea"), ("fz_river_rank", "people_fz3_river"), ("fz_river_and_sea_rank", "people_fz3_both")):
        d[name] = people.where(pd.to_numeric(points[col], errors="coerce").fillna(0) == 2, 0.0) if col in points else 0.0
    dec = pd.to_numeric(points["imd_decile"], errors="coerce")
    d["people_imd_1_2"] = people.where(dec <= 2, 0.0)
    d["imd_w"] = np.where(dec.notna(), people * dec.fillna(0), 0.0)
    d["imd_wsum"] = np.where(dec.notna(), people, 0.0)
    return d[VALUE_COLS]


def to_bng_polygons(geojson_texts):
    """GeoJSON text in WGS84 -> (polygons in British National Grid, status) per input. status is 'ok', 'no geometry', 'not a polygon' (a line or point:
    it has no area to count addresses in) or 'empty after repair'. Invalid polygons are repaired, and only the polygonal part is kept."""
    from pyproj import Transformer
    tr = Transformer.from_crs(4326, 27700, always_xy=True)
    out, status = [], []
    for t in geojson_texts:
        if t is None or not isinstance(t, str) or not t.strip():
            out.append(None); status.append("no geometry"); continue
        try:
            g = shapely.from_geojson(t)
        except Exception:
            out.append(None); status.append("no geometry"); continue
        if g is None or g.is_empty:
            out.append(None); status.append("no geometry"); continue
        if g.geom_type not in ("Polygon", "MultiPolygon"):
            out.append(None); status.append("not a polygon"); continue
        g = shapely.transform(g, lambda c: np.column_stack(tr.transform(c[:, 0], c[:, 1])))
        if not g.is_valid:
            g = shapely.make_valid(g)
        parts = [p for p in shapely.get_parts(g) if p.geom_type in ("Polygon", "MultiPolygon") and not p.is_empty]
        if not parts:
            out.append(None); status.append("empty after repair"); continue
        out.append(shapely.union_all(parts) if len(parts) > 1 else parts[0]); status.append("ok")
    return out, status


def _sum_by(frame: pd.DataFrame, by, cols) -> pd.DataFrame:
    return frame.groupby(by, sort=False)[list(cols)].sum()


def tile_stats(points: pd.DataFrame, geoms, area_ids, kinds, min_pair_people: float = 0.0) -> dict:
    """One tile's points against all areas. `geoms` is a list of polygons (British National Grid), `area_ids` and `kinds` are parallel to it.
    Returns a dict of frames, each a sum that can be added across tiles (concatenate, then groupby the key columns and sum):
      area      (kind, area_id): gross VALUE_COLS, then <measure>_unique and <measure>_split for UNIQUE_SPLIT
      pairs     (kind, area_a, area_b): shared addresses and people (a < b)
      area_pcon (kind, area_id, pcon): addresses, people, households, over65, people_rs_high_medium, people_fz3
      coverage  (kind): the distinct total covered by any area of the kind, and 'all' (every point), for the gap
      pcon      (pcon): all points, and those covered by a warning and by an alert area, for the gap by constituency
    """
    vals = point_values(points)
    n = len(points)
    pcon = points["pcon"].astype("object").where(points["pcon"].notna(), "") if "pcon" in points else pd.Series([""] * n, index=points.index)
    pcon = pcon.to_numpy()
    tree = shapely.STRtree(np.asarray(geoms, dtype=object))
    pt = shapely.points(points["easting"].to_numpy(dtype="float64"), points["northing"].to_numpy(dtype="float64"))
    pi, ai = tree.query(pt, predicate="intersects")
    area_ids, kinds = np.asarray(area_ids, dtype=object), np.asarray(kinds, dtype=object)
    arr = vals.to_numpy()

    pairs = pd.DataFrame({"point": pi, "area": ai})
    pairs["kind"], pairs["area_id"] = kinds[ai], area_ids[ai]
    pairs["depth"] = pairs.groupby(["kind", "point"])["area"].transform("size").to_numpy()
    D = pd.DataFrame(arr[pi], columns=VALUE_COLS)
    D["kind"], D["area_id"] = pairs["kind"].to_numpy(), pairs["area_id"].to_numpy()

    gross = _sum_by(D, ["kind", "area_id"], VALUE_COLS)
    one = pairs["depth"].to_numpy() == 1
    uniq = _sum_by(D[one], ["kind", "area_id"], UNIQUE_SPLIT).add_suffix("_unique")
    inv = 1.0 / pairs["depth"].to_numpy()
    S = D[UNIQUE_SPLIT].mul(inv, axis=0)
    S["kind"], S["area_id"] = D["kind"].to_numpy(), D["area_id"].to_numpy()
    split = _sum_by(S, ["kind", "area_id"], UNIQUE_SPLIT).add_suffix("_split")
    area = gross.join(uniq, how="left").join(split, how="left").fillna(0.0)

    # overlapping pairs of areas of the same kind, with what they share
    ov = pairs[pairs["depth"] > 1][["kind", "point", "area_id"]]
    if len(ov):
        m = ov.merge(ov, on=["kind", "point"], suffixes=("_a", "_b"))
        m = m[m["area_id_a"] < m["area_id_b"]]
        m["addresses"] = arr[m["point"].to_numpy(), VALUE_COLS.index("addresses")]
        m["people"] = arr[m["point"].to_numpy(), VALUE_COLS.index("people")]
        prs = m.groupby(["kind", "area_id_a", "area_id_b"], sort=False)[["addresses", "people"]].sum()
    else:
        prs = pd.DataFrame(columns=["addresses", "people"], index=pd.MultiIndex.from_arrays([[], [], []], names=["kind", "area_id_a", "area_id_b"]))

    # each area split by constituency
    ap_cols = ["addresses", "people", "households", "over65", "people_rs_high_medium", "people_fz3"]
    AP = pd.DataFrame(vals[ap_cols].to_numpy()[pi], columns=ap_cols)
    AP["kind"], AP["area_id"], AP["pcon"] = pairs["kind"].to_numpy(), pairs["area_id"].to_numpy(), pcon[pi]
    area_pcon = _sum_by(AP, ["kind", "area_id", "pcon"], ap_cols)

    # the distinct total covered by any area of a kind, against every point
    cov_cols = ["addresses", "people", "households", "over65", "people_rs_high_medium", "households_rs_high_medium", "people_fz3"]
    cov_rows = {"all": vals[cov_cols].sum()}
    covered = {}
    for k in KINDS:
        idx = np.unique(pi[kinds[ai] == k])
        covered[k] = idx
        cov_rows[k] = vals[cov_cols].iloc[idx].sum()
    coverage = pd.DataFrame(cov_rows).T
    coverage.index.name = "kind"

    # by constituency: all points, and those a warning or an alert area covers
    PC = vals[cov_cols].copy()
    PC["pcon"] = pcon
    for k, short in (("flood_warning", "warning"), ("flood_alert", "alert")):
        flag = np.zeros(n, dtype=bool)
        flag[covered[k]] = True
        for c in ("people", "people_rs_high_medium", "households_rs_high_medium", "people_fz3"):
            PC[f"{c}_in_{short}_area"] = vals[c].where(flag, 0.0)
    pc_cols = [c for c in PC.columns if c != "pcon"]
    pcon_tot = _sum_by(PC, "pcon", pc_cols)
    return {"area": area, "pairs": prs, "area_pcon": area_pcon, "coverage": coverage, "pcon": pcon_tot}


def combine_tiles(parts, key_levels) -> pd.DataFrame:
    """Add up one kind of tile_stats frame across tiles (they are all sums)."""
    parts = [p for p in parts if p is not None and len(p)]
    if not parts:
        return pd.DataFrame()
    d = pd.concat(parts)
    return d.groupby(level=list(range(d.index.nlevels)), sort=False).sum() if d.index.nlevels > 1 else d.groupby(level=0, sort=False).sum()


def finish_areas(area: pd.DataFrame, meta: pd.DataFrame) -> pd.DataFrame:
    """Join the area details (`meta`: kind, area_id and anything else) to the summed figures and add the derived columns: mean deprivation decile,
    people shared with another area, the share of the area's people in a High or Medium band, and addresses and people that are zero."""
    a = area.reset_index()
    d = meta.merge(a, on=["kind", "area_id"], how="left")
    for c in [c for c in d.columns if c in VALUE_COLS or c.endswith("_unique") or c.endswith("_split")]:
        d[c] = d[c].fillna(0.0)
    d["imd_mean_decile"] = (d["imd_w"] / d["imd_wsum"].where(d["imd_wsum"] > 0)).round(2)
    d["people_shared"] = d["people"] - d["people_unique"]
    d["share_people_shared"] = (d["people_shared"] / d["people"].where(d["people"] > 0)).round(4)
    d["share_people_high_medium"] = (d["people_rs_high_medium"] / d["people"].where(d["people"] > 0)).round(4)
    d["share_people_imd_1_2"] = (d["people_imd_1_2"] / d["people"].where(d["people"] > 0)).round(4)
    return d.drop(columns=["imd_w", "imd_wsum"])


def site_stats(sites: pd.DataFrame, geoms, area_ids, kinds) -> pd.DataFrame:
    """Critical sites per area (kind, area_id): all, by group, and in the rivers and sea band High or Medium and in Flood Zone 3.
    `sites` has easting, northing, site_group, rofrs_rank, fz_rank."""
    cols = ["sites", "sites_health", "sites_education", "sites_emergency", "sites_utilities", "sites_rs_high_medium", "sites_fz3"]
    if sites is None or not len(sites):
        return pd.DataFrame(columns=cols, index=pd.MultiIndex.from_arrays([[], []], names=["kind", "area_id"]))
    tree = shapely.STRtree(np.asarray(geoms, dtype=object))
    pi, ai = tree.query(shapely.points(sites["easting"].to_numpy(dtype="float64"), sites["northing"].to_numpy(dtype="float64")), predicate="intersects")
    g = sites["site_group"].astype(str).str.lower().to_numpy()
    f = pd.DataFrame({"sites": 1.0}, index=range(len(sites)))
    for grp in ("health", "education", "emergency", "utilities"):
        f[f"sites_{grp}"] = (g == grp).astype(float)
    f["sites_rs_high_medium"] = (pd.to_numeric(sites["rofrs_rank"], errors="coerce").fillna(0).to_numpy() >= 3).astype(float)
    f["sites_fz3"] = (pd.to_numeric(sites["fz_rank"], errors="coerce").fillna(0).to_numpy() == 2).astype(float)
    D = f.iloc[pi].reset_index(drop=True)
    D["kind"], D["area_id"] = np.asarray(kinds, dtype=object)[ai], np.asarray(area_ids, dtype=object)[ai]
    return _sum_by(D, ["kind", "area_id"], cols)


def coverage_with_gap(cov_sum: pd.DataFrame) -> pd.DataFrame:
    """The summed coverage frame (index kind: 'all', 'flood_warning', 'flood_alert') as rows with the gap added: the people, and the people in the
    rivers and sea High or Medium band, that no area of the kind covers."""
    c = cov_sum.reset_index()
    allr = c[c["kind"] == "all"].iloc[0]
    c["people_not_in_area"] = np.where(c["kind"] == "all", 0.0, allr["people"] - c["people"])
    c["people_high_medium_not_in_area"] = np.where(c["kind"] == "all", 0.0, allr["people_rs_high_medium"] - c["people_rs_high_medium"])
    return c


def constituency_with_gap(pcon_sum: pd.DataFrame) -> pd.DataFrame:
    """Per constituency code: the summed columns with the High or Medium people that no warning (or alert) area covers."""
    c = pcon_sum.reset_index().rename(columns={"pcon": "constituency_code"})
    for short in ("warning", "alert"):
        c[f"people_high_medium_not_in_{short}_area"] = c["people_rs_high_medium"] - c[f"people_rs_high_medium_in_{short}_area"]
    return c
