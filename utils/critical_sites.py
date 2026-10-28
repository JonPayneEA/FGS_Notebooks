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


BROWSER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
# Ways of asking the NHS ODS API for JSON, tried in turn. The "406 Not Acceptable" it gave at first was not about the format: its reply body said
# "Supplied Offset must be greater than 1", because we sent Offset=0 and the API counts from 1. The variants are kept as a harmless fall-back.
ODS_VARIANTS = (
    ("_format=json", {}, {"_format": "json"}),
    ("Accept application/json", {"Accept": "application/json"}, {}),
    ("browser client", {"User-Agent": BROWSER_AGENT, "Accept": "*/*"}, {}),
    ("browser client with _format=json", {"User-Agent": BROWSER_AGENT, "Accept": "*/*"}, {"_format": "json"}),
    ("a smaller page", {"User-Agent": BROWSER_AGENT, "Accept": "*/*"}, {"_format": "json", "_small": "1"}),
)


def fetch_ods_role(get, role, page: int = 1000, base: str = "https://directory.spineservices.nhs.uk/ORD/2-0-0/organisations", variants=ODS_VARIANTS):
    """All active organisations with an ODS role. `role` is a role id (taken as the primary role) or a dict of search parameters such as
    {"NonPrimaryRoleId": "RO76"}. `get(url, params, headers)` returns a response with status_code, text and json().
    Each variant is tried for the first page until one answers 200; that one is then used for every further page. If none works, the error
    lists each attempt with its status and the start of the reply, so the cause can be seen. Returns (organisations, the variant that worked)."""
    role_params = {"PrimaryRoleId": role} if isinstance(role, str) else dict(role)
    attempts, chosen = [], None
    for label, headers, extra in variants:
        params = {**role_params, "Status": "Active", "Limit": page if "_small" not in extra else 100, "Offset": 1,
                  **{k: v for k, v in extra.items() if k != "_small"}}
        r = get(base, params, headers)
        if r.status_code == 200:
            chosen = (label, headers, extra, params["Limit"])
            first = r.json().get("Organisations", [])
            break
        attempts.append(f"{label}: {r.status_code} {str(r.text)[:120]!r}")
    if chosen is None:
        raise RuntimeError("the NHS ODS API refused every variant. " + " | ".join(attempts))
    label, headers, extra, limit = chosen
    out, offset, orgs = list(first), 1, first            # the API counts pages from 1: "Supplied Offset must be greater than 1" (as it replied to Offset=0)
    while len(orgs) >= limit:
        offset += limit
        params = {**role_params, "Status": "Active", "Limit": limit, "Offset": offset, **{k: v for k, v in extra.items() if k != "_small"}}
        r = get(base, params, headers)
        r.raise_for_status() if hasattr(r, "raise_for_status") else None
        orgs = r.json().get("Organisations", [])
        out.extend(orgs)
    return out, label


# GP practices are not found by PrimaryRoleId=RO76 (it returned nothing): in the NHS directory a practice has RO177 (prescribing cost centre) as its
# primary role and RO76 (GP practice) as a secondary one. These ways of asking are tried in order; the first that returns records is used.
GP_PRACTICE_QUERIES = (
    ("non-primary role RO76", {"NonPrimaryRoleId": "RO76"}),
    ("role RO76", {"Roles": "RO76"}),
    ("primary role RO76", {"PrimaryRoleId": "RO76"}),
    ("primary role RO177 (prescribing cost centre, includes GP practices)", {"PrimaryRoleId": "RO177"}),
)
# NHS trust sites (role RO198) are every site of every trust: wards, clinics, offices, health centres. Only these names are hospital-type
# sites; the rest are dropped when the table is built (the saved copy keeps them all).
HOSPITAL_WORDS = r"\b(hospital|infirmary|a ?& ?e|accident|emergency (department|care)|urgent (treatment|care)|walk[- ]?in|minor injur|maternity|birth ?(centre|center)|mental health unit|psychiatric)\b"


# a trust site whose name says it is a part of a hospital, not the hospital: wards, departments, outpatients, pharmacies, theatres and so on
HOSPITAL_PART_WORDS = r"\b(ward|wards|department|dept|outpatient|outpatients|pharmacy|theatre|theatres|day (surgery|unit|case|hospital)|endoscopy|dialysis|imaging|radiology|pathology|laboratory|catering|estates|car park|offices?|headquarters|admin|annex|annexe|block|building|wing)\b"


def merge_clusters(sites: pd.DataFrame, radius_m: float) -> pd.DataFrame:
    """Keep one site from every cluster of sites that lie within `radius_m` of each other (chained: A near B and B near C make one cluster).
    The one kept has the shortest name, which is usually the hospital itself and not a ward or department of it."""
    if len(sites) < 2:
        return sites
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    from scipy.spatial import cKDTree
    xy = sites[["easting", "northing"]].to_numpy(dtype="float64")
    pairs = cKDTree(xy).query_pairs(radius_m, output_type="ndarray")
    n = len(sites)
    graph = coo_matrix((np.ones(len(pairs)), (pairs[:, 0], pairs[:, 1])), shape=(n, n)) if len(pairs) else coo_matrix((n, n))
    _, label = connected_components(graph, directed=False)
    d = sites.assign(_cluster=label, _len=sites["name"].astype(str).str.len())
    keep = d.sort_values(["_cluster", "_len", "name"]).drop_duplicates("_cluster")
    return keep.drop(columns=["_cluster", "_len"]).sort_index().reset_index(drop=True)


def keep_hospitals(sites: pd.DataFrame, merge_radius_m: float = 300.0) -> pd.DataFrame:
    """Of NHS ODS sites, keep GP practices and trust sites whose name says hospital-type care, drop those that are a part of a hospital (ward,
    department, outpatients), and merge the rest within `merge_radius_m` into one site per hospital (the ODS has many records per hospital, each
    placed at its postcode, so the postcodes of one hospital lie close together). Kept trust sites are re-typed 'hospital'. Other sources are untouched."""
    d = sites.copy()
    trust = d["site_type"] == "NHS trust site"
    names = d["name"].astype(str)
    named = names.str.contains(HOSPITAL_WORDS, case=False, regex=True) & ~names.str.contains(HOSPITAL_PART_WORDS, case=False, regex=True)
    hospitals = merge_clusters(d[trust & named].assign(site_type="hospital"), merge_radius_m)
    return pd.concat([d[~trust], hospitals], ignore_index=True)


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
        # solar and wind farms are tagged as plants too: thousands of them, and not critical sites
        if any(w in (t.get("plant:source", "") + ";" + t.get("generator:source", "")) for w in ("solar", "wind")):
            return None
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


def dedupe_nearby(keep: pd.DataFrame, candidates: pd.DataFrame, radius_m: float = 150.0, radius_by_source=None) -> pd.DataFrame:
    """Drop the candidates that lie within a radius of a site in `keep` with the same group (the same place seen in two sources). The radius is
    `radius_m`, or `radius_by_source[source of the kept site]` where given: a site placed by postcode (the NHS list) can be a few hundred metres
    from the building, so it needs a wider radius than one with surveyed coordinates (DfE)."""
    if candidates.empty or keep.empty:
        return candidates
    from scipy.spatial import cKDTree
    radius_by_source = radius_by_source or {}
    drop = np.zeros(len(candidates), dtype=bool)
    for g in candidates["site_group"].unique():
        sel = (candidates["site_group"] == g).to_numpy()
        xy = candidates.loc[sel, ["easting", "northing"]].to_numpy()
        for src, k in keep[keep["site_group"] == g].groupby("source"):
            tree = cKDTree(k[["easting", "northing"]].to_numpy())
            dist, _ = tree.query(xy, k=1)
            drop[np.flatnonzero(sel)[dist <= radius_by_source.get(src, radius_m)]] = True
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
