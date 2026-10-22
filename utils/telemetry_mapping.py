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
# settle, not for this code to guess. The register's parameter and qualifier are reduced to the
# same families. A station with one measure is not matched on the station alone, because that
# is the mistake this replaces.
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


def prepare_mapping(raw: pd.DataFrame):
    """
    The mapping file with clean columns (OUTPUT_FIELDS), the two families and a note on what
    the file got wrong. Returns (mapping, report); report counts rows, rows with no TelemetryID
    and WISKI ids Excel turned into scientific notation, which have lost digits and are blanked.
    """
    cols = {}
    for c in raw.columns:
        key = re.sub(r"[^a-z0-9_]", "", str(c).lower())
        if key in FILE_COLUMNS and FILE_COLUMNS[key] not in cols.values():
            cols[c] = FILE_COLUMNS[key]
    m = raw.rename(columns=cols)[[c for c in cols.values()]].copy()
    for field in OUTPUT_FIELDS:
        if field not in m:
            m[field] = None
    m = m[OUTPUT_FIELDS].copy()
    for field in OUTPUT_FIELDS:
        m[field] = m[field].map(clean)
    scientific = m["wiski_id"].map(lambda v: bool(v) and bool(_SCIENTIFIC.match(v)))
    m.loc[scientific, "wiski_id"] = None
    m["telemetry_family"] = m["telemetry_parameter"].map(family_of_text)
    m["code_family"] = m["parameter_id"].map(family_of_code)
    report = {"rows": int(len(m)), "no_telemetry_id": int(m["telemetry_id"].isna().sum()),
              "wiski_id_in_scientific_notation": int(scientific.sum())}
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
        if tf and cf and tf != cf:
            add(row, "conflict", f"TelemetryParameter says {tf} but ParameterID says {cf}")
            continue
        family = tf or cf
        if family is None:
            add(row, "unknown_parameter", "neither TelemetryParameter nor ParameterID names a known parameter")
            continue
        station = by_station.get(tid)
        if station is None:
            add(row, "no_station", "no measure in the register has this station reference")
            continue
        cands = station[station["family"] == family]
        if cands.empty:
            have = ", ".join(sorted({f or "unknown" for f in station["family"]}))
            add(row, "no_parameter", f"the station has no {family} measure (it has: {have})")
            continue
        if len(cands) > 1 and row["parameter_qualifier"]:
            same = cands[cands["qualifier_key"] == row["parameter_qualifier"].lower()]
            if len(same) >= 1:
                cands = same
        if len(cands) == 1:
            add(row, "matched", "", cands.iloc[0].to_dict())
        else:
            for c in cands.to_dict("records"):
                add(row, "ambiguous", f"{len(cands)} {family} measures fit", c)

    rows = pd.DataFrame(out)
    counts = rows["match_status"].value_counts().to_dict() if len(rows) else {}
    matched = rows[rows["match_status"] == "matched"] if len(rows) else rows
    shared = matched.groupby("measure_uri")["id"].count() if len(matched) else pd.Series(dtype=int)
    report = {"statuses": {k: int(v) for k, v in counts.items()},
              "measures_matched": int(matched["measure_uri"].nunique()) if len(matched) else 0,
              "measures_matched_by_several_rows": int((shared > 1).sum()),
              "examples": examples}
    return rows, report
