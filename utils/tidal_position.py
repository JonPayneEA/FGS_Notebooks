# =============================================================================
# utils/tidal_position.py
# Pure pandas: match tide-gauge mapping rows to stations by WHERE they are, using the position that the
# TelemetryID itself carries. No Spark, so it can be tested without Databricks. Used by jobs/16 and
# explore/Explore_telemetry_mapping.
#
# THE ID IS A POSITION. Tide gauge TelemetryIDs such as 5338N011W and 5559N310W are latitude and
# longitude in degrees and minutes: 5338N011W is 53 deg 38 min N, 0 deg 11 min W (Immingham), and 5559N310W
# is 55 deg 59 min N, 3 deg 10 min W (Leith). The digits are not padded, so the split between degrees and
# minutes is not always clear: 5438N19W could be 1 deg 9 min or 0 deg 19 min West. Every plausible reading
# is tried (degrees in the UK range, minutes under 60) and the one nearest a station is the one used.
#
# A name alone cannot place these rows: the same place has several stations (Immingham has three).
# A name that agrees with a station AND a position within a few kilometres of it is strong evidence, and
# position alone is weaker. Each candidate says which, and its distance, so the rule can be chosen on what it
# shows. Nothing here guesses a measure: apply_position_matches() takes only the station's tidal level
# measures that are INSTANTANEOUS (the thresholds are for instantaneous levels, not means). Where the register
# says which value type each measure is, a station with no instantaneous tidal measure is left unmatched. Where
# it does not say at all (the value type is empty for every candidate), the tidal measures are all kept and,
# if there are several, left ambiguous for the readings to settle.
# =============================================================================

import math
import re

import numpy as np
import pandas as pd

from utils.mapping_diagnostics import name_similarity
from utils.telemetry_mapping import clean, family_of_text

_CODE = re.compile(r"^(\d{3,4})([NS])(\d{2,4})([EW])$", re.IGNORECASE)
LAT_RANGE = (49.0, 61.0)       # degrees, the UK and its waters
LON_RANGE = (-9.0, 2.5)


def _readings(digits: str, low: float, high: float, sign: int):
    """Every way of reading a run of digits as degrees then minutes that lands in the range."""
    out = []
    for k in range(0, len(digits)):
        deg = int(digits[:k]) if k else 0
        mins = int(digits[k:])
        if mins >= 60:
            continue
        value = sign * (deg + mins / 60.0)
        if low <= value <= high and (value, deg, mins) not in out:
            out.append((value, deg, mins))
    return out


def decode_gauge_code(code):
    """
    All (lat, lon) readings of a tide gauge code, or [] if it is not one. 5338N011W gives about (53.63, -0.18)
    and a second, less likely reading (53.63, -1.02) because 011 could be 1 deg 1 min.
    """
    m = _CODE.match((clean(code) or "").replace(" ", ""))
    if not m:
        return []
    lat_digits, ns, lon_digits, ew = m.groups()
    lats = _readings(lat_digits, *LAT_RANGE, sign=1 if ns.upper() == "N" else -1)
    lons = _readings(lon_digits, *LON_RANGE, sign=-1 if ew.upper() == "W" else 1)
    return [(la[0], lo[0]) for la in lats for lo in lons]


def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance in kilometres; arrays are allowed for the second point."""
    p1, p2 = np.radians(lat1), np.radians(np.asarray(lat2, dtype=float))
    dphi = p2 - p1
    dlmb = np.radians(np.asarray(lon2, dtype=float) - lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlmb / 2) ** 2
    return 2 * 6371.0088 * np.arcsin(np.sqrt(a))


def position_candidates(rows: pd.DataFrame, stations: pd.DataFrame, max_km: float = 3.0, min_name: float = 0.6) -> pd.DataFrame:
    """
    Stations within max_km of the position a mapping row's TelemetryID encodes.

    Args:
        rows:      one line per mapping row: id, telemetry_id, fffs_location_name.
        stations:  ea_rt_stations: stationReference, label, lat, long.
        max_km:    how close counts. A reading is only to the minute (about 1.9 km), so 3 is the floor.
        min_name:  the name similarity at which the name is said to agree.

    Returns:
        id, telemetry_id, fffs_location_name, candidate_station_reference, candidate_label, distance_km,
        name_similarity, evidence ("name and position" or "position only"), nearest first within a row.
    """
    cols = ["id", "telemetry_id", "fffs_location_name", "candidate_station_reference", "candidate_label",
            "distance_km", "name_similarity", "evidence"]
    s = stations.copy()
    s["_lat"] = pd.to_numeric(s.get("lat"), errors="coerce")
    s["_lon"] = pd.to_numeric(s.get("long"), errors="coerce")
    s = s.dropna(subset=["_lat", "_lon"]).reset_index(drop=True)
    out = []
    for r in rows.drop_duplicates("id").to_dict("records"):
        readings = decode_gauge_code(r.get("telemetry_id"))
        if not readings or s.empty:
            continue
        # the nearest of the readings to each station
        dist = np.min(np.vstack([haversine_km(la, lo, s["_lat"].values, s["_lon"].values) for la, lo in readings]), axis=0)
        for pos in np.where(dist <= max_km)[0]:
            sim = name_similarity(r.get("fffs_location_name"), s.loc[pos, "label"])
            out.append({"id": r["id"], "telemetry_id": r.get("telemetry_id"), "fffs_location_name": r.get("fffs_location_name"),
                        "candidate_station_reference": s.loc[pos, "stationReference"], "candidate_label": s.loc[pos, "label"],
                        "distance_km": round(float(dist[pos]), 2), "name_similarity": sim,
                        "evidence": "name and position" if sim >= min_name else "position only"})
    df = pd.DataFrame(out, columns=cols)
    return df.sort_values(["id", "distance_km"]).reset_index(drop=True) if len(df) else df


def apply_position_matches(rows: pd.DataFrame, candidates: pd.DataFrame, register: pd.DataFrame,
                           evidence: str = "name and position", prefer_value_type: str = "instantaneous") -> pd.DataFrame:
    """
    Give no_station rows a measure when a station agrees with their name AND position.

    The station's tidal level measures are taken (a level measure whose qualifier says Tidal; a station with
    none is left alone), and of those only the instantaneous ones, wherever the register gives a value type.
    A station whose tidal measures are all means (or any other type) is left unmatched. If the value type is
    empty for every one, they are all kept. Exactly one measure is a match, with a note saying how it was
    found; several are written as ambiguous, one line each, for the readings to settle. Rows with no such
    station are untouched.
    """
    c = candidates[candidates["evidence"] == evidence] if len(candidates) else candidates
    if c.empty:
        return rows
    reg = register.copy()
    reg["_ref"] = reg["station_reference"].map(clean)
    reg["_family"] = [family_of_text(f"{p or ''} {q or ''}") for p, q in zip(reg["parameter"], reg["qualifier"])]
    reg["_tidal"] = reg["qualifier"].fillna("").str.contains("tidal", case=False)
    has_vt = "value_type" in reg
    out = []
    handled = set()
    for rid, g in c.groupby("id"):
        refs = {clean(x) for x in g["candidate_station_reference"]}
        m = reg[reg["_ref"].isin(refs) & (reg["_family"] == "level") & reg["_tidal"]]
        if has_vt and m["value_type"].notna().any():
            m = m[m["value_type"].fillna("").str.lower() == prefer_value_type]     # the type is known: take only that one
        if m.empty:
            continue
        handled.add(rid)
        base = rows[rows["id"] == rid].iloc[0].to_dict()
        best = g.sort_values("distance_km").iloc[0]
        how = f"matched by name and position (tide gauge code; {best['candidate_label']}, {best['distance_km']} km)"
        status = "matched" if len(m) == 1 else "ambiguous"
        for rec in m.to_dict("records"):
            line = dict(base)
            line.update({"match_status": status, "measure_uri": rec["measure_uri"], "station_reference": rec["station_reference"],
                         "station_label": rec.get("station_label"), "register_parameter": rec["parameter"],
                         "register_qualifier": rec["qualifier"], "period": rec.get("period"), "unit_name": rec.get("unit_name"),
                         "match_note": how if status == "matched" else f"{len(m)} tidal level measures fit; {how}"})
            out.append(line)
    if not handled:
        return rows
    kept = rows[~rows["id"].isin(handled)]
    return pd.concat([kept, pd.DataFrame(out).reindex(columns=rows.columns)], ignore_index=True)
