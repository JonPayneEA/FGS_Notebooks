# =============================================================================
# utils/explore.py
# Pure pandas helpers for explore/Explore_live_readings. No Spark, no widgets, no
# plotting, so they can be tested without Databricks. All times are timezone-aware UTC.
# =============================================================================

import pandas as pd

ALL = "All"


def _station_name(register: pd.DataFrame) -> pd.Series:
    """The station's name: the API label, else the archive label, else the reference."""
    name = register["station_label"] if "station_label" in register else pd.Series(index=register.index, dtype=object)
    name = name.where(name.notna() & (name.astype(str).str.strip() != ""))
    if "label" in register:
        name = name.fillna(register["label"])
    if "station_reference" in register:
        name = name.fillna(register["station_reference"])
    return name.fillna("(unnamed station)")


def prepare_register(register: pd.DataFrame) -> pd.DataFrame:
    """
    The measure register with a `station` column (a readable name) and a `measure_name`
    column saying what the measure is, for example "level, stage, 15 min (mASD)".
    """
    r = register.copy()
    r["station"] = _station_name(r)
    for col in ("parameter", "qualifier", "unit_name", "river_name", "ea_area_name", "period"):
        if col not in r:
            r[col] = None

    def label(row):
        parts = [str(row["parameter"]) if pd.notna(row["parameter"]) else "measure"]
        if pd.notna(row["qualifier"]) and str(row["qualifier"]).strip():
            parts.append(str(row["qualifier"]))
        text = ", ".join(parts)
        unit = f" ({row['unit_name']})" if pd.notna(row["unit_name"]) and str(row["unit_name"]).strip() else ""
        return text + unit

    r["measure_name"] = r.apply(label, axis=1)
    return r


def filter_register(register: pd.DataFrame, area=ALL, river=ALL, parameter=ALL, station=ALL) -> pd.DataFrame:
    """Rows matching every choice that is not 'All'."""
    r = register
    if area != ALL:
        r = r[r["ea_area_name"] == area]
    if river != ALL:
        r = r[r["river_name"] == river]
    if parameter != ALL:
        r = r[r["parameter"] == parameter]
    if station != ALL:
        r = r[r["station"] == station]
    return r


def _values(frame: pd.DataFrame, column: str) -> list:
    return [ALL] + sorted(str(v) for v in frame[column].dropna().unique())


def options(register: pd.DataFrame, area=ALL, river=ALL, parameter=ALL) -> dict:
    """
    The choices for each drop-down, each narrowed by the OTHER choices, so a selection
    can never lead to an empty list. Stations are narrowed by all three.
    """
    return {
        "areas":      _values(filter_register(register, river=river, parameter=parameter), "ea_area_name"),
        "rivers":     _values(filter_register(register, area=area, parameter=parameter), "river_name"),
        "parameters": _values(filter_register(register, area=area, river=river), "parameter"),
        "stations":   _values(filter_register(register, area=area, river=river, parameter=parameter), "station"),
    }


def archive_live_boundary(max_archive_date):
    """
    Where the archive ends and the live tail begins: midnight UTC at the end of the last
    day held in the archive (the bronze table). None when nothing is archived yet.
    """
    if max_archive_date is None or pd.isna(max_archive_date):
        return None
    day = pd.Timestamp(max_archive_date)
    day = day.tz_localize("UTC") if day.tzinfo is None else day.tz_convert("UTC")
    return day.normalize() + pd.Timedelta(days=1)


def window_start(now, days: int):
    """The start of a window of `days` days ending at `now`."""
    now = pd.Timestamp(now)
    now = now.tz_localize("UTC") if now.tzinfo is None else now.tz_convert("UTC")
    return now - pd.Timedelta(days=int(days))


def to_utc(series: pd.Series) -> pd.Series:
    """A time column as timezone-aware UTC, whatever Spark handed back."""
    s = pd.to_datetime(series, utc=True)
    return s


def summarise(readings: pd.DataFrame, boundary, now) -> dict:
    """
    A few facts to print under the chart: how many readings fall in the archive and in
    the live tail, the newest reading, and how old it is.
    """
    if readings.empty:
        return {"readings": 0, "archive": 0, "live": 0, "newest": None, "age_minutes": None}
    t = to_utc(readings["date_time"])
    live = int((t >= boundary).sum()) if boundary is not None else 0
    newest = t.max()
    return {
        "readings": int(len(readings)),
        "archive": int(len(readings)) - live,
        "live": live,
        "newest": newest,
        "age_minutes": round((pd.Timestamp(now) - newest).total_seconds() / 60, 1),
    }
