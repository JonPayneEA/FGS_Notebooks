# =============================================================================
# utils/critical_sites.py
# Critical sites (hospitals, GP surgeries, care homes, schools, emergency services, prisons, substations, water and sewage works) from the
# public sources we can reach, in one standard table. Pure pandas, so it is tested without Spark or the network.
# The notebook (jobs/31_build_critical_sites) fetches each source; AddressBase Premium classification would replace all of this and is the
# flagged major improvement (docs/EXPOSURE_AND_RISK_GRID_PLAN.md).
#
# THE STANDARD TABLE   site_id, site_group, site_type, name, easting, northing, postcode, source, source_id
#   site_group is one of health, education, emergency, utilities (health includes care homes; emergency includes prisons).
#
#   normalise_postcode   upper case, no spaces, so postcodes from different sources join
#   gias_sites           schools and colleges from Get Information About Schools
#   ods_sites            NHS sites from the ODS API, placed by postcode
#   classify_osm         the OpenStreetMap tags of one feature -> (group, type), or None if it is not a critical site
#   osm_sites            OpenStreetMap features (lon, lat, tags) -> standard table in British National Grid
#   dedupe_nearby        drop candidates of the same group within a radius of a site already held (the same place from two sources)
#   standardise          join the sources, give every site a unique id, fix the column order and types
# =============================================================================

import re

import numpy as np
import pandas as pd

GROUPS = ("health", "education", "emergency", "utilities")
COLUMNS = ["site_id", "site_group", "site_type", "name", "easting", "northing", "postcode", "source", "source_id"]
# the voltage at which a substation counts (below it is a pole transformer or a street cabinet: tens of thousands, and not critical)
SUBSTATION_MIN_VOLT = 66_000


def normalise_postcode(s):
    return pd.Series(s, dtype="object").astype(str).str.upper().str.replace(r"\s+", "", regex=True).replace({"NONE": "", "NAN": ""})


def _frame(rows):
    return pd.DataFrame(rows, columns=COLUMNS)


def gias_sites(df: pd.DataFrame) -> pd.DataFrame:
    """Open schools and colleges from a GIAS 'all establishment data' file (columns URN, EstablishmentName, TypeOfEstablishment (name),
    PhaseOfEducation (name), EstablishmentStatus (name), Postcode, Easting, Northing). Closed and not-yet-open schools are left out."""
    def col(*names):
        for n in names:
            if n in df.columns:
                return n
        raise KeyError(f"none of {names} in the GIAS columns {list(df.columns)[:15]}")
    status = col("EstablishmentStatus (name)", "EstablishmentStatus")
    d = df[df[status].astype(str).str.lower().str.startswith("open")].copy()
    e, n = pd.to_numeric(d[col("Easting")], errors="coerce"), pd.to_numeric(d[col("Northing")], errors="coerce")
    ok = (e > 0) & (n > 0)
    d, e, n = d[ok], e[ok], n[ok]
    phase = d[col("PhaseOfEducation (name)", "PhaseOfEducation")] if any(c in d for c in ("PhaseOfEducation (name)", "PhaseOfEducation")) else ""
    typ = d[col("TypeOfEstablishment (name)", "TypeOfEstablishment")] if any(c in d for c in ("TypeOfEstablishment (name)", "TypeOfEstablishment")) else ""
    site_type = pd.Series(phase, index=d.index).astype(str).where(lambda s: ~s.isin(["", "Not applicable", "nan"]), pd.Series(typ, index=d.index).astype(str))
    return _frame({
        "site_id": "", "site_group": "education", "site_type": site_type.to_numpy(), "name": d[col("EstablishmentName")].to_numpy(),
        "easting": e.to_numpy(dtype="float64"), "northing": n.to_numpy(dtype="float64"),
        "postcode": d[col("Postcode")].to_numpy() if "Postcode" in d else "", "source": "gias", "source_id": d[col("URN")].astype(str).to_numpy(),
    })


def ods_sites(orgs, role_label: str, postcodes: pd.DataFrame) -> pd.DataFrame:
    """NHS ODS organisations (a list of dicts with OrgId, Name, PostCode, Status) placed by postcode. `postcodes` has pcds, east1m, north1m.
    Organisations whose postcode is not found are dropped (counted by the caller from the lengths)."""
    d = pd.DataFrame(orgs)
    if d.empty:
        return _frame({})
    d["_pc"] = normalise_postcode(d.get("PostCode", pd.Series([""] * len(d)))).to_numpy()
    lut = postcodes.assign(_pc=normalise_postcode(postcodes["pcds"]).to_numpy())
    lut = lut.drop_duplicates("_pc")[["_pc", "east1m", "north1m"]]
    m = d.merge(lut, on="_pc", how="left")
    e, n = pd.to_numeric(m["east1m"], errors="coerce"), pd.to_numeric(m["north1m"], errors="coerce")
    ok = (e > 0) & (n > 0)
    m, e, n = m[ok], e[ok], n[ok]
    return _frame({
        "site_id": "", "site_group": "health", "site_type": role_label, "name": m["Name"].to_numpy(), "easting": e.to_numpy(dtype="float64"),
        "northing": n.to_numpy(dtype="float64"), "postcode": m["PostCode"].to_numpy(), "source": "nhs_ods", "source_id": m["OrgId"].astype(str).to_numpy(),
    })


def _max_voltage(v):
    """The highest voltage in an OSM voltage tag such as '132000;33000' (volts). 0 if there is none."""
    nums = [int(x) for x in re.findall(r"\d+", str(v or ""))]
    return max(nums) if nums else 0


def classify_osm(tags: dict, include_schools: bool = False):
    """(group, type) for an OpenStreetMap feature that is a critical site, else None."""
    t = {k: str(v).lower() for k, v in tags.items()}
    amenity, healthcare = t.get("amenity", ""), t.get("healthcare", "")
    if amenity == "hospital" or healthcare == "hospital":
        return "health", "hospital"
    if amenity == "clinic" or healthcare == "clinic":
        return "health", "clinic"
    if amenity == "doctors" or healthcare == "doctor":
        return "health", "gp surgery"
    if amenity == "nursing_home" or (amenity == "social_facility" and t.get("social_facility") in ("nursing_home", "residential_home", "assisted_living")):
        return "health", "care home"
    if amenity in ("college", "university"):
        return "education", amenity
    if amenity == "kindergarten":
        return "education", "nursery"
    if amenity == "school" and include_schools:
        return "education", "school"
    if amenity == "police":
        return "emergency", "police station"
    if amenity == "fire_station":
        return "emergency", "fire station"
    if amenity == "ambulance_station" or t.get("emergency") == "ambulance_station":
        return "emergency", "ambulance station"
    if t.get("emergency") in ("lifeboat_station", "coast_guard") or amenity == "coast_guard":
        return "emergency", "coastguard or lifeboat station"
    if amenity == "prison":
        return "emergency", "prison"
    if t.get("power") == "substation" and (t.get("substation") == "transmission" or _max_voltage(t.get("voltage")) >= SUBSTATION_MIN_VOLT):
        return "utilities", "substation"
    if t.get("power") == "plant":
        return "utilities", "power station"
    if t.get("man_made") == "wastewater_plant":
        return "utilities", "sewage works"
    if t.get("man_made") == "water_works":
        return "utilities", "water treatment works"
    return None


def osm_sites(features: pd.DataFrame, include_schools: bool = False) -> pd.DataFrame:
    """OpenStreetMap features (columns osm_id, kind, lon, lat, tags as dicts) -> the standard table, in British National Grid."""
    from pyproj import Transformer
    rows = []
    for r in features.itertuples(index=False):
        c = classify_osm(r.tags, include_schools)
        if c:
            rows.append((r.osm_id, r.kind, r.lon, r.lat, c[0], c[1], r.tags.get("name", "")))
    if not rows:
        return _frame({})
    d = pd.DataFrame(rows, columns=["osm_id", "kind", "lon", "lat", "site_group", "site_type", "name"])
    x, y = Transformer.from_crs(4326, 27700, always_xy=True).transform(d["lon"].to_numpy(), d["lat"].to_numpy())
    return _frame({
        "site_id": "", "site_group": d["site_group"].to_numpy(), "site_type": d["site_type"].to_numpy(), "name": d["name"].to_numpy(),
        "easting": np.asarray(x, dtype="float64"), "northing": np.asarray(y, dtype="float64"), "postcode": "", "source": "osm",
        "source_id": (d["kind"] + "/" + d["osm_id"].astype(str)).to_numpy(),
    })


def dedupe_nearby(keep: pd.DataFrame, candidates: pd.DataFrame, radius_m: float = 150.0) -> pd.DataFrame:
    """Drop the candidates that lie within `radius_m` of a site in `keep` with the same group (the same place seen in two sources)."""
    if candidates.empty or keep.empty:
        return candidates
    from scipy.spatial import cKDTree
    drop = np.zeros(len(candidates), dtype=bool)
    for g in candidates["site_group"].unique():
        k = keep[keep["site_group"] == g]
        sel = (candidates["site_group"] == g).to_numpy()
        if k.empty or not sel.any():
            continue
        tree = cKDTree(k[["easting", "northing"]].to_numpy())
        dist, _ = tree.query(candidates.loc[sel, ["easting", "northing"]].to_numpy(), k=1)
        drop[np.flatnonzero(sel)[dist <= radius_m]] = True
    return candidates[~drop]


def standardise(frames) -> pd.DataFrame:
    """Join the sources, give each site a unique id (source:source_id, made unique if needed), and fix the columns."""
    frames = [f for f in frames if f is not None and len(f)]
    if not frames:
        return _frame({})
    d = pd.concat(frames, ignore_index=True)
    d["site_id"] = d["source"].astype(str) + ":" + d["source_id"].astype(str)
    dup = d.groupby("site_id").cumcount()
    d["site_id"] = np.where(dup > 0, d["site_id"] + "#" + dup.astype(str), d["site_id"])
    d["name"] = d["name"].fillna("").astype(str)
    d["postcode"] = d["postcode"].fillna("").astype(str)
    return d[COLUMNS].reset_index(drop=True)
