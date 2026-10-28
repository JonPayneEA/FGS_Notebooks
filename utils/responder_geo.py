# =============================================================================
# utils/responder_geo.py
# Helpers for job 32 (responder geographies): find the code-to-name reference files in the NSUL reference folder, read a code and a name out
# of them, and roll a local authority up to a geography it belongs to (a fire and rescue authority or a combined authority) through a lookup.
# Pure pandas, so it is tested without Spark.
#
# SKELETON: this covers the geographies NSUL carries (local authority district, county or unitary authority, ward, constituency, police force area,
# region). Fire and rescue authorities and combined authorities are not in NSUL; they arrive through a lookup file (district to geography) that
# someone supplies, and rollup_through_lookup then does the arithmetic. Local resilience forums are out of scope for now.
# =============================================================================

import re

import pandas as pd

# the address point column that holds each geography's code, and the words that find its reference file
GEOGRAPHIES = {
    "local_authority_district": {"points_col": "lad", "keywords": ["LAUA", "LAD", "Local Authority"]},
    "county_or_unitary": {"points_col": "ctyua", "keywords": ["CTY", "County"]},
    "ward": {"points_col": "ward", "keywords": ["WD", "Ward"]},
    "constituency": {"points_col": "pcon", "keywords": ["PCON", "Parliamentary Constituency", "Constituenc"]},
    "police_force_area": {"points_col": "pfa", "keywords": ["PFA", "Police"]},
    "region": {"points_col": "rgn", "keywords": ["RGN", "Region"]},
}
# not in NSUL: supplied by a lookup file of (district code, geography code, geography name)
LOOKUP_GEOGRAPHIES = ("fire_and_rescue_authority", "combined_authority")


def _as_at(name: str) -> int:
    """'... as at 05_25.csv' -> 202505 (for choosing the latest of several); 0 if there is no date."""
    m = re.search(r"as at (\d{2})_(\d{2})", name, flags=re.I)
    return int(m.group(2)) * 100 + int(m.group(1)) if m else 0


def find_reference_file(file_names, keywords):
    """The best-matching file name for a geography, or None. A name that starts with a keyword beats one that only contains it; of equal matches
    the latest 'as at' date wins. England and Wales or England files are preferred to Great Britain or UK ones only by date, not by name."""
    best, best_key = None, None
    for n in file_names:
        low = n.lower()
        if not low.endswith(".csv"):
            continue
        score = 0
        for k in keywords:
            if low.startswith(k.lower() + " "):
                score = max(score, 3)
            elif re.search(r"\b" + re.escape(k.lower()), low):
                score = max(score, 1)
        if score and (best_key is None or (score, _as_at(n)) > best_key):
            best, best_key = n, (score, _as_at(n))
    return best


def parse_code_name(df: pd.DataFrame):
    """From a reference table, the (code, name) frame and the two column names used. The code column ends CD (or is called code), the name column
    ends NM (not NMW, the Welsh name). Returns (frame with columns code and name, code column, name column), or (None, None, None) if they are not found."""
    cols = [str(c) for c in df.columns]
    code = next((c for c in cols if re.search(r"CD$", c, re.I)), None) or next((c for c in cols if c.lower() == "code"), None)
    name = next((c for c in cols if re.search(r"NM$", c, re.I)), None) or next((c for c in cols if c.lower() == "name"), None)
    if code is None or name is None:
        return None, None, None
    out = pd.DataFrame({"code": df[code].astype(str).str.strip(), "name": df[name].astype(str).str.strip()}).drop_duplicates("code")
    return out.reset_index(drop=True), code, name


def rollup_through_lookup(by_district: pd.DataFrame, lookup: pd.DataFrame, value_cols) -> pd.DataFrame:
    """Add up district figures into a geography a lookup puts them in. `by_district` has `code` (district) and the numeric `value_cols`; `lookup`
    has district_code, geography, geography_code, geography_name. A district that is in no row of the lookup is reported by the caller from the
    returned frame's absence. Whole districts are assumed to belong to one geography each: where a district is split between two (a fire authority
    boundary running through a district), the figures are counted in both, so the sums over geographies can exceed the national total."""
    m = lookup.merge(by_district.rename(columns={"code": "district_code"}), on="district_code", how="inner")
    g = m.groupby(["geography", "geography_code", "geography_name"], as_index=False)[list(value_cols)].sum()
    n = m.groupby(["geography", "geography_code", "geography_name"]).size().rename("districts").reset_index()
    return g.merge(n, on=["geography", "geography_code", "geography_name"])


def read_csv_text(path_or_buffer, **kw) -> pd.DataFrame:
    """Read a reference CSV as text. UTF-8 with or without a byte order mark first (the ONS files carry one, which otherwise shows up as 'i>?' in the
    first column name and mangles accents such as Ynys Mon with a circumflex), then Latin-1 if the file is not UTF-8."""
    for enc in ("utf-8-sig", "latin-1"):
        try:
            if hasattr(path_or_buffer, "seek"):
                path_or_buffer.seek(0)
            return pd.read_csv(path_or_buffer, dtype=str, encoding=enc, **kw)
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("utf-8-sig/latin-1", b"", 0, 1, "could not decode the file")


def parse_district_lookup(df: pd.DataFrame, geography: str) -> pd.DataFrame:
    """A raw ONS lookup of local authority districts to a bigger geography (fire and rescue authority, combined authority) in the layout the
    roll-up needs: district_code, geography, geography_code, geography_name. The district column is the one named LAD..CD, the geography code is
    another column ending CD, and its name is the matching column ending NM (a Welsh NMW column is ignored). A district with no geography
    (blank code) is dropped: it is in none."""
    cols = [str(c).replace("\ufeff", "").strip() for c in df.columns]
    d = df.copy()
    d.columns = cols
    lad = next((c for c in cols if re.fullmatch(r"LAD\d*CD", c, re.I)), None)
    other_cd = [c for c in cols if re.search(r"CD$", c, re.I) and c != lad]
    if lad is None or not other_cd:
        raise ValueError(f"Expected a district column (LAD..CD) and a geography code column (..CD); found {cols[:8]}")
    code_col = other_cd[0]
    stem = re.sub(r"\d*CD$", "", code_col, flags=re.I)
    name_col = next((c for c in cols if re.fullmatch(stem + r"\d*NM", c, re.I)), None)
    if name_col is None:
        raise ValueError(f"No name column for {code_col}: expected {stem}..NM among {cols[:8]}")
    out = pd.DataFrame({"district_code": d[lad].astype(str).str.strip(), "geography": geography,
                        "geography_code": d[code_col].astype(str).str.strip(), "geography_name": d[name_col].astype(str).str.strip()})
    return out[out["geography_code"].notna() & ~out["geography_code"].isin(["", "nan", "None"])].drop_duplicates(["district_code", "geography_code"]).reset_index(drop=True)


def read_geojson_features(text: str):
    """A GeoJSON FeatureCollection as (a frame of the properties plus a `geometry` column of GeoJSON text per feature, the EPSG code). The code is 4326
    unless the file names another coordinate system (an old-style 'crs' member, as in files exported in British National Grid, 27700)."""
    import json
    j = json.loads(text)
    if j.get("type") != "FeatureCollection":
        raise ValueError("Expected a GeoJSON FeatureCollection.")
    epsg = 4326
    name = str(((j.get("crs") or {}).get("properties") or {}).get("name", ""))
    m = re.search(r"(\d{4,5})\s*$", name)
    if m:
        epsg = int(m.group(1))
    rows = []
    for f in j.get("features", []):
        r = dict(f.get("properties") or {})
        r["geometry"] = json.dumps(f["geometry"]) if f.get("geometry") else None
        rows.append(r)
    return pd.DataFrame(rows), epsg


def assign_points_to_polygons(x, y, polys, max_nearest_m: float = 2000.0):
    """For each point, the index of the polygon it lies in, or, if it lies in none (a coastal point just outside a boundary clipped to the coast),
    the nearest polygon within `max_nearest_m`; -1 if there is none. A point in two overlapping polygons takes the lower index. Returns an int array
    and the number of points placed by the nearest fallback.
    Built for boundaries with very many vertices (an authority boundary at full resolution): each polygon is prepared once, and only the points inside
    its bounding box that no earlier polygon has taken are tested against it."""
    import numpy as np
    import shapely
    x, y = np.asarray(x, dtype="float64"), np.asarray(y, dtype="float64")
    out = np.full(len(x), -1, dtype="int64")
    for k, poly in enumerate(polys):
        minx, miny, maxx, maxy = poly.bounds
        cand = np.flatnonzero((out < 0) & (x >= minx) & (x <= maxx) & (y >= miny) & (y <= maxy))
        if len(cand):
            inside = shapely.contains_xy(poly, x[cand], y[cand])
            out[cand[inside]] = k
    missing = np.flatnonzero(out < 0)
    nearest = 0
    if len(missing):
        tree = shapely.STRtree(np.asarray(polys, dtype=object))
        ni, dist = tree.query_nearest(shapely.points(x[missing], y[missing]), return_distance=True, all_matches=False, max_distance=max_nearest_m)
        out[missing[ni[0]]] = ni[1]
        nearest = int(len(ni[0]))
    return out, nearest
