# app/modules/nlp/__init__.py
from .utils import tokens_from_corpus, legend_from_scores
from .corpus import build_corpus_from_fusion_gdf
from .session import NLPEngine

# Instance globale (optionnelle)
nlp_engine = NLPEngine()

# --- SHIM de compatibilité pour l’ancien code ---
_engines_by_path: dict[str, NLPEngine] = {}

def get_session_for_export_path(export_path: str) -> NLPEngine:
    import geopandas as gpd
    if export_path not in _engines_by_path:
        gdf = gpd.read_file(export_path)
        eng = NLPEngine()
        eng.init_from_fusion_gdf(gdf)   # cette méthode doit exister dans NLPEngine
        _engines_by_path[export_path] = eng
    return _engines_by_path[export_path]
