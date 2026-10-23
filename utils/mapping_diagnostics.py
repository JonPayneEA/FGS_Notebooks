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

from utils.telemetry_mapping import family_of_text, clean, id_key

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


_TRUE_WORDS = {"true", "t", "yes", "y", "1", "1.0"}
_STATUS_ORDER = ["matched", "ambiguous", "conflict", "no_parameter", "unknown_parameter", "no_station", "inactive", "out_of_scope", "no_telemetry_id"]


def keep_true_status(scope: pd.DataFrame, rows: pd.DataFrame) -> pd.DataFrame:
    """
    The rows of the import list that are marked Keep = TRUE, each with whether it paired to a real-time measure.

    Args:
        scope: the import list (Import_Timeseries.csv): FFFS_ID, Keep, and optionally Parameter, ExternalID,
               ExternalParameter, ImportSource. Column names are matched without regard to case, spaces or
               underscores.
        rows:  ea_rt_telemetry_mapping (one line per mapping row, or per candidate when ambiguous).

    Returns:
        One line per kept row: the list's own columns, then in_mapping (the FFFS_ID, or failing that the
        ExternalID read as a TelemetryID, is a row of the mapping file), paired (it matched a measure),
        match_status, measure_uri, station_reference, station_label, n_readings and the mapping note.
        A row on the list but absent from the mapping file says so; one in the file but unmatched shows why.
    """
    import re as _re
    norm = {_re.sub(r"[^a-z0-9]", "", str(c).lower()): c for c in scope.columns}
    fcol, kcol = norm.get("fffsid"), norm.get("keep")
    if not fcol or not kcol:
        raise ValueError(f"The import list needs FFFS_ID and Keep columns. Columns present: {list(scope.columns)}")
    ecol = norm.get("externalid")
    keep = scope[scope[kcol].map(lambda v: (clean(v) or "").lower() in _TRUE_WORDS)]
    by_fffs, by_tel = {}, {}
    for rec in rows.to_dict("records"):          # joined on id_key: Excel drops leading zeros in some files and not others
        if id_key(rec.get("fffs_id")):
            by_fffs.setdefault(id_key(rec["fffs_id"]), []).append(rec)
        if id_key(rec.get("telemetry_id")):
            by_tel.setdefault(id_key(rec["telemetry_id"]), []).append(rec)
    out = []
    for r in keep.to_dict("records"):
        fid, eid = clean(r.get(fcol)), (clean(r.get(ecol)) if ecol else None)
        lines = by_fffs.get(id_key(fid)) or (by_tel.get(id_key(eid)) if eid else None) or []
        line = {"FFFS_ID": fid}
        for key in ("parameter", "externalid", "externalparameter", "importsource"):
            if norm.get(key):
                line[norm[key]] = clean(r.get(norm[key]))
        if lines:
            lines = sorted(lines, key=lambda x: _STATUS_ORDER.index(x["match_status"]) if x.get("match_status") in _STATUS_ORDER else 99)
            best = lines[0]
            line.update({"in_mapping": True, "paired": best.get("match_status") == "matched",
                         "match_status": best.get("match_status"), "measure_uri": best.get("measure_uri"),
                         "station_reference": best.get("station_reference"), "station_label": best.get("station_label"),
                         "n_readings": best.get("n_readings"), "match_note": best.get("match_note"),
                         "location_name": clean(best.get("fffs_location_name"))})
        else:
            line.update({"in_mapping": False, "paired": False, "match_status": "not in the mapping file", "measure_uri": None,
                         "station_reference": None, "station_label": None, "n_readings": None,
                         "match_note": "neither the FFFS_ID nor the ExternalID is a row of the mapping file", "location_name": None})
        out.append(line)
    return pd.DataFrame(out)


_RETRY_STATUSES = {"no_station", "no_telemetry_id", "not in the mapping file"}


def pair_by_external_id(kept: pd.DataFrame, register: pd.DataFrame, activity=None, now=None) -> pd.DataFrame:
    """
    For kept rows the mapping file could not place, try the import list's own ExternalID as the station reference.

    The mapping file's TelemetryID is sometimes not the station reference (a WISKI-style id, an old number),
    where the import list's ExternalID is (2230TH). Only rows whose status is no_station, no_telemetry_id or
    not in the mapping file are retried, with the list's Parameter and ExternalParameter naming the family.

    Args:
        kept:     keep_true_status() output (needs FFFS_ID, ExternalID, Parameter, ExternalParameter, paired,
                  match_status; any column with "qualifier" in its name is used as the qualifier).
        register: the measure register (measure_uri, station_reference, station_label, parameter, qualifier,
                  period, unit_name).
        activity: optional measure_uri, n_readings, first_reading, last_reading (as in notebook 16); with it an
                  ambiguous retry is settled by readings and every match carries n_readings.
        now:      the time for "recent" in settling; defaults to now.

    Returns:
        kept with paired_via ("mapping file", "ExternalID" or None) and external_status, external_measure_uri,
        external_station_reference, external_station_label, external_n_readings, external_last_reading and
        external_note. A row matched through its ExternalID has paired = True. data_state says "delivers data",
        "matched, no readings" (the station is in the register but the measure delivers nothing, as a suspended
        station does) or "not paired".
    """
    from utils.telemetry_mapping import match_mapping, prepare_mapping, settle_by_readings
    out = kept.copy()
    new = ("external_status", "external_measure_uri", "external_station_reference", "external_station_label",
           "external_n_readings", "external_last_reading", "external_note")
    for c in new:
        out[c] = None
    out["paired_via"] = out["paired"].map(lambda p: "mapping file" if p else None)
    norm = {re.sub(r"[^a-z0-9]", "", str(c).lower()): c for c in out.columns}
    ecol, pcol, xcol = norm.get("externalid"), norm.get("parameter"), norm.get("externalparameter")
    qcol = next((c for k, c in norm.items() if "qualifier" in k), None)
    todo = out[(~out["paired"]) & out["match_status"].isin(_RETRY_STATUSES)] if ecol else out.iloc[0:0]
    todo = todo[todo[ecol].map(lambda v: bool(clean(v)))]
    if not todo.empty:
        raw = pd.DataFrame({"ID": [str(i) for i in todo.index], "FFFS_ID": todo["FFFS_ID"].values, "FFFSLocName": None,
                            "ParameterID": todo[pcol].values if pcol else None, "TelemetryID": todo[ecol].values,
                            "TelemetryParameter": todo[xcol].values if xcol else None,
                            "ParameterQualifier": todo[qcol].values if qcol else None, "WISKI_ID": None})
        rows, _ = match_mapping(prepare_mapping(raw)[0], register)
        if activity is not None and len(activity):
            rows, _ = settle_by_readings(rows, activity, now if now is not None else pd.Timestamp.now())
        for rid, g in rows.groupby("id"):
            idx = int(rid)
            best = g.iloc[0]
            out.loc[idx, "external_status"] = best["match_status"]
            out.loc[idx, "external_note"] = best.get("match_note")
            if best["match_status"] in ("matched", "ambiguous"):
                out.loc[idx, "external_measure_uri"] = best.get("measure_uri") if best["match_status"] == "matched" else None
                out.loc[idx, "external_station_reference"] = best.get("station_reference")
                out.loc[idx, "external_station_label"] = best.get("station_label")
            if best["match_status"] == "matched":
                out.loc[idx, "external_n_readings"] = best.get("n_readings") if "n_readings" in g else None
                out.loc[idx, "external_last_reading"] = best.get("last_reading") if "last_reading" in g else None
                out.loc[idx, "paired"] = True
                out.loc[idx, "paired_via"] = "ExternalID"
    nread = pd.to_numeric(out["external_n_readings"], errors="coerce")
    mapped_n = pd.to_numeric(out["n_readings"], errors="coerce")
    delivers = ((out["paired_via"] == "mapping file") & (mapped_n.fillna(0) > 0)) | ((out["paired_via"] == "ExternalID") & (nread.fillna(0) > 0))
    out["data_state"] = "not paired"
    out.loc[out["paired_via"].notna(), "data_state"] = "matched, no readings"
    out.loc[delivers, "data_state"] = "delivers data"
    return out


def profile_unpaired_kept(kept: pd.DataFrame) -> dict:
    """
    What the kept rows that did not pair have in common, so a rule or a list can be made for them.

    Args:
        kept: pair_by_external_id() output (needs paired, match_status, ExternalID, Parameter,
              ExternalParameter, ImportSource; location_name when the mapping file had one).

    Returns:
        A dict of small DataFrames:
        shapes          the shape of the ExternalID of the rows with no station (digits 9, letters A), by how many,
                        how many are in the mapping file at all, and how many have a Welsh place-name element
                        in the mapping file's name (a hint only);
        prefixes        the first two characters of the same ExternalIDs;
        sources         the same rows by ImportSource;
        unknown_parameter  the Parameter and ExternalParameter words of the rows whose parameter is not known.
    """
    import re as _re
    norm = {_re.sub(r"[^a-z0-9]", "", str(c).lower()): c for c in kept.columns}
    ecol, pcol, xcol, scol = norm.get("externalid"), norm.get("parameter"), norm.get("externalparameter"), norm.get("importsource")
    un = kept[~kept["paired"]]
    gone = un[un["match_status"].isin(["no_station", "no_telemetry_id", "not in the mapping file"])].copy()
    gone["_id"] = gone[ecol].map(clean) if ecol else None
    gone["_welsh"] = gone["location_name"].map(welsh_hint) if "location_name" in gone else False
    gone["shape"] = gone["_id"].map(id_shape)
    gone["first_two_characters"] = gone["_id"].map(lambda v: (v or "")[:2].upper())
    gone["in_mapping_file"] = gone["in_mapping"]
    agg = {"rows": ("shape", "size"), "in_mapping_file": ("in_mapping_file", "sum"), "welsh_name_hint": ("_welsh", "sum")}
    shapes = gone.groupby("shape").agg(**agg).reset_index().sort_values("rows", ascending=False)
    prefixes = gone.groupby("first_two_characters").agg(**agg).reset_index().sort_values("rows", ascending=False)
    sources = (gone.groupby(gone[scol].fillna("(none)")).size().rename("rows").reset_index().rename(columns={scol: "ImportSource"})
               if scol else pd.DataFrame(columns=["ImportSource", "rows"]))
    unk = un[un["match_status"] == "unknown_parameter"]
    keys = [c for c in (pcol, xcol) if c]
    unknown = (unk.groupby([unk[c].fillna("(none)") for c in keys]).size().rename("rows").reset_index().sort_values("rows", ascending=False)
               if keys and len(unk) else pd.DataFrame(columns=keys + ["rows"]))
    return {"shapes": shapes, "prefixes": prefixes, "sources": sources, "unknown_parameter": unknown}


def numeric_id_variants(kept: pd.DataFrame, register: pd.DataFrame, max_examples: int = 3) -> pd.DataFrame:
    """
    For the unpaired kept rows whose ExternalID is digits only, what the register holds that looks like it.

    Tests each ID against the register's station references: as written; with leading zeros to six digits (the
    rule notebook 16 applies); with zeros to seven or eight digits; and any reference that ENDS with the digits
    (a letter or other prefix in front) or STARTS with them (a suffix behind). It says which test found something
    and shows examples, so a rule that does not fire can be told from an ID the feed does not carry.

    Args:
        kept:     pair_by_external_id() output (needs paired, match_status, ExternalID).
        register: the measure register (station_reference).

    Returns:
        One line per ID: external_id, status, then found_as_written, found_zero_padded_6, found_zero_padded_7_8,
        ends_with (examples), starts_with (examples), and verdict.
    """
    import re as _re
    norm = {_re.sub(r"[^a-z0-9]", "", str(c).lower()): c for c in kept.columns}
    ecol = norm.get("externalid")
    cols = ["external_id", "match_status", "found_as_written", "found_zero_padded_6", "found_zero_padded_7_8", "ends_with",
            "starts_with", "verdict"]
    if not ecol:
        return pd.DataFrame(columns=cols)
    refs = sorted({clean(v) for v in register["station_reference"].dropna()} - {None})
    refset = set(refs)
    un = kept[~kept["paired"]]
    out = []
    for eid, status in zip(un[ecol], un["match_status"]):
        e = clean(eid)
        if not e or not e.isdigit():
            continue
        padded6 = e.zfill(6) in refset and len(e) < 6
        padded78 = [e.zfill(n) for n in (7, 8) if e.zfill(n) in refset and len(e) < n]
        ends = [r for r in refs if r.endswith(e) and r != e][:max_examples]
        starts = [r for r in refs if r.startswith(e) and r != e][:max_examples]
        if e in refset:
            verdict = "in the register as written (something else stopped the match)"
        elif padded6:
            verdict = "leading-zero rule should match (check notebook 16 was re-run)"
        elif padded78 or ends or starts:
            verdict = "a similar reference exists: a different rule is needed"
        else:
            verdict = "nothing like it in the register"
        out.append({"external_id": e, "match_status": status, "found_as_written": e in refset, "found_zero_padded_6": padded6,
                    "found_zero_padded_7_8": ", ".join(padded78), "ends_with": ", ".join(ends), "starts_with": ", ".join(starts),
                    "verdict": verdict})
    return pd.DataFrame(out, columns=cols)


def listed_station_check(kept: pd.DataFrame, register: pd.DataFrame, stations: pd.DataFrame) -> pd.DataFrame:
    """
    For every unpaired kept row with no station, whether the feed knows the ExternalID at all.

    Each ExternalID (and, for digits only and shorter than six, its zero-padded form) is looked up in the measure
    register's station references and in the station list (stationReference, notation, RLOIid, wiskiID). The
    verdict separates the cases a new rule or a request to the data team would each fix:
        a measure exists in the register          something else stopped the match
        station listed, no measures in the register   the feed lists the station but gives it no measure (suspended,
                                                  or Wales); no rule can match it
        listed under RLOIid / wiskiID / notation  the station reference differs from the ExternalID: a rule can map it
        not in the station list either            the feed has never heard of it

    Args:
        kept:     pair_by_external_id() output (needs paired, match_status, ExternalID).
        register: the measure register (station_reference).
        stations: ea_rt_stations (stationReference, notation, RLOIid, wiskiID, label), or an empty frame.

    Returns:
        external_id, match_status, verdict, listed_by, station_reference, station_label; one line per row.
    """
    import re as _re
    cols = ["external_id", "match_status", "verdict", "listed_by", "station_reference", "station_label"]
    norm = {_re.sub(r"[^a-z0-9]", "", str(c).lower()): c for c in kept.columns}
    ecol = norm.get("externalid")
    if not ecol:
        return pd.DataFrame(columns=cols)
    refs = {clean(v) for v in register["station_reference"].dropna()} - {None}
    keys = {}
    snorm = {_re.sub(r"[^a-z0-9]", "", str(c).lower()): c for c in stations.columns}
    label_col = snorm.get("label") or snorm.get("name")
    for key_name in ("stationreference", "notation", "rloiid", "wiskiid"):
        col = snorm.get(key_name)
        if not col:
            continue
        for _, r in stations.iterrows():
            v = clean(r[col])
            if v:
                keys.setdefault(v, []).append((key_name, clean(r[snorm["stationreference"]]) if snorm.get("stationreference") else None,
                                               clean(r[label_col]) if label_col else None))
    nice = {"stationreference": "stationReference", "notation": "notation", "rloiid": "RLOIid", "wiskiid": "wiskiID"}
    un = kept[(~kept["paired"]) & kept["match_status"].isin(["no_station", "no_telemetry_id", "not in the mapping file"])]
    out = []
    for eid, status in zip(un[ecol], un["match_status"]):
        e = clean(eid)
        if not e:
            continue
        forms = [e] + ([e.zfill(6)] if e.isdigit() and len(e) < 6 else [])
        if any(f in refs for f in forms):
            out.append((e, status, "a measure exists in the register: something else stopped the match", "register", e, None))
            continue
        hit = next(((f, h) for f in forms for h in keys.get(f, [])), None)
        if hit is None:
            out.append((e, status, "not in the station list either", None, None, None))
        elif hit[1][0] == "stationreference":
            out.append((e, status, "station listed, no measures in the register", "stationReference", hit[1][1], hit[1][2]))
        else:
            out.append((e, status, f"listed under {nice[hit[1][0]]}: the station reference differs from the ExternalID", nice[hit[1][0]],
                        hit[1][1], hit[1][2]))
    return pd.DataFrame(out, columns=cols)
