# =============================================================================
# utils/mapping_diagnostics.py
# Pure pandas: find out WHY telemetry mapping rows did not match a measure, and look for the
# rule that would match them. No Spark, so it can be tested without Databricks. Used by
# explore/Explore_telemetry_mapping. Read only: nothing here writes a table.
#
# The match in utils/telemetry_mapping.py joins TelemetryID to the register's stationReference.
# These helpers ask what else could have been joined:
#
#   station_candidates   for rows whose TelemetryID is not a station reference (no_station),
#                        look the row up in ea_rt_stations by every other key it carries: RLOIid,
#                        notation, wiskiID (matched to the file's WISKI_ID, and to its TelemetryID),
#                        the same ID after tidying (case, spaces, dashes, leading zeros) and
#                        finally a similar name. Each candidate says HOW it was found, so a
#                        rule can be chosen on how reliable that route is.
#   candidate_summary    how many rows each route would have found.
#   classify_ambiguity   why several measures fit one row (two periods, two qualifiers ...), and
#                        whether a simple rule would settle it.
#   stage_pairs          the ambiguous rows that are just a Stage and a Downstream Stage, and what the
#                        file says about them (one row, or a row for each).
#   unmapped_measures    register measures no matched row points at, and why.
#   matched_name_check   matched rows whose gauge name and station name disagree: possible wrong
#                        matches, for a person to look at.
# =============================================================================

import re
from difflib import SequenceMatcher

import pandas as pd

from utils.telemetry_mapping import family_of_text, clean

# Routes, best first. A row's best route is the earliest in this list that found a station.
ROUTES = ["RLOIid", "notation", "wiskiID", "wiskiID = TelemetryID", "same after tidy-up", "similar name"]

# Words that say what kind of site it is, not which one, so they are ignored when comparing names.
_STOP = {"the", "at", "rg", "gauge", "raingauge", "rain", "rainfall", "station", "level", "river", "road",
         "rd", "stw", "wtw", "inactive", "gl", "gwl", "obs", "bridge", "br", "fb", "weir"}


def tidy_id(value):
    """An ID compared loosely: upper case, spaces, dashes and underscores removed, leading zeros dropped."""
    v = clean(value)
    if not v:
        return None
    v = re.sub(r"[\s\-_]", "", v.upper())
    return v.lstrip("0") or "0"


def name_tokens(text) -> set:
    return {t for t in re.findall(r"[a-z0-9]+", (clean(text) or "").lower()) if t not in _STOP and len(t) > 1}


def name_similarity(a, b) -> float:
    """0 to 1: the better of word overlap and character similarity on the names with filler words removed."""
    ta, tb = name_tokens(a), name_tokens(b)
    if not ta or not tb:
        return 0.0
    jaccard = len(ta & tb) / len(ta | tb)
    ratio = SequenceMatcher(None, " ".join(sorted(ta)), " ".join(sorted(tb))).ratio()
    return round(max(jaccard, ratio), 3)


def _index(stations: pd.DataFrame, column: str, tidy: bool = False) -> dict:
    """value -> [row positions] for one column of ea_rt_stations."""
    out = {}
    if column not in stations:
        return out
    for pos, value in enumerate(stations[column].tolist()):
        key = tidy_id(value) if tidy else clean(value)
        if key:
            out.setdefault(key, []).append(pos)
    return out


def station_candidates(rows: pd.DataFrame, stations: pd.DataFrame, max_per_row: int = 3, register_refs=None) -> pd.DataFrame:
    """
    Candidate stations for mapping rows that matched no station.

    Args:
        rows:      one line per mapping row, with id, telemetry_id, wiski_id, fffs_location_name.
        stations:  ea_rt_stations: stationReference, notation, label, RLOIid, wiskiID.
        max_per_row: the best candidates kept per row.
        register_refs: the station references in ea_rt_measure_register. A candidate that is NOT in it is
                   listed by the stations API but has no readings in the feed (Welsh sites, for one), so
                   a new rule could not reach it: candidate_in_register says which is which.

    Returns:
        id, telemetry_id, fffs_location_name, how, candidate_station_reference, candidate_label, score,
        candidate_in_register. A row with no candidate by any route is absent; count it as the difference.
    """
    s = stations.reset_index(drop=True)
    by_rloi, by_notation, by_wiski = _index(s, "RLOIid"), _index(s, "notation"), _index(s, "wiskiID")
    by_tidy_ref = _index(s, "stationReference", tidy=True)
    by_tidy_not = _index(s, "notation", tidy=True)
    # An inverted word index, so the name search looks only at stations sharing a word.
    words = {}
    for pos, label in enumerate(s["label"].tolist() if "label" in s else []):
        for t in name_tokens(label):
            words.setdefault(t, []).append(pos)

    refs = None if register_refs is None else {clean(x) for x in register_refs if clean(x)}
    found = []
    for r in rows.drop_duplicates("id").to_dict("records"):
        tid, wid, name = clean(r.get("telemetry_id")), clean(r.get("wiski_id")), r.get("fffs_location_name")
        hits = []                                              # (route, position, score)
        for pos in by_rloi.get(tid, []):
            hits.append(("RLOIid", pos, 1.0))
        for pos in by_notation.get(tid, []):
            hits.append(("notation", pos, 1.0))
        for pos in by_wiski.get(wid, []) if wid else []:
            hits.append(("wiskiID", pos, 1.0))
        for pos in by_wiski.get(tid, []):
            hits.append(("wiskiID = TelemetryID", pos, 1.0))
        t_tidy = tidy_id(tid)
        for pos in by_tidy_ref.get(t_tidy, []) + by_tidy_not.get(t_tidy, []):
            hits.append(("same after tidy-up", pos, 0.95))
        if not hits and name_tokens(name):
            seen = {}
            for t in name_tokens(name):
                for pos in words.get(t, []):
                    seen[pos] = seen.get(pos, 0) + 1
            for pos in sorted(seen, key=seen.get, reverse=True)[:40]:
                score = name_similarity(name, s.loc[pos, "label"])
                if score >= 0.7:
                    hits.append(("similar name", pos, score))
        taken, kept = set(), []
        for route, pos, score in sorted(hits, key=lambda h: (ROUTES.index(h[0]), -h[2])):
            if pos in taken:
                continue
            taken.add(pos)
            kept.append((route, pos, score))
            if len(kept) >= max_per_row:
                break
        for route, pos, score in kept:
            ref = s.loc[pos, "stationReference"] if "stationReference" in s else None
            found.append({"id": r["id"], "telemetry_id": tid, "fffs_location_name": name, "how": route,
                          "candidate_station_reference": ref,
                          "candidate_label": s.loc[pos, "label"] if "label" in s else None, "score": score,
                          "candidate_in_register": None if refs is None else (clean(ref) in refs)})
    cols = ["id", "telemetry_id", "fffs_location_name", "how", "candidate_station_reference", "candidate_label", "score",
            "candidate_in_register"]
    return pd.DataFrame(found, columns=cols)


def candidate_summary(rows: pd.DataFrame, candidates: pd.DataFrame) -> pd.DataFrame:
    """
    How many mapping rows each route would have found, counting each row once at its best route.
    Where the candidates say whether the station is in the register, a route is split into
    "reachable" (the station has readings, so a rule using this route would gain a match) and
    "listed, no readings in the feed" (found, but there is nothing to link: Welsh sites, say).
    """
    total = rows["id"].nunique()
    if candidates.empty:
        return pd.DataFrame({"how": ["no candidate"], "reachable": [None], "rows": [total]})
    c = candidates.assign(rank=candidates["how"].map(ROUTES.index))
    known = c["candidate_in_register"].notna().any()
    # A row's best candidate is the best-ranked one that IS in the register, if any, else the best of all.
    if known:
        c["reg_rank"] = (~c["candidate_in_register"].fillna(False).astype(bool)).astype(int)
        best = c.sort_values(["id", "reg_rank", "rank"]).drop_duplicates("id")
    else:
        best = c.sort_values(["id", "rank"]).drop_duplicates("id")
    best = best.assign(reachable=best["candidate_in_register"].map({True: "reachable", False: "listed, no readings in the feed"})
                       if known else None)
    out = best.groupby(["how", "reachable"], dropna=False).size().rename("rows").reset_index()
    out["order"] = out["how"].map(ROUTES.index)
    out = out.sort_values(["order", "reachable"]).drop(columns="order").reset_index(drop=True)
    missing = total - int(out["rows"].sum())
    if missing > 0:
        out = pd.concat([out, pd.DataFrame({"how": ["no candidate"], "reachable": [None], "rows": [missing]})], ignore_index=True)
    return out


def classify_ambiguity(rows: pd.DataFrame) -> pd.DataFrame:
    """
    One line per ambiguous mapping row saying why several measures fit and what would settle it:
    candidates, why (same qualifier different period, different qualifiers ...) and
    settled_by_shortest_period (exactly one candidate has the shortest period).
    """
    amb = rows[rows["match_status"] == "ambiguous"]
    out = []
    for rid, g in amb.groupby("id"):
        quals = {(q or "").lower() for q in g["register_qualifier"]}
        periods = {p for p in g["period"] if p}
        if len(quals) == 1 and len(periods) > 1:
            why = "same qualifier, different period"
        elif len(quals) > 1 and len(periods) <= 1:
            why = "different qualifiers, same period"
        elif len(quals) > 1 and len(periods) > 1:
            why = "different qualifiers and periods"
        else:
            why = "identical qualifier and period (duplicate measures)"
        nums = pd.to_numeric(pd.Series(list(g["period"])), errors="coerce")
        shortest = nums[nums == nums.min()] if nums.notna().any() else nums.iloc[0:0]
        out.append({"id": rid, "telemetry_id": g["telemetry_id"].iloc[0], "telemetry_parameter": g["telemetry_parameter"].iloc[0],
                    "parameter_qualifier": g["parameter_qualifier"].iloc[0], "candidates": len(g), "why": why,
                    "qualifiers": " | ".join(sorted(q for q in {x or "" for x in g["register_qualifier"]})),
                    "periods": " | ".join(sorted(periods)), "settled_by_shortest_period": len(shortest) == 1})
    return pd.DataFrame(out, columns=["id", "telemetry_id", "telemetry_parameter", "parameter_qualifier", "candidates", "why",
                                      "qualifiers", "periods", "settled_by_shortest_period"])


_STAGE_PAIR = {"stage", "downstream stage"}


def stage_pairs(rows: pd.DataFrame) -> pd.DataFrame:
    """
    The ambiguous mapping rows where the candidate measures are a Stage and a Downstream Stage and
    nothing else, with what the FILE says about that station:
        file_rows           how many rows of the file share the TelemetryID and the parameter family
        file_qualifiers     the ParameterQualifier each of those rows carries
        wiski_ids, fffs_ids the distinct WISKI and FFFS ids on those rows
        file_describes      one_row_no_qualifier (the file does not say which measure it means),
                            distinct_qualifiers (a row each for Stage and Downstream Stage: the
                            qualifier should have settled it, so look for a spelling difference),
                            same_qualifier (several rows with one qualifier) or other
    """
    amb = rows[rows["match_status"] == "ambiguous"]
    out = []
    for rid, g in amb.groupby("id"):
        quals = {(q or "").strip().lower() for q in g["register_qualifier"]}
        if quals != _STAGE_PAIR:
            continue
        tid = g["telemetry_id"].iloc[0]
        fam = g["telemetry_family"].iloc[0] if "telemetry_family" in g else None
        same = rows[(rows["telemetry_id"] == tid)].drop_duplicates("id")
        if fam is not None and "telemetry_family" in same:
            same = same[same["telemetry_family"].fillna("") == (fam or "")]
        fq = [(q or "").strip() for q in same["parameter_qualifier"]]
        filled = {q.lower() for q in fq if q}
        if len(same) == 1 and not fq[0]:
            describes = "one_row_no_qualifier"
        elif len(same) > 1 and filled == _STAGE_PAIR:
            describes = "distinct_qualifiers"
        elif len(same) > 1 and len(filled) <= 1:
            describes = "same_qualifier"
        else:
            describes = "other"
        out.append({"telemetry_id": tid, "file_rows": len(same), "file_qualifiers": " | ".join(sorted({q or "(blank)" for q in fq})),
                    "wiski_ids": " | ".join(sorted({str(w) for w in same["wiski_id"].dropna()})),
                    "fffs_ids": " | ".join(sorted({str(w) for w in same["fffs_id"].dropna()})),
                    "file_describes": describes})
    cols = ["telemetry_id", "file_rows", "file_qualifiers", "wiski_ids", "fffs_ids", "file_describes"]
    return pd.DataFrame(out, columns=cols).drop_duplicates("telemetry_id").reset_index(drop=True)


# Elements of many Welsh place names. A HINT only: it is wrong for some sites (Aberdeen is in Scotland) and
# misses others, and it is there to help someone decide, not to decide. Long elements match the start of a word
# (llan matches Llanwrtyd); short ones must be a whole word (pen, ty).
_WELSH_PREFIXES = ("aber", "afon", "bryn", "caer", "coed", "glan", "llan", "llyn", "maes", "nant", "pont", "porth", "rhyd",
                   "rhos", "ystrad", "pwll", "cefn")
_WELSH_WORDS = {"pen", "ty", "tre", "dol", "cwm", "bach", "fach", "mawr", "y", "yr"} - {"y", "yr"}


def id_shape(value) -> str:
    """The shape of an ID: digits as 9, letters as A, everything else kept ("067033_TG 148" is 999999_AA 999)."""
    v = clean(value) or ""
    return "".join("9" if ch.isdigit() else "A" if ch.isalpha() else ch for ch in v)


def welsh_hint(name) -> bool:
    toks = re.findall(r"[a-z]+", (clean(name) or "").lower())
    return any(t in _WELSH_WORDS or any(t.startswith(p) for p in _WELSH_PREFIXES) for t in toks)


def profile_unplaced(rows: pd.DataFrame) -> dict:
    """
    What the unplaced mapping rows (given as one line each) have in common, so a rule or a list can be
    made for them: the shape of the TelemetryID, its first characters, what they measure, whether the
    name has Welsh place-name elements (a hint), and how many carry a WISKI id.
    Returns a dict of small DataFrames: shapes, prefixes, parameters, hints.
    """
    r = rows.drop_duplicates("id")
    shapes = r["telemetry_id"].map(id_shape).value_counts().rename("rows").reset_index().rename(columns={"telemetry_id": "shape", "index": "shape"})
    prefixes = r["telemetry_id"].map(lambda v: (clean(v) or "")[:2].upper()).value_counts().rename("rows").reset_index()
    prefixes.columns = ["first_two_characters", "rows"]
    params = r.groupby([r["parameter_id"].fillna("(none)"), r["telemetry_parameter"].fillna("(none)")]).size().rename("rows").reset_index()
    params = params.sort_values("rows", ascending=False)
    hint = r["fffs_location_name"].map(welsh_hint)
    hints = pd.DataFrame({"measure": ["rows", "name has a Welsh place-name element (hint only)", "has a WISKI id"],
                          "count": [len(r), int(hint.sum()), int(r["wiski_id"].notna().sum()) if "wiski_id" in r else 0]})
    shapes.columns = ["shape", "rows"]
    return {"shapes": shapes, "prefixes": prefixes, "parameters": params, "hints": hints}


def candidate_measures(candidates: pd.DataFrame, register: pd.DataFrame) -> pd.DataFrame:
    """
    The measures behind each candidate station, so a name-based candidate can be judged: its
    parameter, qualifier, value type (instantaneous, mean ...), period and unit, and whether it is a
    tidal level. One line per candidate measure. A station reached by name often has several
    measures that differ only in value type, which is what makes a name match ambiguous.
    """
    cols = ["id", "telemetry_id", "fffs_location_name", "how", "candidate_station_reference", "candidate_label", "measure_uri",
            "parameter", "qualifier", "value_type", "period", "unit_name", "tidal"]
    if candidates.empty:
        return pd.DataFrame(columns=cols)
    reg = register.copy()
    for c in ("value_type", "unit_name", "qualifier", "parameter", "period"):
        if c not in reg:
            reg[c] = None
    reg["_ref"] = reg["station_reference"].map(clean)
    out = candidates.assign(_ref=candidates["candidate_station_reference"].map(clean)).merge(
        reg[["_ref", "measure_uri", "parameter", "qualifier", "value_type", "period", "unit_name"]], on="_ref", how="left")
    out["tidal"] = (out["qualifier"].fillna("").str.contains("tidal", case=False)
                    | out["parameter"].fillna("").str.contains("tidal", case=False))
    return out[cols]


def tidal_summary(cand_measures: pd.DataFrame) -> pd.DataFrame:
    """
    For rows reached by a similar name: how many have candidate stations that are tidal, and how many
    distinct stations each row reaches (the same place often has several: instantaneous, mean ...).
    """
    cm = cand_measures
    if cm.empty:
        return pd.DataFrame(columns=["id", "stations", "measures", "any_tidal", "value_types"])
    g = cm.groupby("id")
    return pd.DataFrame({
        "stations": g["candidate_station_reference"].nunique(),
        "measures": g["measure_uri"].nunique(),
        "any_tidal": g["tidal"].any(),
        "value_types": g["value_type"].agg(lambda s: " | ".join(sorted({str(v) for v in s.dropna()})))}).reset_index()


def unmapped_measures(register: pd.DataFrame, rows: pd.DataFrame) -> pd.DataFrame:
    """
    Register measures that no matched mapping row points at, each with the family it is and why it is
    unmapped: the station is not in the file at all, or it is but only for other parameters (or its
    rows did not match).
    """
    matched = set(rows.loc[rows["match_status"] == "matched", "measure_uri"].dropna())
    in_file = set(rows["telemetry_id"].dropna())
    r = register[~register["measure_uri"].isin(matched)].copy()
    r["family"] = [family_of_text(f"{p or ''} {q or ''}") or "other" for p, q in zip(r["parameter"], r["qualifier"])]
    r["why"] = [("station is in the file, this parameter is not matched" if clean(s) in in_file else "station is not in the file")
                for s in r["station_reference"]]
    return r[["measure_uri", "station_reference", "station_label", "family", "parameter", "qualifier", "period", "why"]]


def matched_name_check(rows: pd.DataFrame, below: float = 0.4) -> pd.DataFrame:
    """Matched rows whose FFFS location name and the register's station label barely overlap: worth a look."""
    m = rows[rows["match_status"] == "matched"].copy()
    m["name_similarity"] = [name_similarity(a, b) for a, b in zip(m["fffs_location_name"], m["station_label"])]
    cols = ["telemetry_id", "telemetry_parameter", "fffs_location_name", "station_label", "measure_uri", "name_similarity"]
    return m[m["name_similarity"] < below][cols].sort_values("name_similarity")
