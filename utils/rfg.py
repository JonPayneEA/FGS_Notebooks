# =============================================================================
# utils/rfg.py
# Rapid Flood Guidance (RFG) from the Flood Forecasting Centre API, and what lies under each of its polygons. Pure pandas, numpy and shapely, so it
# is tested without Spark. jobs/36_ingest_rfg reads and stores the updates; jobs/37_rfg_exposure counts what is under each polygon.
#
# AN RFG UPDATE is issued when rapid flooding (usually from heavy showers) is expected or has been seen. It holds a status, a national summary,
# a link to the PDF and a set of polygons, each with its own latest update text, its counties and a validity flag. When no guidance is current
# the latest endpoint returns only last_updated and the status "No Rapid Flood Guidance at this time". That state is stored as a row of its own, so
# the history shows when guidance started and stopped.
#
#   update_key      "<id>" for an issued update, "none_<last_updated>" for a no-guidance state. The key of every row.
#   parse_update    one API update (or the no-guidance reply) -> the update row and its polygon rows
#   is_present      whether a status text means guidance is in force
#   polygon_key     "<update_key>|<polygon_number>", the key the exposure tables use
#   rfg_exposure    for each polygon: the people and other measures inside it, by constituency, by warning and alert area, and the critical sites
# =============================================================================

import json

import numpy as np
import pandas as pd
import shapely

from .area_exposure import VALUE_COLS, point_values, site_membership

NO_GUIDANCE_STATUS = "No Rapid Flood Guidance at this time"
PRESENT_STATUS = "Rapid Flooding Guidance Present"

UPDATE_COLS = ["update_key", "rfg_id", "status", "is_present", "issued_at", "last_updated", "date_issued", "time_issued", "pdf_url",
               "national_summary", "n_polygons", "n_valid", "ingested_at"]
POLYGON_COLS = ["update_key", "rfg_id", "polygon_number", "geometry", "updated_at", "latest_update", "counties", "is_valid", "ingested_at"]

# what is counted under each polygon: the exposure measures job 35 uses, less the deprivation weights (not summable to a mean here)
EXPOSURE_COLS = [c for c in VALUE_COLS if c not in ("imd_w", "imd_wsum")]


def is_present(status) -> bool:
    """True when a status text means guidance is in force. Anything that says 'No' or is blank is not."""
    t = str(status or "").strip().lower()
    return bool(t) and not t.startswith("no ") and "present" in t


def update_key(update: dict) -> str:
    """'<id>' for an issued update; 'none_<last_updated>' for the no-guidance reply, which has no id."""
    if update.get("id") is not None:
        return str(int(update["id"]))
    return f"none_{update.get('last_updated') or 'unknown'}"


def polygon_key(key, polygon_number) -> str:
    return f"{key}|{int(polygon_number)}"


def _ts(value):
    """An ISO timestamp as a timezone-aware pandas Timestamp, or NaT."""
    return pd.to_datetime(value, utc=True, errors="coerce") if value else pd.NaT


def _geojson(coordinates):
    """Polygon rings from the API (a list of rings of [lon, lat]) as a GeoJSON Polygon string. A MultiPolygon nesting is kept as such."""
    depth, c = 0, coordinates
    while isinstance(c, list) and c:
        c, depth = c[0], depth + 1
    kind = {3: "Polygon", 4: "MultiPolygon"}.get(depth)
    if kind is None:
        return None
    return json.dumps({"type": kind, "coordinates": coordinates})


def parse_update(update: dict, ingested_at):
    """(update row dict, [polygon row dicts]) from one RFG update as the API returns it. The no-guidance reply (no id, no polygons) gives an update row
    with is_present False and no polygons. A polygon with no usable coordinates is kept with geometry None, so it is counted and not hidden."""
    key = update_key(update)
    status = update.get("status")
    polys = update.get("polygons") or []
    prows = []
    for p in polys:
        num = p.get("polygon_number")
        prows.append({
            "update_key": key, "rfg_id": update.get("id"), "polygon_number": int(num) if num is not None else len(prows) + 1,
            "geometry": _geojson(p.get("coordinates")), "updated_at": _ts(p.get("updated_at")), "latest_update": p.get("latest_update"),
            "counties": ", ".join(str(c) for c in (p.get("counties") or [])), "is_valid": bool(p.get("is_valid")), "ingested_at": ingested_at})
    urow = {
        "update_key": key, "rfg_id": update.get("id"), "status": status, "is_present": is_present(status) and update.get("id") is not None,
        "issued_at": _ts(update.get("issued_at")), "last_updated": _ts(update.get("last_updated")), "date_issued": update.get("date_issued"),
        "time_issued": _ts(update.get("time_issued")), "pdf_url": update.get("pdf_url"), "national_summary": update.get("national_summary"),
        "n_polygons": len(prows), "n_valid": sum(1 for r in prows if r["is_valid"] and r["geometry"]), "ingested_at": ingested_at}
    return urow, prows


def is_stored(stored: pd.DataFrame, update: dict) -> bool:
    """Whether this version of the update is already stored: the same key with the same last_updated. `stored` has update_key and last_updated.
    A modified update (same id, later last_updated) is therefore ingested again."""
    if stored is None or not len(stored):
        return False
    key = update_key(update)
    lu = _ts(update.get("last_updated"))
    s = stored[stored["update_key"].astype(str) == key]
    return bool(len(s)) and any(pd.notna(lu) and pd.notna(x) and pd.Timestamp(x) == lu for x in s["last_updated"])


def _empty(cols):
    return pd.DataFrame(columns=cols)


def rfg_exposure(points: pd.DataFrame, polys, keys, area_polys=None, area_ids=None, area_kinds=None, area_names=None, sites: pd.DataFrame = None) -> dict:
    """What lies under each RFG polygon. `points` are address points (easting, northing, people ... and the rank columns job 35 reads), `polys` are
    polygons in British National Grid, `keys` the polygon_key of each. Optional: the warning and alert areas (`area_polys`, `area_ids`, `area_kinds`,
    `area_names`) and the critical sites. Returns a dict of frames:
      polygon         one row per polygon (every key, zero where nothing is inside): EXPOSURE_COLS as sums
      constituency    (polygon_key, pcon): addresses, people, households, people_rs_high_medium
      areas           (polygon_key, kind, area_id, area_name): addresses, people, people_rs_high_medium  (people of the polygon who are in that area)
      sites           one row per critical site inside a polygon: polygon_key, site_id, name, site_group, site_type, band, rofrs_rank
    People counted in two areas of one kind appear once in each; the polygon row is the distinct total."""
    keys = list(keys)
    zero = pd.DataFrame(0.0, index=pd.Index(keys, name="polygon_key"), columns=EXPOSURE_COLS)
    if not len(keys):
        return {"polygon": zero.reset_index(), "constituency": _empty(["polygon_key", "pcon", "addresses", "people", "households", "people_rs_high_medium"]),
                "areas": _empty(["polygon_key", "kind", "area_id", "area_name", "addresses", "people", "people_rs_high_medium"]),
                "sites": _empty(["polygon_key", "site_id", "name", "site_group", "site_type", "band", "rofrs_rank"])}
    out = {}
    pcols = ["addresses", "people", "households", "people_rs_high_medium"]
    tree = shapely.STRtree(np.asarray(polys, dtype=object))
    if len(points):
        pt = shapely.points(points["easting"].to_numpy(dtype="float64"), points["northing"].to_numpy(dtype="float64"))
        pi, qi = tree.query(pt, predicate="intersects")
    else:
        pi = qi = np.array([], dtype=int)
    if len(pi):
        vals = point_values(points.reset_index(drop=True))
        D = vals.iloc[pi][EXPOSURE_COLS].reset_index(drop=True)
        D["polygon_key"] = np.asarray(keys, dtype=object)[qi]
        poly = D.groupby("polygon_key", sort=False)[EXPOSURE_COLS].sum()
        zero.update(poly)
        pc = points["pcon"].astype("object").where(points["pcon"].notna(), "").to_numpy() if "pcon" in points else np.array([""] * len(points), dtype=object)
        C = vals.iloc[pi][pcols].reset_index(drop=True)
        C["polygon_key"], C["pcon"] = np.asarray(keys, dtype=object)[qi], pc[pi]
        con = C.groupby(["polygon_key", "pcon"], sort=False)[pcols].sum().reset_index()
        out["constituency"] = con[con["pcon"].astype(str) != ""]
        if area_polys is not None and len(area_polys):
            atree = shapely.STRtree(np.asarray(area_polys, dtype=object))
            sub = shapely.points(points["easting"].to_numpy(dtype="float64")[pi], points["northing"].to_numpy(dtype="float64")[pi])
            ri, aj = atree.query(sub, predicate="intersects")
            if len(ri):
                A = vals.iloc[pi].reset_index(drop=True).iloc[ri][["addresses", "people", "people_rs_high_medium"]].reset_index(drop=True)
                A["polygon_key"] = np.asarray(keys, dtype=object)[qi][ri]
                A["kind"] = np.asarray(area_kinds, dtype=object)[aj]
                A["area_id"] = np.asarray(area_ids, dtype=object)[aj]
                A["area_name"] = np.asarray(area_names if area_names is not None else area_ids, dtype=object)[aj]
                out["areas"] = A.groupby(["polygon_key", "kind", "area_id", "area_name"], sort=False)[["addresses", "people", "people_rs_high_medium"]].sum().reset_index()
    out.setdefault("constituency", _empty(["polygon_key", "pcon", *pcols]))
    out.setdefault("areas", _empty(["polygon_key", "kind", "area_id", "area_name", "addresses", "people", "people_rs_high_medium"]))
    out["polygon"] = zero.reset_index()
    if sites is not None and len(sites):
        m = site_membership(sites, polys, keys, ["rfg"] * len(keys))
        m = m.rename(columns={"area_id": "polygon_key"}).drop(columns=["kind", "easting", "northing"], errors="ignore")
        out["sites"] = m[["polygon_key", "site_id", "name", "site_group", "site_type", "band", "rofrs_rank"]].reset_index(drop=True)
    else:
        out["sites"] = _empty(["polygon_key", "site_id", "name", "site_group", "site_type", "band", "rofrs_rank"])
    return out
