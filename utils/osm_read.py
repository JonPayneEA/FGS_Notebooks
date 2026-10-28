# =============================================================================
# utils/osm_read.py
# Read the tagged features we need from an OpenStreetMap file (.osm.pbf or .osm), as points. Needs the osmium package (pyosmium 4).
# A node is its own point; a way (a building outline, say) is the mean of its node positions. Relations (multipolygon sites) are skipped:
# a small loss for the sites we want, and it keeps the read to one pass.
# =============================================================================

import pandas as pd

KEYS = ("amenity", "healthcare", "emergency", "power", "man_made", "social_facility")


def read_osm_features(path: str, keys=KEYS, keep=None, progress=None, progress_every: int = 20_000_000) -> pd.DataFrame:
    """Features that carry any of `keys`: a frame with osm_id, kind (node or way), lon, lat and tags (a dict of every tag).
    `keep` is an optional function of the tags dict; only features it returns True for are kept, so the millions of benches and car
    parks never reach memory. The location store is added before the key filter so that way geometries can be found from nodes
    that are themselves filtered out. `progress` is called with the number of objects seen, every `progress_every` objects.
    Memory: the node location store is a few GB for the whole UK."""
    import osmium
    fp = osmium.FileProcessor(path).with_locations().with_filter(osmium.filter.KeyFilter(*keys))
    rows, seen = [], 0
    for o in fp:
        seen += 1
        if progress and seen % progress_every == 0:
            progress(seen, len(rows))
        if not (o.is_node() or o.is_way()):
            continue
        tags = {t.k: t.v for t in o.tags}
        if keep is not None and not keep(tags):
            continue
        if o.is_node():
            if o.location.valid():
                rows.append((o.id, "node", o.location.lon, o.location.lat, tags))
        else:
            lons, lats = [], []
            for n in o.nodes:
                if n.location.valid():
                    lons.append(n.location.lon)
                    lats.append(n.location.lat)
            if lons:
                rows.append((o.id, "way", sum(lons) / len(lons), sum(lats) / len(lats), tags))
    return pd.DataFrame(rows, columns=["osm_id", "kind", "lon", "lat", "tags"])
