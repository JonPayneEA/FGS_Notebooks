# =============================================================================
# utils/census_load.py
# Pure pandas: read Census 2021 output-area tables (Nomis bulk downloads) and the English Indices of Deprivation 2025 into the shape
# the exposure grid needs. No Spark and no network here, so it can be tested without Databricks. jobs/29_load_census_deprivation does the
# downloading and the writing.
#
# CENSUS. A Nomis bulk table is a zip holding one CSV per geography level (census2021-ts001-oa.csv, -lsoa.csv ...). Each CSV has date,
# geography, geography code, then one column per category ("Age: Aged 65 to 69 years"). The column wording can change between releases,
# so columns are found by what they say, and a table that does not give a total says so instead of guessing.
#   population   TS001, usual residents, all categories
#   households   TS041, number of households
#   age65plus    TS007A, the five-year bands from 65 to 69 upwards (summed; the bands are found by their text)
# IoD2025. The "all ranks, scores, deciles and population denominators" file (File 7), one row per LSOA. Columns are found by text, not
# position.
# =============================================================================

import io
import re
import zipfile

import pandas as pd


def _norm(text) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip()


def read_nomis_zip(content: bytes, level: str = "oa") -> pd.DataFrame:
    """The CSV for one geography level from a Nomis bulk zip held as bytes (the file whose name ends -<level>.csv)."""
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        names = [n for n in z.namelist() if n.lower().endswith(f"-{level.lower()}.csv")]
        if not names:
            raise ValueError(f"No file ending -{level}.csv in the zip. It holds: {z.namelist()[:12]}")
        with z.open(names[0]) as f:
            return pd.read_csv(f)


def _code_column(df: pd.DataFrame) -> str:
    for c in df.columns:
        if _norm(c) == "geography code":
            return c
    raise ValueError(f"No 'geography code' column. Columns present: {list(df.columns)[:10]}")


def _total_column(df: pd.DataFrame, prefer: str = "total") -> str:
    """The all-categories column: the one whose category text is 'Total' (first match)."""
    cand = [c for c in df.columns if re.search(rf"(^|[:;] ?){prefer}\b", str(c), re.I) and "geography" not in str(c).lower() and str(c).lower() not in ("date",)]
    if not cand:
        raise ValueError(f"No '{prefer}' column. Columns present: {list(df.columns)[:10]}")
    return cand[0]


def age65plus_columns(df: pd.DataFrame) -> list:
    """The age-band columns for 65 and over: 'Aged 65 to 69 years' up to 'Aged 85 years and over'."""
    out = []
    for c in df.columns:
        t = str(c).lower()
        m = re.search(r"aged (\d+)(?: to (\d+))?(?: years)?(?: and (?:over|under))?", t)
        if not m or "age" not in t:
            continue
        lo = int(m.group(1))
        if "and under" in t:
            continue
        if lo >= 65:
            out.append(c)
    return out


def census_oa_table(ts001: pd.DataFrame, ts007a: pd.DataFrame, ts041: pd.DataFrame = None):
    """
    One row per output area: oa, population, households, age65plus, share_65plus. Returns (table, report). The report says which columns
    were used and flags an age table whose bands do not add up to the total (a sign a band was missed).
    """
    out = ts001[[_code_column(ts001), _total_column(ts001)]].copy()
    out.columns = ["oa", "population"]
    rep = {"population_column": _total_column(ts001)}
    a_code = _code_column(ts007a)
    bands = age65plus_columns(ts007a)
    if not bands:
        raise ValueError(f"No age 65 and over columns found. Columns present: {list(ts007a.columns)[:12]}")
    age = pd.DataFrame({"oa": ts007a[a_code], "age65plus": ts007a[bands].sum(axis=1)})
    rep["age_columns"] = bands
    try:
        tot = _total_column(ts007a)
        age["age_total"] = ts007a[tot].to_numpy()
        rep["age_total_column"] = tot
    except ValueError:
        pass
    out = out.merge(age, on="oa", how="left")
    if ts041 is not None:
        h = ts041[[_code_column(ts041), _total_column(ts041)]].copy()
        h.columns = ["oa", "households"]
        out = out.merge(h, on="oa", how="left")
        rep["households_column"] = _total_column(ts041)
    else:
        out["households"] = float("nan")
    if "age_total" in out:
        off = (out["age_total"] - out["population"]).abs()
        rep["age_total_differs_from_population_in"] = int((off > 0.5).sum())
        out = out.drop(columns=["age_total"])
    out["share_65plus"] = (out["age65plus"] / out["population"]).where(out["population"] > 0)
    for c in ("population", "households", "age65plus"):
        out[c] = pd.to_numeric(out[c], errors="coerce")
    rep["rows"] = int(len(out)); rep["england_rows"] = int(out["oa"].astype(str).str.startswith("E").sum())
    return out, rep


def find_iod_links(html: str) -> list:
    """Links on the gov.uk statistics page that look like the IoD2025 'all ranks, scores, deciles' file (File 7), best first."""
    links = re.findall(r'href="([^"]+\.(?:csv|xlsx|ods))"', html, flags=re.I)
    def score(u):
        s = u.lower()
        return (("file_7" in s or "file 7" in s) * 4 + ("all_ranks" in s or "all ranks" in s or "scores" in s) * 2 + ("deciles" in s) * 1 + s.endswith(".csv") * 1)
    return sorted(set(links), key=score, reverse=True)


def iod_lsoa_table(raw: pd.DataFrame):
    """
    One row per LSOA from the IoD2025 file: lsoa, imd_score, imd_rank (1 most deprived), imd_decile (1 most deprived 10 per cent), and each
    domain's decile where present (domain_<name>_decile). Columns are found by their text. Returns (table, report).
    """
    cols = {c: _norm(c) for c in raw.columns}
    def pick(*pats, exclude=()):
        for c, n in cols.items():
            if all(re.search(p, n) for p in pats) and not any(re.search(e, n) for e in exclude):
                return c
        return None
    code = pick(r"lsoa", r"code")
    imd = r"index of multiple deprivation|^imd"
    score = pick(imd, r"score")
    rank = pick(imd, r"rank")
    dec = pick(imd, r"decile")
    if code is None or dec is None:
        raise ValueError(f"Could not find the LSOA code and IMD decile columns. Columns present: {list(raw.columns)[:14]}")
    out = pd.DataFrame({"lsoa": raw[code], "imd_decile": pd.to_numeric(raw[dec], errors="coerce")})
    if score: out["imd_score"] = pd.to_numeric(raw[score], errors="coerce")
    if rank: out["imd_rank"] = pd.to_numeric(raw[rank], errors="coerce")
    for c, n in cols.items():
        m = re.match(r"(income|employment|education.*|health.*|crime|barriers.*|living environment).* decile", n)
        if m and c != dec and "index of multiple" not in n:
            out["domain_" + re.sub(r"[^a-z]+", "_", m.group(1)).strip("_") + "_decile"] = pd.to_numeric(raw[c], errors="coerce")
    rep = {"code_column": code, "decile_column": dec, "rows": int(len(out)), "decile_values": sorted(out["imd_decile"].dropna().unique().tolist())[:12],
           "england_lsoas": int(out["lsoa"].astype(str).str.startswith("E01").sum())}
    return out, rep
