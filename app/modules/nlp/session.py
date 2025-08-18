# app/modules/nlp/session.py
from __future__ import annotations
import json
from pathlib import Path
from typing import Dict, Optional, Tuple, Any
import numpy as np
import pandas as pd
import geopandas as gpd
from gensim.models import Word2Vec, KeyedVectors
from sklearn.metrics.pairwise import cosine_similarity
from flask import current_app
from .utils import tokens_from_corpus, legend_from_scores
from .corpus import build_corpus_from_fusion_gdf

class NLPEngine:
    _kv: Optional[KeyedVectors] = None  # cache modèle partagé

    def __init__(self):
        self.is_ready = False
        self.corpus_gdf: Optional[gpd.GeoDataFrame] = None
        self.doc_embeddings: Optional[np.ndarray] = None
        self.current_model_name: str = "N/A"

    #Lister les modèles disponibles
    @classmethod
    def available_models(cls) -> list[dict]:
        """_summary_

        Returns:
            list[dict]: les modèles disponibles
        """
        try:
            base = Path(current_app.config["NLP_MODEL_PATH"])
        except Exception:
            return []
        out = []
        for p in sorted(list(base.glob("*.model")) + list(base.glob("*.bin"))):
            out.append({
                "key": p.stem,
                "name": p.stem,
                "file": p.name,
                "exists": True,
                "path": str(p),
            })
        return out
    
    # --------- Modèle ---------
    @classmethod
    def _load_kv(cls) -> KeyedVectors:
        if cls._kv is not None:
            return cls._kv
        model_path = Path(current_app.config["NLP_MODEL_PATH"])
        # On essaie d'abord un Word2Vec .model ; sinon on fabriquera un fallback au besoin.
        # Si tu as un binaire word2vec (.bin), adapte ici pour KeyedVectors.load_word2vec_format
        w2v_files = list(model_path.glob("*.model"))
        if w2v_files:
            w2v = Word2Vec.load(str(w2v_files[0]))
            cls._kv = w2v.wv
        else:
            # placeholder : sera remplacé quand on créera un fallback
            cls._kv = None  # type: ignore
        return cls._kv

    def _ensure_fallback(self, sentences: list[list[str]], vector_size: int = 50) -> None:
        if self._kv is not None:
            return
        # Entraîne un petit modèle si aucun pré-entraîné n’est présent
        w2v = Word2Vec(
            sentences=sentences,
            vector_size=vector_size, window=3, min_count=1,
            workers=2, epochs=10, sg=1, seed=42
        )
        self.__class__._kv = w2v.wv
        self.current_model_name = "fallback_model"

    # --------- Initialisation depuis un GDF de fusion ---------
    def init_from_fusion_gdf(self, gdf_fusion: gpd.GeoDataFrame) -> Dict[str, Any]:
        self.corpus_gdf = build_corpus_from_fusion_gdf(gdf_fusion)
        docs = [t for t in self.corpus_gdf["corpus_texte"].tolist() if t != "corpus_vide"]
        tokenized = [tokens_from_corpus(t) for t in docs if t]

        kv = self._load_kv()
        if kv is None:
            self._ensure_fallback(tokenized)

        kv = self._load_kv()  # sûr d’être non-None maintenant
        embs = np.zeros((len(self.corpus_gdf), kv.vector_size), dtype=np.float32)
        for i, txt in enumerate(self.corpus_gdf["corpus_texte"].fillna("")):
            toks = tokens_from_corpus(txt)
            vecs = [kv[w] for w in toks if w in kv]
            embs[i] = np.mean(vecs, axis=0).astype(np.float32) if vecs else np.zeros(kv.vector_size, np.float32)

        self.doc_embeddings = embs
        self.is_ready = True
        if self.current_model_name == "N/A":
            self.current_model_name = "pretrained" if isinstance(kv, KeyedVectors) else "unknown"

        return {
            "success": True,
            "documents": int((np.any(embs, axis=1)).sum()),
            "dimension": int(kv.vector_size),
            "model": self.current_model_name
        }

    # --------- Recherche ---------
    def search(self, query: str, top_k: int = 20) -> pd.DataFrame:
        if not self.is_ready or self.corpus_gdf is None or self.doc_embeddings is None:
            return pd.DataFrame()
        kv = self._load_kv()
        toks = tokens_from_corpus(query)
        vecs = [kv[w] for w in toks if w in kv]
        if not vecs:
            return pd.DataFrame()
        qv = np.mean(vecs, axis=0).reshape(1, -1)
        sims = cosine_similarity(qv, self.doc_embeddings)[0]
        idx = np.argsort(sims)[::-1][:top_k]
        return pd.DataFrame({
            "row_idx": idx,
            "id_zone": self.corpus_gdf.iloc[idx]["id_zone"].to_numpy(),
            "similarite": sims[idx]
        })

    # --------- Sortie GeoJSON ---------
    def to_geojson(self, df: pd.DataFrame):
        if df.empty or self.corpus_gdf is None:
            return {"type":"FeatureCollection","features":[]}, {"type":"continuous","items":[]}, None

        sel = self.corpus_gdf.iloc[df["row_idx"]][["id_zone","corpus_texte","geometry"]].reset_index(drop=True)
        sel["similarite"] = df["similarite"].to_numpy()
        legend = legend_from_scores(sel["similarite"].to_numpy(), classes=6)

        # bornes pour trouver la couleur
        items = legend["items"]
        bounds = [float(it["label"].split(" - ")[0]) for it in items] + [legend["max_value"]]
        colors = [it["color"] for it in items]

        def color_for(v: float) -> str:
            for i in range(len(bounds)-1):
                if bounds[i] <= v <= bounds[i+1]:
                    return colors[i]
            return colors[-1]

        feats = []
        for _, r in sel.iterrows():
            col = color_for(float(r["similarite"]))
            props = {
                "id_zone": r["id_zone"],
                "nlp_similarity": float(r["similarite"]),
                "nlp_rank": None,  # l’UI peut ordonner par similarité
                "nlp_content_preview": str(r["corpus_texte"])[:200],
                "style": {"fillColor": col, "color": col, "fillOpacity": 0.7, "weight": 2, "opacity": 0.9},
                "thematic_field": "similarite",
                "thematic_value": f"{float(r['similarite']):.3f}",
            }
            gj = json.loads(gpd.GeoSeries([r.geometry], crs="EPSG:4326").to_json())["features"][0]["geometry"]
            feats.append({"type":"Feature","properties":props,"geometry":gj})

        tb = self.corpus_gdf.total_bounds
        leaflet_bounds = [[tb[1], tb[0]], [tb[3], tb[2]]]
        return {"type":"FeatureCollection","features":feats}, legend, leaflet_bounds

    # --------- Statut ---------
    def stats(self) -> Dict[str, Any]:
        return {
            "ready": self.is_ready,
            "doc_count": int(self.corpus_gdf.shape[0]) if self.corpus_gdf is not None else 0,
            "model": self.current_model_name
        }
