# =============================================================================
# utils/telemetry_mapping.py
# Pure pandas: match the telemetry mapping file (TelemetryID, FFFS_ID, WISKI_ID and the
# parameter each row is for) to the measures in ea_rt_measure_register. No Spark, so it can be
# tested without Databricks. Used by jobs/16_build_wiski_lookup.
#
# THE KEY. TelemetryID is the equivalent of stationReference, which the register carries for
# every measure. But a station has several measures, and one TelemetryID can stand for several
# parameters: Bewdley has a flow and a stage and the file gives both the TelemetryID 2001.
# So a row is matched on the station AND on what is measured.
#
# WHAT IS MEASURED. Two columns of the file say it, and they can disagree:
#   TelemetryParameter   free text: Water Level, Rainfall, Flow, Significant wave height (Hm0) ...
#   ParameterID          a code whose prefix names the family: H.obs, Q.obs, P.obs, GW.obs ...
# Each is reduced to a family (level, flow, rainfall, groundwater, wave, shingle). If both are
# known and differ, the row is written as a conflict and NOT matched: it is for a person to
# settle, not for this code to guess. ONE difference is not a conflict: a groundwater level gauge
# is called "Water Level" in TelemetryParameter and GW.obs in ParameterID. The code is the more
# specific, so a "level" text with a groundwater code is a groundwater measure. The register's parameter and qualifier are reduced to the
# same families. A station with one measure is not matched on the station alone, because that
# is the mistake this replaces.
#
# THE _TG SPACE. Rain gauge telemetry IDs are written with a space before the number where the station
# reference has an underscore ("067033_TG 148" for "067033_TG_148"). That ONE pattern, "_TG" then a space
# then digits at the end, is tried with an underscore when the ID as written is not a station reference,
# and the note on the row says so. Any other ID with a space is left exactly as written.
#
# INACTIVE SITES. A location name with "INACTIVE" in it (Pevensey INACTIVE) is a gauge the file itself
# says is no longer in use. A row like that which did not match anything is set aside with the status
# inactive and takes no part in the counts of what is missing. One that DID match a live measure stays
# matched, with a note, because a real match is not thrown away on the strength of a name.
#
# SETTLING BY READINGS. A station can list two measures of the same kind of which only one ever
# delivers data (a retired or duplicate entry). settle_by_readings() takes the readings each candidate
# has: if exactly one candidate has any readings it is chosen, and if several have but exactly one has
# a reading in the last `recent_days`, that one is. Anything else stays ambiguous. This is the one place
# the match looks beyond the file and the register, and the note on the row says so.
#
# STATUS of each mapping row:
#   matched          exactly one measure at that station has the family (after the qualifier
#                    narrowed several, where it could)
#   ambiguous        several measures still fit (two periods of the same measure, say). Every
#                    candidate is written, marked ambiguous. Use only matched rows as the link.
#   conflict         the two columns disagree about the family
#   unknown_parameter  neither column names a family this code knows
#   no_station       no measure in the register has that station reference
#   no_parameter     the station is there but has no measure of that family
#   no_telemetry_id  the row has no TelemetryID
#   inactive         the location name says INACTIVE and the row matched nothing
#   out_of_scope     the row is on a list of sites known to be outside the real-time feed (see
#                    flag_out_of_scope) and matched nothing
# =============================================================================

import re

import pandas as pd

# The mapping file's columns, by the name they take here.
FILE_COLUMNS = {
    "id": "id", "fffs_id": "fffs_id", "fffsloc_name": "fffs_location_name", "fffslocname": "fffs_location_name",
    "parameterid": "parameter_id", "telemetryid": "telemetry_id", "telemetryparameter": "telemetry_parameter",
    "parameterqualifier": "parameter_qualifier", "wiski_id": "wiski_id", "wiskiid": "wiski_id",
    "wiskiparameter": "wiski_parameter", "wiskixmlparameter": "wiski_xml_parameter",
    "wiskixmlqualifier": "wiski_xml_qualifier",
}
OUTPUT_FIELDS = ["id", "fffs_id", "fffs_location_name", "parameter_id", "telemetry_id", "telemetry_parameter",
                 "parameter_qualifier", "wiski_id", "wiski_parameter", "wiski_xml_parameter", "wiski_xml_qualifier"]

# Keyword to family, in order: the first that appears in the text wins, so groundwater is tested
# before level and "wave height" before the bare word.
_TEXT_FAMILIES = [("groundwater", "groundwater"), ("wave", "wave"), ("shingle", "shingle"), ("rain", "rainfall"),
                  ("flow", "flow"), ("level", "level"), ("stage", "level")]
# ParameterID prefix (before the first dot) to family.
_CODE_FAMILIES = {"h": "level", "q": "flow", "p": "rainfall", "gw": "groundwater", "wave": "wave", "shingle": "shingle"}

_SCIENTIFIC = re.compile(r"^\d+(\.\d+)?[eE][+-]?\d+$")
# "067033_TG 148": a space between _TG and the number that ends the ID. Nothing else is rewritten.
_TG_SPACE = re.compile(r"(_TG)\s+(\d+)$", re.IGNORECASE)


def _none(value):
    """None for a missing value (NaN, NaT, None), else the value."""
    return None if value is None or (not isinstance(value, str) and pd.isna(value)) else value


def clean(value):
    """A trimmed string, or None. A number that arrived as a float ("4687.0") loses its ".0"."""
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None
    text = str(value).strip()
    if not text or text.lower() in ("nan", "none", "null"):
        return None
    if re.fullmatch(r"\d+\.0+", text):
        text = text.split(".")[0]
    return text


def family_of_text(text):
    """The family a free-text parameter names, or None."""
    t = (clean(text) or "").lower()
    for word, family in _TEXT_FAMILIES:
        if word in t:
            return family
    return None


def family_of_code(code):
    """The family a ParameterID names from its prefix (H.obs is level), or None."""
    c = (clean(code) or "").lower()
    if not c:
        return None
    return _CODE_FAMILIES.get(c.split(".")[0])


def reconcile_family(text_family, code_family):
    """
    The family a mapping row is for, from the two columns, and whether they conflict.
    Returns (family, conflict). Either alone is used. Equal is that. A level text with a
    groundwater code is groundwater (the code is the more specific). Anything else that
    differs is a conflict and has no family.
    """
    if text_family and code_family and text_family != code_family:
        if text_family == "level" and code_family == "groundwater":
            return "groundwater", False
        return None, True
    return (text_family or code_family), False


def prepare_mapping(raw: pd.DataFrame):
    """
    The mapping file with clean columns (OUTPUT_FIELDS), the two families and a note on what
    the file got wrong. EVERY other column in the file is kept too, as text, named src_ plus its
    own name (a station name column, say), so nothing in the file is lost on the way in; the
    WISKI id exactly as the file holds it is kept as wiski_id_in_file. Returns (mapping, report);
    report counts rows, rows with no TelemetryID and WISKI ids Excel turned into scientific
    notation, which have lost digits and are blanked, and lists the extra columns kept.
    """
    cols, extras, taken = {}, {}, set()
    for c in raw.columns:
        key = re.sub(r"[^a-z0-9_]", "", str(c).lower())
        if key in FILE_COLUMNS and FILE_COLUMNS[key] not in cols.values():
            cols[c] = FILE_COLUMNS[key]
        else:
            name = "src_" + (re.sub(r"[^a-z0-9]+", "_", str(c).lower()).strip("_") or "column")
            while name in taken:
                name += "_"
            taken.add(name)
            extras[c] = name
    m = raw.rename(columns={**cols, **extras})[[*cols.values(), *extras.values()]].copy()
    for field in OUTPUT_FIELDS:
        if field not in m:
            m[field] = None
    m = m[OUTPUT_FIELDS + list(extras.values())].copy()
    for field in m.columns:
        m[field] = m[field].map(clean)
    m["wiski_id_in_file"] = m["wiski_id"]
    scientific = m["wiski_id"].map(lambda v: bool(v) and bool(_SCIENTIFIC.match(v)))
    m.loc[scientific, "wiski_id"] = None
    m["telemetry_family"] = m["telemetry_parameter"].map(family_of_text)
    m["code_family"] = m["parameter_id"].map(family_of_code)
    report = {"rows": int(len(m)), "no_telemetry_id": int(m["telemetry_id"].isna().sum()),
              "wiski_id_in_scientific_notation": int(scientific.sum()), "extra_columns": list(extras.values())}
    return m, report


def prepare_register(register: pd.DataFrame) -> pd.DataFrame:
    """The register with a cleaned station_reference, the measure's family and its qualifier in lower case."""
    r = register.copy()
    for c in ("measure_uri", "station_reference", "station_label", "parameter", "qualifier", "period", "unit_name"):
        if c not in r:
            r[c] = None
    r["station_key"] = r["station_reference"].map(clean)
    r["family"] = [family_of_text(f"{p or ''} {q or ''}") for p, q in zip(r["parameter"], r["qualifier"])]
    r["qualifier_key"] = r["qualifier"].map(lambda v: (clean(v) or "").lower())
    return r


_NO_DATUM = {"", "---", "--", "-"}


def _prefer_stage_in_datum(cands: pd.DataFrame):
    """
    Narrow several level measures at one station when the file does not name a qualifier. Some stations
    export a Logged level beside the Stage level, and the Stage in two forms (with a datum such as mASD, and
    with none). Logged is dropped where a non-Logged measure exists, then the no-datum measure is dropped
    where one with a datum exists. Never empties the set. Returns (candidates, note).
    """
    notes = []
    logged = cands["qualifier_key"].str.contains("logged", na=False)
    if logged.any() and not logged.all():
        cands = cands[~logged]
        notes.append("Logged level set aside")
    unit = cands["unit_name"].map(lambda v: (clean(v) or "").strip().lower())
    nodatum = unit.isin(_NO_DATUM)
    if nodatum.any() and not nodatum.all():
        cands = cands[~nodatum]
        notes.append("level with no datum set aside")
    return cands, "; ".join(notes)


_MEASURE_COLUMNS = ("measure_uri", "station_reference", "station_label", "register_parameter", "register_qualifier", "period", "unit_name")


def _set_aside(rows: pd.DataFrame, mask: pd.Series, status: str, note, keep_note_for_matched: str, override_matched: bool = False):
    """
    Give every UNMATCHED mapping row selected by `mask` the status `status`, collapsed to one line with no
    measure. A selected row that matched a measure is left matched and gets keep_note_for_matched on its
    note, unless override_matched is true, when it is set aside too and its link is removed. `note` is a
    string, or a function of the row's FFFS id and TelemetryID giving its own note.
    """
    r = rows.copy()
    matched_ids = set() if override_matched else set(r.loc[mask & (r["match_status"] == "matched"), "id"])
    kept = mask & r["id"].isin(matched_ids)
    r.loc[kept, "match_note"] = r.loc[kept, "match_note"].fillna("").map(
        lambda n: (n + "; " if n else "") + keep_note_for_matched)
    aside = mask & ~r["id"].isin(matched_ids)
    first = aside & ~r.duplicated("id")                              # one line per mapping row
    for col in _MEASURE_COLUMNS:
        if col in r:
            r.loc[first, col] = None
    r.loc[first, "match_status"] = status
    r.loc[first, "match_note"] = [note(f, t) if callable(note) else note
                                  for f, t in zip(r.loc[first, "fffs_id"], r.loc[first, "telemetry_id"])]
    return r[~(aside & ~first)].reset_index(drop=True)


def flag_inactive(rows: pd.DataFrame) -> pd.DataFrame:
    """
    Set aside mapping rows whose FFFS location name says INACTIVE and that matched nothing: one line
    each, status inactive, no measure. A row that matched a measure is left matched and its note says
    the name says INACTIVE.
    """
    if rows.empty or "fffs_location_name" not in rows:
        return rows
    inactive = rows["fffs_location_name"].fillna("").str.contains("inactive", case=False)
    if not inactive.any():
        return rows
    return _set_aside(rows, inactive, "inactive", "the location name says INACTIVE and nothing matched",
                      "the location name says INACTIVE")


_FALSE_WORDS = {"false", "f", "no", "n", "0", "0.0"}


def flag_out_of_scope(rows: pd.DataFrame, scope: pd.DataFrame) -> pd.DataFrame:
    """
    Set aside mapping rows that are known to be outside what the real-time feed covers (Welsh sites, for one),
    from a list the user supplies. `scope` has an fffs_id and/or telemetry_id column, and optionally:
      reason   the note given to the row;
      keep     TRUE or FALSE. With a Keep column only the rows marked FALSE are on the list (the others are
               kept and ignored), and a row marked FALSE is set aside EVEN IF IT MATCHED a measure: the list
               is the user's decision that the row is not wanted, and its link is removed;
      importsource  added to the note when there is no reason (Astro, say).
    Without a Keep column, a listed row that matched nothing gets the status out_of_scope with the reason as
    its note, and one that matched a measure stays matched, with a note. Columns are matched without regard
    to case, spaces or underscores (FFFS_ID, FFFS ID and fffs_id are the same).
    """
    if rows.empty or scope is None or scope.empty:
        return rows
    norm = {re.sub(r"[^a-z0-9]", "", str(c).lower()): c for c in scope.columns}
    fcol, tcol, rcol = norm.get("fffsid"), norm.get("telemetryid"), norm.get("reason")
    kcol, scol = norm.get("keep"), norm.get("importsource")
    if not fcol and not tcol:
        raise ValueError(f"The out-of-scope list needs an FFFS_ID or TelemetryID column. Columns present: {list(scope.columns)}")
    if kcol:
        # Only the rows marked FALSE are on the list. A blank Keep is not a decision, so it stays.
        scope = scope[scope[kcol].map(lambda v: (clean(v) or "").lower() in _FALSE_WORDS)].copy()
        if scope.empty:
            return rows
        if not rcol:
            scope["Reason"] = [("Keep = FALSE" + (f" ({clean(s)})" if scol and clean(s) else "")) for s in
                               (scope[scol] if scol else [None] * len(scope))]
            rcol = "Reason"
    fff = {clean(v) for v in scope[fcol]} - {None} if fcol else set()
    tel = {clean(v) for v in scope[tcol]} - {None} if tcol else set()
    reasons = {}
    for col, key in ((fcol, "fffs_id"), (tcol, "telemetry_id")):
        if col:
            for k, why in zip(scope[col], scope[rcol] if rcol else [None] * len(scope)):
                if clean(k):
                    reasons[(key, clean(k))] = clean(why) or "listed as out of scope"
    listed = rows["fffs_id"].map(clean).isin(fff) | rows["telemetry_id"].map(clean).isin(tel)
    if not listed.any():
        return rows

    def note(f, t):
        return reasons.get(("fffs_id", clean(f))) or reasons.get(("telemetry_id", clean(t))) or "listed as out of scope"
    return _set_aside(rows, listed, "out_of_scope", note, "listed as out of scope, but matched a measure",
                      override_matched=bool(kcol))


def match_mapping(mapping: pd.DataFrame, register: pd.DataFrame):
    """
    Match each mapping row to measures in the register.

    Returns:
        (rows, report). rows has OUTPUT_FIELDS, the two families, match_status, match_note and the
        candidate's measure_uri, station_reference, station_label, register_parameter,
        register_qualifier, period and unit_name (empty where nothing matched). A row that matched
        nothing is one row; an ambiguous row is one row per candidate. report counts each status,
        measures matched by more than one mapping row, and examples of each problem.
    """
    reg = prepare_register(register)
    by_station = {k: g for k, g in reg.groupby("station_key")}
    out, examples = [], {}

    def add(row, status, note, cand=None):
        rec = dict(row)
        rec["match_status"], rec["match_note"] = status, note
        for src, dst in (("measure_uri", "measure_uri"), ("station_reference", "station_reference"),
                         ("station_label", "station_label"), ("parameter", "register_parameter"),
                         ("qualifier", "register_qualifier"), ("period", "period"), ("unit_name", "unit_name")):
            rec[dst] = None if cand is None else _none(cand.get(src))
        out.append(rec)
        examples.setdefault(status, [])
        if len(examples[status]) < 5:
            examples[status].append(f"{row['telemetry_id']} {row['telemetry_parameter']}")

    for row in mapping.to_dict("records"):
        row = {k: _none(v) for k, v in row.items()}       # a missing value is None, never NaN, which is truthy
        tid, tf, cf = row["telemetry_id"], row["telemetry_family"], row["code_family"]
        if not tid:
            add(row, "no_telemetry_id", "the row has no TelemetryID")
            continue
        family, conflict = reconcile_family(tf, cf)
        if conflict:
            add(row, "conflict", f"TelemetryParameter says {tf} but ParameterID says {cf}")
            continue
        if family is None:
            add(row, "unknown_parameter", "neither TelemetryParameter nor ParameterID names a known parameter")
            continue
        station, id_note = by_station.get(tid), ""
        if station is None:
            underscored = _TG_SPACE.sub(r"\1_\2", tid)
            if underscored != tid:
                station = by_station.get(underscored)
                if station is not None:
                    id_note = f"TelemetryID matched as {underscored} (the space after _TG changed to an underscore)"
        if station is None:
            add(row, "no_station", "no measure in the register has this station reference")
            continue
        cands = station[station["family"] == family]
        if cands.empty:
            have = ", ".join(sorted({f or "unknown" for f in station["family"]}))
            add(row, "no_parameter", f"the station has no {family} measure (it has: {have})")
            continue
        by_file_qualifier = False
        if len(cands) > 1 and row["parameter_qualifier"]:
            same = cands[cands["qualifier_key"] == row["parameter_qualifier"].lower()]
            if len(same) >= 1:
                cands, by_file_qualifier = same, True
        if len(cands) > 1 and family == "level" and not by_file_qualifier:
            cands, pref_note = _prefer_stage_in_datum(cands)
            id_note = "; ".join(x for x in (id_note, pref_note) if x)
        if len(cands) == 1:
            add(row, "matched", id_note, cands.iloc[0].to_dict())
        else:
            for c in cands.to_dict("records"):
                add(row, "ambiguous", "; ".join(x for x in (f"{len(cands)} {family} measures fit", id_note) if x), c)

    rows = pd.DataFrame(out)
    rows = flag_inactive(rows)
    counts = rows["match_status"].value_counts().to_dict() if len(rows) else {}
    matched = rows[rows["match_status"] == "matched"] if len(rows) else rows
    shared = matched.groupby("measure_uri")["id"].count() if len(matched) else pd.Series(dtype=int)
    report = {"statuses": {k: int(v) for k, v in counts.items()},
              "measures_matched": int(matched["measure_uri"].nunique()) if len(matched) else 0,
              "measures_matched_by_several_rows": int((shared > 1).sum()),
              "examples": examples}
    return rows, report


def fallback_external_id(rows: pd.DataFrame, mapping: pd.DataFrame, scope: pd.DataFrame, register: pd.DataFrame):
    """
    Second chance for mapping rows that found no station: use the import list's ExternalID as the station reference.

    The mapping file's TelemetryID is sometimes not a station reference (a WISKI-style id, an old number) where the
    import list's ExternalID for the same FFFS_ID is (FFFS 2230_w1TH has TelemetryID 2230_w1TH and ExternalID
    2230TH). A row with status no_station or no_telemetry_id whose FFFS_ID is on the list, with an ExternalID that
    differs from its TelemetryID, is matched again with the ExternalID in its place, keeping its own parameter and
    qualifier. A retry that matches (or is ambiguous) replaces the row, keeps the file's TelemetryID and says in
    the note what happened; one that still finds nothing leaves the row as it was. Rows marked Keep = FALSE are
    never retried. Columns are matched without regard to case, spaces or underscores.

    Returns (rows, n): the rows, and the number of mapping rows newly matched.
    """
    if rows.empty or scope is None or scope.empty:
        return rows, 0
    norm = {re.sub(r"[^a-z0-9]", "", str(c).lower()): c for c in scope.columns}
    fcol, ecol, kcol = norm.get("fffsid"), norm.get("externalid"), norm.get("keep")
    if not fcol or not ecol:
        return rows, 0
    if kcol:
        scope = scope[~scope[kcol].map(lambda v: (clean(v) or "").lower() in _FALSE_WORDS)]
    ext = {}
    for f, e in zip(scope[fcol], scope[ecol]):
        if clean(f) and clean(e) and clean(f) not in ext:
            ext[clean(f)] = clean(e)
    stuck = rows[rows["match_status"].isin(["no_station", "no_telemetry_id"])].drop_duplicates("id")
    stuck = stuck[stuck["fffs_id"].map(lambda f: clean(f) in ext)]
    stuck = stuck[[ext[clean(f)] != clean(t) for f, t in zip(stuck["fffs_id"], stuck["telemetry_id"])]]
    if stuck.empty:
        return rows, 0
    sub = mapping[mapping["id"].isin(stuck["id"])].copy()
    original = dict(zip(sub["id"], sub["telemetry_id"]))
    sub["telemetry_id"] = [ext[clean(f)] for f in sub["fffs_id"]]
    retried, _ = match_mapping(sub, register)
    retried = retried[retried["match_status"].isin(["matched", "ambiguous"])].copy()
    if retried.empty:
        return rows, 0
    retried["match_note"] = [
        "; ".join(x for x in (f"matched through the import list's ExternalID {t} (the file's TelemetryID {original[i] or 'is blank'} found no station)",
                              n or "") if x)
        for i, t, n in zip(retried["id"], retried["telemetry_id"], retried["match_note"])]
    retried["telemetry_id"] = retried["id"].map(original)
    keep_rows = rows[~rows["id"].isin(retried["id"])]
    out = pd.concat([keep_rows, retried[[c for c in rows.columns if c in retried.columns]]], ignore_index=True)
    return out, int(retried[retried["match_status"] == "matched"]["id"].nunique())


def settle_by_readings(rows: pd.DataFrame, activity: pd.DataFrame, now, recent_days: int = 30):
    """
    Settle ambiguous rows using which candidate measures actually have readings, and add n_readings,
    first_reading and last_reading to every row that has a measure.

    Args:
        rows:      match_mapping() output.
        activity:  measure_uri, n_readings, first_reading, last_reading, for measures that have any
                   readings (a measure absent from it has none).
        now:       the time recent_days is counted back from.
        recent_days: how recent a reading must be to separate two measures that both have data.

    Returns:
        (rows, settled). The ambiguous lines of a settled row collapse to its chosen measure, which
        becomes matched with a note; settled is the number of mapping rows settled this way.
    """
    r = rows.copy().reset_index(drop=True)
    act = activity.drop_duplicates("measure_uri").set_index("measure_uri") if len(activity) else None
    has_uri = r["measure_uri"].notna()
    for col in ("n_readings", "first_reading", "last_reading"):
        r[col] = None
        if act is not None and col in act:
            r.loc[has_uri, col] = r.loc[has_uri, "measure_uri"].map(act[col])
    r["n_readings"] = pd.to_numeric(r["n_readings"], errors="coerce").where(has_uri).fillna(0).where(has_uri)
    last = pd.to_datetime(r["last_reading"], errors="coerce", utc=True).dt.tz_localize(None)
    cutoff = pd.Timestamp(now).tz_localize(None) - pd.Timedelta(days=recent_days) if pd.Timestamp(now).tzinfo else pd.Timestamp(now) - pd.Timedelta(days=recent_days)
    drop, settled = [], 0
    for rid, g in r[r["match_status"] == "ambiguous"].groupby("id"):
        with_data = g[g["n_readings"] > 0]
        chosen, why = None, None
        if len(with_data) == 1:
            chosen, why = with_data.index[0], "settled by readings: the other candidate(s) have none"
        elif len(with_data) > 1:
            recent = with_data[last.loc[with_data.index] >= cutoff]
            if len(recent) == 1:
                chosen, why = recent.index[0], f"settled by readings: the other candidate(s) have none in the last {recent_days} days"
        if chosen is None:
            continue
        r.loc[chosen, "match_status"], r.loc[chosen, "match_note"] = "matched", why
        drop.extend(i for i in g.index if i != chosen)
        settled += 1
    return r.drop(index=drop).reset_index(drop=True), settled
