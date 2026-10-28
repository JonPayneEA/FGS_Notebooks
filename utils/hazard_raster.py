# =============================================================================
# utils/hazard_raster.py
# Turn the Environment Agency flood polygons (NaFRA2 bands, Flood Zones 2 and 3) into 10 m rasters, one per 50 km tile, and
# read them back as (a) the band at any point and (b) the share of each 100 m cell in each band. Pure numpy and rasterio plus
# shapely, so it can be tested without Databricks.
#
# WHY A RASTER: a national point-in-polygon join over tens of millions of address points against these polygon layers is slow
# and heavy. Painting the polygons once onto a 10 m grid makes every later question a lookup, and the cell shares fall out of
# adding up 10 x 10 blocks. The cost is a position error of at most 7 m at a polygon edge, stated in the docs.
#
# WORST BAND WINS at a pixel where polygons overlap, which is the right rule for a point (a home is as at risk as its worst
# band) and is only applied at 10 m, never to a whole 100 m cell: cell shares count pixels, so one patch of surface water
# does not turn a cell High.
#
# TILES: the source folders are named by OS grid tile, for example RoFRS_SE50_v202606. Two letters name a 100 km square, then
# two digits: the first is the easting offset in tens of km (0 or 5), the second the northing offset. Each tile is 50 km.
#
#   tile_code / is_present_day   read a folder name; folders with Climate_Change in the name are the future scenario
#   tile_bounds                  (xmin, ymin, xmax, ymax) in British National Grid metres
#   RasterAccumulator            paint polygons batch by batch (so a tile larger than memory still works), then finalize
#   sample_ranks                 the rank at points (0 outside the tile or outside any flood area)
#   cell_shares                  per 100 m cell, the share of its area in each band (cells with no band are left out)
#   save_raster / load_raster    compressed .npz with the bounds and resolution inside, so the file is self-describing
# =============================================================================

import re

import numpy as np
import pandas as pd
import shapely

OS_LETTERS = "ABCDEFGHJKLMNOPQRSTUVWXYZ"   # no I
TILE_M = 50_000
_TILE_RE = re.compile(r"_([A-Z]{2}\d{2})_v\d+")


def tile_code(name):
    """'RoFRS_SE50_v202606' -> 'SE50'. None if the name has no tile."""
    m = _TILE_RE.search(str(name))
    return m.group(1) if m else None


def is_present_day(name) -> bool:
    return "climate_change" not in str(name).lower()


def tile_bounds(code):
    """Bounds of a 50 km tile such as 'NT50': (xmin, ymin, xmax, ymax). The first digit is the easting offset, the second the
    northing offset, each in tens of km."""
    code = str(code).upper()
    if not re.fullmatch(r"[A-HJ-Z]{2}\d{2}", code):
        raise ValueError(f"not a tile code: {code!r}")
    big, small = OS_LETTERS.index(code[0]), OS_LETTERS.index(code[1])
    x0 = (big % 5 - 2) * 500_000 + (small % 5) * 100_000
    y0 = (3 - big // 5) * 500_000 + (4 - small // 5) * 100_000
    x0 += int(code[2]) * 10_000
    y0 += int(code[3]) * 10_000
    return float(x0), float(y0), float(x0 + TILE_M), float(y0 + TILE_M)


def _shape(bounds, res):
    xmin, ymin, xmax, ymax = bounds
    return int(round((ymax - ymin) / res)), int(round((xmax - xmin) / res))


class RasterAccumulator:
    """Paint polygons onto a raster for one tile. One mask per rank, so polygons can arrive in any order and in batches, and the
    worst (highest) rank wins at a pixel when the masks are combined."""

    def __init__(self, bounds, res=10, ranks=(1, 2, 3, 4)):
        from rasterio.transform import from_origin
        self.bounds, self.res, self.ranks = tuple(bounds), res, tuple(ranks)
        self.shape = _shape(self.bounds, res)
        self.transform = from_origin(self.bounds[0], self.bounds[3], res, res)
        self.masks = {r: np.zeros(self.shape, dtype="uint8") for r in self.ranks}
        self.n_polygons = 0

    def add(self, wkb_values, rank_values):
        """Paint a batch: geometries as WKB (bytes) and a rank for each. Rank 0 or an unknown rank is skipped."""
        from rasterio.features import rasterize
        geoms = shapely.from_wkb(np.asarray(list(wkb_values), dtype=object))
        ranks = np.asarray(list(rank_values), dtype="int64")
        for r in self.ranks:
            sel = [g for g, k in zip(geoms, ranks) if k == r and g is not None and not g.is_empty]
            if not sel:
                continue
            sel = [shapely.make_valid(g) if not g.is_valid else g for g in sel]
            rasterize([(g, 1) for g in sel], out=self.masks[r], transform=self.transform, all_touched=False)
            self.n_polygons += len(sel)

    def finalize(self):
        """One uint8 array: 0 for no band, otherwise the highest rank painted at that pixel."""
        out = np.zeros(self.shape, dtype="uint8")
        for r in sorted(self.ranks):
            out[self.masks[r] > 0] = r
        return out


def sample_ranks(arr, bounds, res, easting, northing):
    """The rank at each point. 0 for a point outside the tile or outside every flood area."""
    e, n = np.asarray(easting, dtype="float64"), np.asarray(northing, dtype="float64")
    col = np.floor((e - bounds[0]) / res).astype("int64")
    row = np.floor((bounds[3] - n) / res).astype("int64")
    ok = (col >= 0) & (col < arr.shape[1]) & (row >= 0) & (row < arr.shape[0])
    out = np.zeros(len(e), dtype="uint8")
    out[ok] = arr[row[ok], col[ok]]
    return out


def cell_shares(arr, bounds, res, labels, cell=100) -> pd.DataFrame:
    """Per 100 m cell and band, the share of the cell's pixels in that band. `labels` maps rank to a name, for example
    {1: "Very Low", 2: "Low", 3: "Medium", 4: "High"}. Long format: cell_key, band, share. Cells with no band are omitted,
    so the result is sparse. The tile bounds must sit on the cell grid, which 50 km tiles do."""
    if cell % res:
        raise ValueError("the cell size must be a whole number of pixels")
    b = cell // res
    ny, nx = arr.shape
    if ny % b or nx % b:
        raise ValueError("the raster does not divide into whole cells")
    blocks = arr.reshape(ny // b, b, nx // b, b)
    xmin, _, _, ymax = bounds
    frames = []
    for rank, label in labels.items():
        counts = (blocks == rank).sum(axis=(1, 3))
        rows, cols = np.nonzero(counts)
        if not len(rows):
            continue
        # row 0 is the northern edge; a cell's south-west corner is its row's lower edge
        east = xmin + cols * cell
        north = ymax - (rows + 1) * cell
        frames.append(pd.DataFrame({
            "cell_key": (north // cell).astype("int64") * 10_000 + (east // cell).astype("int64"),
            "band": label,
            "share": counts[rows, cols] / float(b * b),
        }))
    if not frames:
        return pd.DataFrame({"cell_key": np.array([], dtype="int64"), "band": [], "share": np.array([], dtype="float64")})
    return pd.concat(frames, ignore_index=True)


def save_raster(path, arr, bounds, res):
    np.savez_compressed(path, arr=arr, bounds=np.asarray(bounds, dtype="float64"), res=np.asarray(res))


def load_raster(path):
    """(arr, bounds, res) from a file written by save_raster."""
    with np.load(path) as z:
        return z["arr"], tuple(float(v) for v in z["bounds"]), float(z["res"])
