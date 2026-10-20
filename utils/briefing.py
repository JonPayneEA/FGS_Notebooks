# =============================================================================
# utils/briefing.py
# Pure Python and pandas for MP briefing drafts. No Spark, so it can be tested
# without Databricks. Used by jobs/20_mp_briefing_drafts (the latest statement)
# and jobs/24_backfill_mp_briefing_drafts (earlier statements).
# =============================================================================

import re
import unicodedata
from datetime import date

import pandas as pd

RANK_NAME = {1: "Very Low", 2: "Low", 3: "Medium", 4: "High"}
RANK_OF = {name: rank for rank, name in RANK_NAME.items()}
DAY_RANK_COLUMNS = [f"fgs_rank_day{n}" for n in range(1, 6)]


def long_date(d) -> str:
    return f"{d:%A} {d.day} {d:%B %Y}"


def source_name(s) -> str:
    return {"river": "River flooding", "coastal": "Coastal flooding",
            "surface": "Surface water flooding", "ground": "Groundwater flooding"}.get(s, str(s).title())


def optional_bool(value):
    """True or False, or None when the value is not known (a warning count that is not held)."""
    if value is None or (not isinstance(value, (bool, str)) and pd.isna(value)):
        return None
    return bool(value)


def _count(value) -> int:
    return 0 if value is None or pd.isna(value) else int(value)


def day_dates(pairs) -> dict:
    """
    The date of each forecast day, 1 to 5, from (day_index, forecast_date) pairs.
    Days nothing is forecast on are filled by counting from a known day.
    """
    known = {}
    for index, when in pairs:
        if index is not None and when and not pd.isna(index):
            known[int(index)] = date.fromisoformat(str(when)[:10])
    if known:
        k, d = next(iter(sorted(known.items())))
        for n in range(1, 6):
            known.setdefault(n, date.fromordinal(d.toordinal() + (n - k)))
    return known


def build_draft(c, detail, areas, day_date, areas_sentence=None):
    """
    The subject and plain-text body of one MP briefing.

    Args:
        c:         One constituency's situation (a Spark Row, dict or pandas row).
        detail:    Rows with source, risk_rank, forecast_date, impact_label and
                   likelihood_label behind this constituency's forecast.
        areas:     Rows with source, risk_rank, ea_area_code and ea_area_type for the
                   flood warning and alert areas under its polygons.
        day_date:  {1..5: date}, from day_dates().
        areas_sentence: Function giving the sentence about areas under the forecast.
                   Defaults to utils.helpers.areas_under_forecast_sentence.
    """
    if areas_sentence is None:
        from utils.helpers import areas_under_forecast_sentence as areas_sentence

    worst      = int(c["fgs_worst_rank"])
    worst_date = date.fromisoformat(str(c["fgs_worst_date"])[:10])
    issued     = str(c["fgs_issued_at"] or "")[:10]

    # The impact and likelihood at the worst risk, on the day it first occurs.
    lines = sorted({
        f"  - {source_name(r['source'])}: {r['impact_label']} impact, {r['likelihood_label']} likelihood"
        for r in detail
        if int(r["risk_rank"]) == worst and str(r["forecast_date"])[:10] == worst_date.isoformat()
    })

    day_lines = []
    for n in range(1, 6):
        rank  = _count(c[f"fgs_rank_day{n}"])
        label = RANK_NAME.get(rank, "No risk flagged")
        when  = long_date(day_date[n]) if n in day_date else f"Day {n}"
        day_lines.append(f"  - Day {n}, {when}: {label}")

    parts = [
        f"Dear {c['mp_name']},",
        "",
        f"The Flood Guidance Statement issued on {issued} shows a {RANK_NAME[worst]} flood "
        f"risk for {c['constituency_name']} on {long_date(worst_date)}.",
        "",
        "Behind that risk:",
        *lines,
        "",
        "The forecast for each of the five days:",
        *day_lines,
    ]
    if optional_bool(c["fgs_escalated"]):
        parts += ["", "This is higher than in the previous statement."]

    # Potential warnings and alerts: areas under the worst-risk polygons.
    worst_sources = {s.strip() for s in str(c["fgs_worst_sources"] or "").split(",") if s.strip()}
    under = {(r["ea_area_code"], r["ea_area_type"])
             for r in areas
             if int(r["risk_rank"]) == worst and r["source"] in worst_sources}
    areas_line = areas_sentence(
        len({code for code, kind in under if kind == "flood_warning"}),
        len({code for code, kind in under if kind == "flood_alert"}),
        RANK_NAME[worst], worst_sources)
    if areas_line:
        parts += ["", areas_line]

    # Warnings and alerts in force. Not held for past statements, where the counts are
    # None and this sentence is left out rather than saying "0".
    n_w = _count(c["severe_warnings"]) + _count(c["warnings"])
    n_a = _count(c["alerts"])
    if n_w or n_a:
        parts += ["", f"The Environment Agency currently has {n_w} flood warning(s) and "
                      f"{n_a} flood alert(s) in force in the constituency."]
    parts += [
        "",
        "The Flood Guidance Statement forecasts the risk of flooding across England for "
        "the next five days. It is not a flood warning. Warnings and alerts for "
        "individual areas are issued separately by the Environment Agency.",
        "",
        "[Sender name and contact details to be added on review]",
    ]
    subject = f"Flood forecast: {RANK_NAME[worst]} risk for {c['constituency_name']} on {long_date(worst_date)}"
    return subject, "\n".join(parts)


def name_key(text) -> str:
    """Match key for a constituency name: lower case, accents folded, '&' as 'and', letters and digits only."""
    text = unicodedata.normalize("NFKD", str(text).lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).replace("&", "and")
    return re.sub(r"[^a-z0-9]", "", text)


def situation_from_intersections(inter, all_const, mp, escalated_ids, statement_id, issued_at, min_pct):
    """
    One row per constituency for ANY statement, from the intersection rows the pipeline
    keeps for every statement. It mirrors what job 19 does for the forecast half.

    Polygons covering less than min_pct of a constituency are ignored. The worst rank is
    taken per constituency, forecast day and source, then per day. The worst rank over
    the five days, the first date it occurs and the sources behind it follow.

    Warnings and alerts in force are not kept historically, so those columns are None.

    Args:
        inter:         constituency_id, forecast_date, day_index, source, risk_level,
                       intersection_pct, for ONE statement.
        all_const:     constituency_id, constituency_name for every constituency, so a
                       quiet one still gets a row.
        mp:            constituency_name, member_id, name, email. Today's MPs. Where a name
                       appears twice the lowest member_id is used.
        escalated_ids: Set of constituency_ids that rose against the previous statement,
                       or None when that statement was never compared.
    """
    out = all_const[["constituency_id", "constituency_name"]].drop_duplicates("constituency_id").copy()
    for column in DAY_RANK_COLUMNS:
        out[column] = 0
    out["fgs_worst_rank"] = 0
    out["fgs_worst_date"] = None
    out["fgs_worst_sources"] = None

    inter = inter.copy()
    if len(inter):
        inter = inter[inter["intersection_pct"].isna() | (inter["intersection_pct"] >= min_pct)]
        inter["rk"] = inter["risk_level"].map(RANK_OF)
        inter = inter[inter["rk"].notna()]
    if len(inter):
        inter["rk"] = inter["rk"].astype(int)
        inter["forecast_date"] = inter["forecast_date"].astype(str).str[:10]
        cell = inter.groupby(["constituency_id", "forecast_date", "day_index", "source"], as_index=False)["rk"].max()
        by_day = cell.groupby(["constituency_id", "day_index"], as_index=False)["rk"].max()
        for n in range(1, 6):
            day = by_day[by_day["day_index"] == n].set_index("constituency_id")["rk"]
            out[f"fgs_rank_day{n}"] = out["constituency_id"].map(day).fillna(0).astype(int)
        worst = cell.groupby("constituency_id")["rk"].max().rename("w")
        top = cell.join(worst, on="constituency_id")
        top = top[top["rk"] == top["w"]]
        first = top.groupby("constituency_id").agg(
            fgs_worst_date=("forecast_date", "min"),
            fgs_worst_sources=("source", lambda s: ", ".join(sorted(set(s)))))
        out["fgs_worst_rank"] = out["constituency_id"].map(worst).fillna(0).astype(int)
        out["fgs_worst_date"] = out["constituency_id"].map(first["fgs_worst_date"])
        out["fgs_worst_sources"] = out["constituency_id"].map(first["fgs_worst_sources"])

    if escalated_ids is None:
        out["fgs_escalated"] = None
    else:
        out["fgs_escalated"] = out["constituency_id"].isin(escalated_ids)

    mp = mp.sort_values("member_id").copy()
    mp["key"] = mp["constituency_name"].map(name_key)
    mp = mp.drop_duplicates("key").set_index("key")
    out["key"] = out["constituency_name"].map(name_key)
    out["mp_member_id"] = out["key"].map(mp["member_id"])
    out["mp_name"] = out["key"].map(mp["name"])
    out["mp_email"] = out["key"].map(mp["email"])
    out["mp_matched"] = out["mp_name"].notna()
    out = out.drop(columns="key")

    out["severe_warnings"] = None
    out["warnings"] = None
    out["alerts"] = None
    out["has_active_warning"] = None
    out["fgs_statement_id"] = int(statement_id)
    out["fgs_issued_at"] = str(issued_at)
    return out.reset_index(drop=True)


def check_setting_is_current(config_path, name, loaded):
    """
    Stop a notebook that is running on an out-of-date setting.

    Databricks keeps an imported module until the Python session restarts, so after
    config.py changes (a Git pull, or an edit) a notebook that was already open still
    holds the old value and quietly does the wrong thing. This reads the setting's line
    straight from config.py and raises if it differs from the value the notebook loaded.
    Nothing happens when the file cannot be read or the line is not a plain number.
    """
    try:
        with open(config_path, encoding="utf-8") as handle:
            text = handle.read()
    except OSError:
        return
    match = re.search(rf"^{re.escape(name)}\s*=\s*(-?\d+)\s*(?:#.*)?$", text, flags=re.MULTILINE)
    if not match or int(match.group(1)) == int(loaded):
        return
    raise RuntimeError(
        f"{name} is {match.group(1)} in config.py but this notebook loaded {loaded}: the Python "
        f"session is holding an old copy. Run dbutils.library.restartPython(), then run the "
        f"notebook again from the top."
    )
