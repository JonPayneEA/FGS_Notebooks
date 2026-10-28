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
