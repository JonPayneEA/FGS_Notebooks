# =============================================================================
# utils/threshold_link.py
# Pure pandas: link the Observed flood warning thresholds to the gauges the real-time import holds. No Spark,
# so it can be tested without Databricks. Used by jobs/28_link_thresholds_to_gauges, which runs in the
# restricted deployment because the thresholds are restricted.
#
# THE INPUT. The EA thresholds file, one row per threshold: ID, Location (the gauge's FFFS_ID), the basis
# (Observed or Forecast), Value, Unit (mALD, mAOD ...), Category and CatDesc (Flood Warning, Forecaster
# Activity, Information), Type (ACT, ACTCON, RES, INFO), Purpose, Parameter (H.obs, H.obs.us, H.rated.fc ...)
# and the target lead time.
#
# WHICH ROWS. Only Observed rows whose CatDesc is Flood Warning. Every Type and Purpose inside that
# (ACTCON and RES, the alert and warning purposes) is kept and carried as columns, not filtered, so a later step can
# choose among them.
#
# THE LINK. A threshold goes to a gauge measure on two things, as the telemetry mapping does:
#   1. Location = the mapping's fffs_id, compared with id_key (case and leading zeros ignored).
#   2. Parameter's family: H is level, Q is flow, and so on (family_of_code), against the measure's family.
# Then the UNIT must agree. The threshold's Unit (mALD) is compared with the measure's unit_name, which for a
# level is its datum (mAOD, mASD, mALD ...). The two are compared after case and spacing are ignored, and
# through an alias table for spellings that differ. A bare "m" (no datum named) is taken as mALD, mASD or mAOD by default, only when
# the gauge has no measure in exactly the threshold's unit; unit_match says exact, datum_assumed (mALD and mASD taken as one), alias (a bare m) or converted (the value moved between mAOD and a local datum with z, see value_in_measure_unit). A measure whose unit does not agree is never linked: the
# row says unit_mismatch and names the units the gauge does have, so the alias table can be filled in
# from the profile rather than guessed.
#
# STATUS of each threshold:
#   linked         one measure agrees on gauge, family and unit
#   linked_chosen  several measures agree; one was chosen (instantaneous, then most readings, then URI) and the number
#                  of candidates is n_candidates
#   unit_mismatch  the gauge has measures of that family, none in the threshold's unit
#   no_measure     the gauge is in the mapping but has no measure of that family with a measure_uri
#   no_gauge       the Location is not in the mapping
#   no_family      the Parameter names no family this code knows
# =============================================================================

import re

import pandas as pd

from utils.telemetry_mapping import clean, family_of_code, family_of_text, id_key

# Column names tried, in order, when the notebook is not told which column holds what.
COLUMN_GUESSES = {
    "location": ["location", "fffsid", "fffs"],
    "value": ["value", "thresholdvalue"],
    "unit": ["unit", "units"],
    "catdesc": ["catdesc", "categorydescription", "categorydesc"],
    "type": ["type", "thresholdkind"],
    "purpose": ["purpose"],
    "parameter": ["parameter", "parameterid"],
    "lead": ["targetleadtimeminutes", "targetleadtime"],
    "target_area": ["targetareaid", "targetarea"],
    "category": ["category", "cat"],
    "season": ["season"],
    "z": ["z", "gaugezero", "zero"],
    "label_prefix": ["labeldescriptionprefix", "labeldescprefix", "labelprefix", "labeldescription", "labeldesc"],
    "id": ["id", "thresholdid"],
    "name": ["fffslocname", "locationname"],
}
REQUIRED = ("location", "value", "unit", "catdesc", "parameter")


def _norm(name) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def unit_key(text):
    """A unit for comparison: lower case, spaces and punctuation dropped. None for a blank."""
    t = clean(text)
    return re.sub(r"[^a-z0-9]", "", t.lower()) if t else None


def resolve_columns(raw: pd.DataFrame, columns: dict = None, basis_label: str = "Observed") -> dict:
    """
    Which column of the thresholds file holds each field. `columns` names them (any case, spaces and underscores ignored);
    a field not named is looked for among COLUMN_GUESSES. The basis column (Observed or Forecast) has no fixed name
    in the file, so it is the column, among those whose name starts with "threshold" (or is named in `columns["basis"]`),
    whose values include the basis label. Raises when a required field cannot be found.
    """
    present = {_norm(c): c for c in raw.columns}
    out = {}
    for field, guesses in COLUMN_GUESSES.items():
        named = (columns or {}).get(field)
        if named:
            if _norm(named) not in present:
                raise ValueError(f"The {field} column '{named}' is not in the thresholds file. Columns present: {list(raw.columns)}")
            out[field] = present[_norm(named)]
        else:
            out[field] = next((present[g] for g in guesses if g in present), None)
    if out.get("label_prefix") is None:               # the name is not certain: any column whose name has both "label" and "prefix"
        out["label_prefix"] = next((c for c in raw.columns if "label" in _norm(c) and "prefix" in _norm(c)), None)
    named = (columns or {}).get("basis")
    if named:
        if _norm(named) not in present:
            raise ValueError(f"The basis column '{named}' is not in the thresholds file. Columns present: {list(raw.columns)}")
        out["basis"] = present[_norm(named)]
    else:
        lab = basis_label.strip().lower()
        pool = [c for c in raw.columns if _norm(c).startswith("threshold")] or list(raw.columns)
        out["basis"] = next((c for c in pool if raw[c].map(lambda v: (clean(v) or "").lower()).eq(lab).any()), None)
    missing = [f for f in REQUIRED + ("basis",) if out[f] is None]
    if missing:
        raise ValueError(f"Could not find the column for {missing} in the thresholds file. Name them in the widgets. "
                         f"Columns present: {list(raw.columns)}")
    return out


def prepare_thresholds(raw: pd.DataFrame, columns: dict = None, basis_label: str = "Observed", catdesc_label: str = "Flood Warning"):
    """
    The Observed, Flood Warning thresholds in a fixed shape: one row per threshold with threshold_id, fffs_id, key,
    parameter, family, unit, unit_key, value, type, purpose, category, target_lead_minutes and target_area. Rows that are
    not Observed or not Flood Warning, or have no Location or no numeric value, are dropped. Returns (thresholds, report);
    the report has the resolved columns, the basis and CatDesc values found, and the Unit and Parameter counts of the rows kept, which
    is what to read before filling in the alias table.
    """
    cols = resolve_columns(raw, columns, basis_label)

    def col(field):
        return raw[cols[field]] if cols.get(field) else pd.Series([None] * len(raw), index=raw.index)

    basis = col("basis").map(clean)
    catdesc = col("catdesc").map(clean)
    df = pd.DataFrame({"threshold_id": col("id").map(clean).values, "fffs_id": col("location").map(clean).values})
    df["key"] = df["fffs_id"].map(id_key)
    df["location_name"] = col("name").map(clean).values
    df["parameter"] = col("parameter").map(clean).values
    df["family"] = [family_of_code(p) or family_of_text(p) for p in df["parameter"]]
    df["unit"] = col("unit").map(clean).values
    df["unit_key"] = df["unit"].map(unit_key)
    df["value"] = pd.to_numeric(col("value"), errors="coerce").values
    df["type"] = col("type").map(clean).values
    df["purpose"] = col("purpose").map(clean).values
    df["category"] = col("category").map(clean).values
    df["season"] = col("season").map(clean).values
    df["z"] = pd.to_numeric(col("z"), errors="coerce").values
    df["label_prefix"] = col("label_prefix").map(clean).values
    df["target_lead_minutes"] = pd.to_numeric(col("lead"), errors="coerce").values
    df["target_area"] = col("target_area").map(clean).values
    keep = (basis.str.lower() == basis_label.strip().lower()).fillna(False) & (catdesc.str.lower() == catdesc_label.strip().lower()).fillna(False)
    report = {"columns": cols, "rows": int(len(df)),
              "basis_values": basis.value_counts(dropna=False).to_dict(), "catdesc_values": catdesc.value_counts(dropna=False).to_dict(),
              "kept_basis_and_catdesc": int(keep.sum())}
    df = df[keep.values]
    report["dropped_no_location"] = int(df["key"].isna().sum())
    report["dropped_no_value"] = int((df["key"].notna() & df["value"].isna()).sum())
    df = df[df["key"].notna() & df["value"].notna()].reset_index(drop=True)
    report["rows_kept"] = int(len(df))
    report["units"] = df["unit"].value_counts(dropna=False).to_dict()
    report["parameters"] = df["parameter"].value_counts(dropna=False).to_dict()
    report["types"] = df["type"].value_counts(dropna=False).to_dict()
    return df, report


# Units that are taken to mean the same without being told. The register often gives a level in plain "m" with no datum named,
# where the thresholds say mALD, mASD or mAOD. A bare "m" is only a fallback: a measure whose unit is exactly the threshold's is always preferred,
# and a link made through an alias says so (unit_match = alias).
# mALD and mASD are taken as one datum (the user's decision for this data): they link with unit_match = datum_assumed, which ranks
# below an exact match and above the bare-m fallback. mAOD is never taken for either.
DATUM_EQUIVALENTS = {"mald": {"masd"}, "masd": {"mald"}}
DEFAULT_ALIASES = {"mald": {"m", "masd"}, "masd": {"m", "mald"}, "maod": {"m"}, "m": {"mald", "masd", "maod"}}


def prepare_aliases(aliases: pd.DataFrame = None) -> dict:
    """
    {threshold unit key: set of measure unit keys it also means}. `aliases` has two columns, threshold_unit and
    measure_unit (any case, spaces and underscores ignored); each row also works the other way round. They are added to
    DEFAULT_ALIASES (mALD and m). None or empty gives the defaults.
    """
    out = {k: set(v) for k, v in DEFAULT_ALIASES.items()}
    if aliases is None or len(aliases) == 0:
        return out
    present = {_norm(c): c for c in aliases.columns}
    a, b = present.get("thresholdunit"), present.get("measureunit")
    if a is None or b is None:
        raise ValueError(f"The alias table needs threshold_unit and measure_unit columns. Columns present: {list(aliases.columns)}")
    for x, y in zip(aliases[a].map(unit_key), aliases[b].map(unit_key)):
        if x and y:
            out.setdefault(x, set()).add(y)
            out.setdefault(y, set()).add(x)
    return out


def _unit_agrees(threshold_unit, measure_unit, aliases) -> bool:
    t, m = unit_key(threshold_unit), unit_key(measure_unit)
    return bool(t) and bool(m) and (t == m or m in aliases.get(t, ()))


def _order(cands: pd.DataFrame) -> pd.DataFrame:
    """Candidates best first: instantaneous, then most readings, then URI."""
    inst = cands["register_value_type"].map(lambda v: 0 if (clean(v) or "").lower() == "instantaneous" else 1) \
        if "register_value_type" in cands else pd.Series(1, index=cands.index)
    n = pd.to_numeric(cands["n_readings"], errors="coerce").fillna(0) if "n_readings" in cands else pd.Series(0, index=cands.index)
    return cands.assign(_inst=inst, _n=n).sort_values(["_inst", "_n", "measure_uri"], ascending=[True, False, True])


# A threshold in one datum can be moved to the measure's datum with the gauge zero z (metres AOD of the gauge's zero), taking
# mALD and mASD as one local datum (see DATUM_EQUIVALENTS). mAOD threshold to a local measure: value minus z. Local threshold to an
# mAOD measure: value plus z. Only when z is a number, only when no measure agrees on unit, and flagged unit_match = converted.
_LOCAL = {"mald", "masd"}


def _conversion(threshold_unit_key, measure_unit_key, z, zero_z_is_missing=True):
    """
    The amount to add to the threshold value to put it in the measure's datum, or None when no conversion applies. A z of exactly 0
    is treated as "not set" by default (zero_z_is_missing): a gauge zero at precisely 0 mAOD is unlikely, and a 0 would leave an mAOD
    value (27.28) to be compared with local-datum readings.
    """
    if z is None or pd.isna(z) or (zero_z_is_missing and float(z) == 0.0):
        return None
    if threshold_unit_key == "maod" and measure_unit_key in _LOCAL:
        return -float(z)
    if threshold_unit_key in _LOCAL and measure_unit_key == "maod":
        return float(z)
    return None


_MEASURE_COLS = ["measure_uri", "station_reference", "station_label", "register_parameter", "register_qualifier", "period",
                 "unit_name", "register_value_type", "n_readings", "first_reading", "last_reading"]


def link_thresholds(thr: pd.DataFrame, mapping: pd.DataFrame, aliases: dict = None, zero_z_is_missing: bool = True) -> pd.DataFrame:
    """
    Each threshold with the gauge measure it links to and a status (see the top of the file). `mapping` is the
    telemetry mapping table (fffs_id, telemetry_family or code_family, and the matched measure's columns); only its rows
    with a measure_uri count. The result has every column of `thr` plus link_status, link_note, n_candidates and the chosen
    measure's columns (measure_uri, station_label, unit_name, register_value_type, n_readings ...).
    """
    aliases = DEFAULT_ALIASES if aliases is None else aliases
    m = mapping.copy()
    for c in _MEASURE_COLS:
        if c not in m:
            m[c] = None
    m["key"] = m["fffs_id"].map(id_key)
    fam = m["telemetry_family"] if "telemetry_family" in m else pd.Series([None] * len(m), index=m.index)
    codefam = m["code_family"] if "code_family" in m else pd.Series([None] * len(m), index=m.index)
    m["mfamily"] = [a if clean(a) else b for a, b in zip(fam, codefam)]
    by_key = {k: g for k, g in m.groupby("key")}

    rows = []
    for t in thr.to_dict("records"):
        out = dict(t)
        out.update({c: None for c in _MEASURE_COLS})
        out["n_candidates"] = 0
        out["unit_match"] = None
        out["value_in_measure_unit"] = t.get("value")
        out["z_used"] = None
        g = by_key.get(t["key"])
        if not t.get("family"):
            status, note = "no_family", f"Parameter {t.get('parameter')} names no family"
        elif g is None:
            status, note = "no_gauge", "Location is not in the telemetry mapping"
        else:
            cands = g[(g["mfamily"] == t["family"]) & g["measure_uri"].notna()]
            if cands.empty:
                status, note = "no_measure", f"the gauge has no {t['family']} measure in the register"
            else:
                agree = cands[[_unit_agrees(t.get("unit"), u, aliases) for u in cands["unit_name"]]]
                tk = unit_key(t.get("unit"))
                match_kind = "exact"
                if not agree.empty:
                    kind = pd.Series(["exact" if unit_key(u) == tk else "datum_assumed" if unit_key(u) in DATUM_EQUIVALENTS.get(tk, ()) else "alias"
                                      for u in agree["unit_name"]], index=agree.index)
                    for rank in ("exact", "datum_assumed", "alias"):                         # the best kind present wins
                        if (kind == rank).any():
                            agree, match_kind = agree[kind == rank], rank
                            break
                via_alias = match_kind != "exact"
                convert = None
                if agree.empty:
                    tk_ = unit_key(t.get("unit"))
                    conv = cands[[_conversion(tk_, unit_key(u), t.get("z"), zero_z_is_missing) is not None for u in cands["unit_name"]]]
                    if len(conv):
                        agree, match_kind, via_alias = conv, "converted", True
                        convert = _conversion(tk_, unit_key(_order(conv).iloc[0]["unit_name"]), t.get("z"), zero_z_is_missing)
                if agree.empty:
                    have = sorted({clean(u) or "(no unit)" for u in cands["unit_name"]})
                    why = ""
                    if unit_key(t.get("unit")) in ({"maod"} | _LOCAL):
                        z_ = t.get("z")
                        if z_ is None or pd.isna(z_):
                            why = "; no z to convert with"
                        elif zero_z_is_missing and float(z_) == 0.0:
                            why = "; z is 0, taken as not set"
                    status, note = "unit_mismatch", f"threshold unit {t.get('unit')}; the gauge's {t['family']} measures are in {', '.join(have)}{why}"
                else:
                    best = _order(agree)
                    out.update({c: best.iloc[0][c] for c in _MEASURE_COLS})
                    out["n_candidates"] = int(len(agree))
                    one = len(agree) == 1
                    status = "linked" if one else "linked_chosen"
                    note = "" if one else f"{len(agree)} measures agree on unit; chose the instantaneous one with the most readings"
                    if convert is not None:
                        out["value_in_measure_unit"] = round(float(t["value"]) + convert, 4)
                        out["z_used"] = t.get("z")
                        note = (note + "; " if note else "") + (f"threshold {t.get('unit')} {t['value']} converted to {clean(best.iloc[0]['unit_name'])} "
                                                                f"{out['value_in_measure_unit']} with z {t.get('z')}")
                    elif via_alias:
                        note = (note + "; " if note else "") + f"measure unit {clean(best.iloc[0]['unit_name'])} taken as the threshold's {t.get('unit')}"
                    out["unit_match"] = match_kind
        out["link_status"], out["link_note"] = status, note
        rows.append(out)
    cols = list(thr.columns) + ["link_status", "link_note", "n_candidates", "unit_match", "value_in_measure_unit", "z_used"] + _MEASURE_COLS
    return pd.DataFrame(rows, columns=cols)


def link_summary(linked: pd.DataFrame) -> dict:
    """Counts by status, and the distinct (threshold unit, gauge units) pairs behind each unit_mismatch, most common first."""
    out = {"by_status": linked["link_status"].value_counts().to_dict()}
    mm = linked[linked["link_status"] == "unit_mismatch"]
    out["unit_mismatches"] = mm.groupby(["unit", "link_note"]).size().sort_values(ascending=False).reset_index(name="n").to_dict("records") if len(mm) else []
    return out
