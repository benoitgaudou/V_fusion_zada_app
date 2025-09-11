# app/modules/nlp/api.py
from __future__ import annotations
from typing import Dict, Any
from pathlib import Path
import geopandas as gpd

from .session import NLPEngine

# Petit cache par export_path -> moteur, pour réutiliser l'initialisation
_ENGINES: dict[str, NLPEngine] = {}

def _get_engine(export_path: str) -> NLPEngine:
    key = str(Path(export_path).resolve())
    if key not in _ENGINES:
        _ENGINES[key] = NLPEngine()
    return _ENGINES[key]

def init_from_fusion_export(export_path: str, backend: str | None = None) -> Dict[str, Any]:
    import flask
    gdf = gpd.read_file(export_path)
    eng = _get_engine(export_path)

    if backend:
        eng.set_backend(backend)
    else:
        try:
            config_backend = flask.current_app.config.get("NLP_BACKEND")
            if config_backend:
                eng.set_backend(config_backend)
        except Exception:
            pass

    info = eng.init_from_fusion_gdf(gdf)
    return {
        "success": True,
        "documents_count": int(info.get("documents", 0)),
        "embedding_dim": int(info.get("dimension", 0)),
        "model_used": info.get("model", "inconnu"),
    }



def semantic_search(export_path: str, query: str, top_k: int = 10) -> Dict[str, Any]:
    """
    Exécute une recherche sémantique via le moteur associé au même export_path.
    """
    eng = _get_engine(export_path)
    if not eng.is_ready:
        return {
            "success": False,
            "error": "Système NLP non initialisé pour ce résultat de fusion."
        }

    df = eng.search(query, top_k=top_k)
    geojson, legend, bounds = eng.to_geojson(df)

    # injecte rang/similarité dans les features pour les popups front
    if not df.empty:
        sim = df["similarite"].reset_index(drop=True)
        for i, f in enumerate(geojson.get("features", [])):
            f["properties"]["nlp_rank"] = i + 1
            f["properties"]["nlp_similarity"] = float(sim.iloc[i])

    return {
        "success": True,
        "query": query,
        "matches": int(df.shape[0]),
        "features": geojson.get("features", []),
        "legend": legend,
        "map_bounds": bounds,
    }
