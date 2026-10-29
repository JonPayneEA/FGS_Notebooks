# =============================================================================
# utils/economics.py
# The assumptions behind the indicative expected annual damage the app shows (pure pandas, tested without Spark). jobs/38_economics_assumptions
# creates the table from these defaults and never overwrites a value someone has set.
#
# THE METHOD (applied in the app, from households by band in ea_area_exposure):
#   expected annual damage = sum over bands of  households in the band x annual probability of the band x damage per household per flood event
# computed three times (low, central, high). Low uses the bottom of each band's probability range with the low damage; high uses the top with the
# high damage; central uses the middle of each range (geometric) with the central damage. The two vary together, so the range is wide and honest
# about how little a banded method pins down. It is indicative: no flood depth, no property type and no property value is used.
#
# NaFRA2 chance of flooding in any year, by band: High over 3.3%, Medium 1 to 3.3%, Low 0.1 to 1%, Very Low under 0.1%. High has no top and Very Low
# no bottom, so High is capped at 10% a year and Very Low floored at 0.01%. Both caps are assumptions, shown on screen.
#
# THE DAMAGE FIGURES ARE NOT SET HERE. They are left empty for the economists (the Multi-Coloured Manual average per household, with its price year)
# to enter in the table; the app says so and shows no pound figure until they do.
# =============================================================================

import math

import pandas as pd

SCENARIOS = ("low", "central", "high")
BANDS = ("high", "medium", "low", "very_low")

# (low, high) annual probability of each band; central is the geometric mean, except High, which has no top: 4% is a judgement (most households in the
# High band sit nearer its bottom edge of 3.3% than the 10% cap), and it is the one to revisit when better evidence is to hand
BAND_RANGE = {"high": (0.033, 0.10), "medium": (0.01, 0.033), "low": (0.001, 0.01), "very_low": (0.0001, 0.001)}
CENTRAL_OVERRIDE = {"high": 0.04}

COLUMNS = ["item", "scenario", "value", "unit", "source", "price_year", "note", "updated_at"]


def central_probability(band: str) -> float:
    if band in CENTRAL_OVERRIDE:
        return CENTRAL_OVERRIDE[band]
    lo, hi = BAND_RANGE[band]
    return math.sqrt(lo * hi)


def default_rows(now=None) -> pd.DataFrame:
    """The seed rows: a probability for every band and scenario, and an empty damage per household for each scenario."""
    rows = []
    for band in BANDS:
        lo, hi = BAND_RANGE[band]
        for scenario, v in zip(SCENARIOS, (lo, central_probability(band), hi)):
            rows.append({"item": f"prob_{band}", "scenario": scenario, "value": float(v), "unit": "annual probability",
                         "source": "NaFRA2 band range", "price_year": None,
                         "note": {"high": "Over 3.3% a year; no upper limit in the band, so capped at 10% (assumption).",
                                  "very_low": "Under 0.1% a year; no lower limit in the band, so floored at 0.01% (assumption)."}.get(band, "Band range from NaFRA2."),
                         "updated_at": now})
    for scenario in SCENARIOS:
        rows.append({"item": "damage_per_household", "scenario": scenario, "value": None, "unit": "GBP per household per flood event",
                     "source": "To be set (for example the Multi-Coloured Manual average residential damage)", "price_year": None,
                     "note": "Not set. The app shows no pound figure until low, central and high are all filled in.", "updated_at": now})
    return pd.DataFrame(rows, columns=COLUMNS)


def missing_rows(existing: pd.DataFrame, now=None) -> pd.DataFrame:
    """The default rows whose (item, scenario) is not in the existing table, so a re-run adds new items and never overwrites a value someone set."""
    d = default_rows(now)
    if existing is None or not len(existing):
        return d
    have = set(zip(existing["item"].astype(str), existing["scenario"].astype(str)))
    return d[[(i, s) not in have for i, s in zip(d["item"], d["scenario"])]].reset_index(drop=True)


def check_assumptions(table: pd.DataFrame) -> list:
    """Problems with an assumptions table, as plain sentences (empty list when it is fine): probabilities outside 0 to 1, a scenario out of order
    (low above central above high), a band whose probability is below that of a rarer one, damage that is negative or out of order."""
    problems = []
    t = table.copy()
    t["value"] = pd.to_numeric(t["value"], errors="coerce")

    def get(item, scenario):
        x = t[(t["item"] == item) & (t["scenario"] == scenario)]["value"]
        return float(x.iloc[0]) if len(x) and pd.notna(x.iloc[0]) else None

    for item in [f"prob_{b}" for b in BANDS] + ["damage_per_household"]:
        vals = [get(item, s) for s in SCENARIOS]
        if any(v is None for v in vals):
            if item != "damage_per_household":
                problems.append(f"{item} is missing a scenario.")
            continue
        if item.startswith("prob_") and any(v <= 0 or v > 1 for v in vals):
            problems.append(f"{item} must be between 0 and 1.")
        if item == "damage_per_household" and any(v < 0 for v in vals):
            problems.append("damage_per_household must not be negative.")
        if not (vals[0] <= vals[1] <= vals[2]):
            problems.append(f"{item} should run low, central, high in order.")
    for s in SCENARIOS:
        ps = [get(f"prob_{b}", s) for b in BANDS]
        if all(p is not None for p in ps) and not all(ps[i] >= ps[i + 1] for i in range(3)):
            problems.append(f"In the {s} scenario a rarer band has a higher probability than a more frequent one.")
    return problems
