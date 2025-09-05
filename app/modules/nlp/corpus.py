# app/modules/nlp/corpus.py
from __future__ import annotations
import re
from typing import Optional, List
import pandas as pd
import geopandas as gpd
from .utils import clean_value

def build_corpus_from_fusion_gdf(
    gdf: gpd.GeoDataFrame,
    exclude_exact: Optional[set] = None,
    exclude_patterns: Optional[List[str]] = None,
) -> gpd.GeoDataFrame:
    if gdf is None or gdf.empty:
        raise ValueError("GDF fusion vide")

    exclude_exact = exclude_exact or {
        "geometry", "original_source_id", "Original_source_id",
        "original_source_name", "Original_source_name",
        "intersection_type", "type", "sources", "source_names", "id", 'nan','non',
    }
    pats = exclude_patterns or ["nom*", "id*", "source*", "original*", "intersection*"]

    def allowed(col: str) -> bool:
        if col in exclude_exact: return False
        for pat in pats:
            rx = "^" + pat.replace("*", ".*") + "$"
            if re.match(rx, col, flags=re.IGNORECASE): return False
        return True

    gdf_wgs = gdf.to_crs("EPSG:4326") if gdf.crs and gdf.crs.to_string() != "EPSG:4326" else gdf
    cols = [c for c in gdf_wgs.columns if allowed(c)]

    records = []
    for i, row in gdf_wgs.iterrows():
        parts = []
        for c in cols:
            v = row.get(c, None)
            cv = clean_value(v)
            if cv: parts.append(f"{c}: {cv}")
        corpus = "; ".join(parts) if parts else "corpus_vide"
        records.append((i, corpus, row.geometry))

    out = gpd.GeoDataFrame(
        {"id_zone": [rid for rid,_,_ in records],
         "corpus_texte": [tx for _,tx,_ in records],
         "geometry": [geom for *_, geom in records]},
        crs="EPSG:4326"
    )
    return out
