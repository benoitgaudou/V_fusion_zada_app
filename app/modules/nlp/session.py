# app/modules/nlp/session.py
from __future__ import annotations
import json
import re
import unicodedata
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

try:
    from sentence_transformers import SentenceTransformer
    _HAS_ST = True
except Exception:
    _HAS_ST = False


class NLPEngine:
    """Moteur NLP avec double backend (Word2Vec et optionnellement Sentence-Transformers)
       - 'sentence_transformers' : paraphrase-multilingual-MiniLM-L12-v2
       - 'word2vec'              : modèle Word2Vec (taille variable)
    """
    _kv: Optional[KeyedVectors] = None                # cache W2V partagé
    _st_model: Optional["SentenceTransformer"] = None # cache ST partagé

    def __init__(self):
        self.is_ready = False
        self.corpus_gdf: Optional[gpd.GeoDataFrame] = None
        self.doc_embeddings: Optional[np.ndarray] = None
        self.current_model_name: str = "N/A"
        self.backend: str = self._pick_backend()
        self.corpus_plain: Optional[list[str]] = None
        # NEW: tokens/doc pour recherche mots-clés
        self.corpus_tokens: Optional[list[set[str]]] = None


    # ----------------- Choix du backend -----------------
    def _pick_backend(self) -> str:
        """Choisit le backend selon la config et la dispo des libs."""
        cfg = None
        try:
            cfg = current_app.config.get("NLP_BACKEND", None)
        except Exception:
            cfg = None
            
        # normalisation robuste
        if isinstance(cfg, str):
            cfg = cfg.strip().lower()
        else:
            cfg = None
        
        # utilisation des alias
        st_alias = {"sentence_transformers", "st", "transformers", "s-t", "s_t"}
        w2v_alias = {"modele_word2vec", "w2v", "w2-v", "w_2v", "word_2vec"}
        
        if cfg in st_alias:
            return "sentence_transformers" if _HAS_ST else "word2vec"
        if cfg in w2v_alias:
            return "word2vec"

        # Par défaut : ST si dispo, sinon W2V
        return "sentence_transformers" if _HAS_ST else "word2vec"
    
    def set_backend(self, backend: str) -> None:
        """Permet de forcer le backend (avant init_from_fusion_gdf)."""
        b = (backend or "").strip().lower()
        if b in {"word2vec", "word_2vec", "w2v", "w_2v", "w-2v", "modele_word2vec"}:
            self.backend = "word2vec"
        elif b in {"sentence_transformers", "sentence-transformers", "st", "s-t", "s_t", "transformers"} and _HAS_ST:
            self.backend = "sentence_transformers"
        else:
            self.backend = "word2vec"  if not _HAS_ST else "sentence_transformers"
        try:
            current_app.logger.info(f"NLP backend set to: '{self.backend}'")
        except Exception:
            pass

    # ----------------- Lister les modèles dispos -----------------
    @classmethod
    def available_models(cls) -> list[dict]:
        """Retourne les modèles W2V trouvés + l’option ST si installée."""
        out = []
        try:
            base = Path(current_app.config["NLP_MODEL_PATH"])
            for p in sorted(list(base.glob("*.model")) + list(base.glob("*.bin"))):
                out.append({
                    "key": p.stem,
                    "name": p.stem,
                    "file": p.name,
                    "exists": True,
                    "path": str(p),
                })
        except Exception:
            pass

        if _HAS_ST:
            out.append({
                "key": "paraphrase-multilingual-MiniLM-L12-v2",
                "name": "paraphrase-multilingual-MiniLM-L12-v2",
                "file": None,
                "exists": True,
                "path": "huggingface",
            })
        return out

    # ----------------- Modèle Word2Vec -----------------
    @classmethod
    def _load_kv(cls) -> Optional[KeyedVectors]:
        if cls._kv is not None:
            return cls._kv
        model_path = Path(current_app.config["NLP_MODEL_PATH"])
        # Essaye d'abord un Word2Vec .model ; sinon on laissera le fallback le fabriquer.
        w2v_files = list(model_path.glob("*.model"))
        if w2v_files:
            w2v = Word2Vec.load(str(w2v_files[0]))
            cls._kv = w2v.wv
        else:
            # placeholder : sera remplacé quand on créera un fallback
            cls._kv = None
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

    # ----------------- Sentence-Transformers -----------------
    @classmethod
    def _load_st(cls) -> Optional["SentenceTransformer"]:
        if not _HAS_ST:
            return None
        if cls._st_model is not None:
            return cls._st_model

        model_name = "paraphrase-multilingual-MiniLM-L12-v2"
        try:
            # permet de surcharger via config
            model_name = current_app.config.get("SENTENCE_TRANSFORMERS_MODEL", model_name)
        except Exception:
            pass

        device = "cpu"
        try:
            device = current_app.config.get("SENTENCE_TRANSFORMERS_DEVICE", "cpu")
        except Exception:
            pass

        from sentence_transformers import SentenceTransformer  # import local safe
        cls._st_model = SentenceTransformer(model_name, device=device)
        return cls._st_model

    def _embed_texts_st(self, texts: list[str]) -> np.ndarray:
        st = self._load_st()
        if st is None:
            raise RuntimeError(
                "Sentence-Transformers indisponible. Installez 'sentence-transformers' et 'torch'."
            )
        embs = st.encode(texts, convert_to_numpy=True, normalize_embeddings=False)
        return embs.astype(np.float32, copy=False)

    # ----------------- Initialisation depuis un GDF de fusion -----------------
    def init_from_fusion_gdf(self, gdf_fusion: gpd.GeoDataFrame) -> Dict[str, Any]:
        self.corpus_gdf = build_corpus_from_fusion_gdf(gdf_fusion)
        
        texts = self.corpus_gdf["corpus_texte"].fillna("").tolist()
        self.corpus_tokens = [set(tokens_from_corpus(t)) for t in texts]
        
        # NEW: texte normalisé "plain" (tolérant à la ponctuation)
        def _normalize_plain(s: str) -> str:
            if not isinstance(s, str):
                s = "" if s is None else str(s)
            s = s.lower()
            s = unicodedata.normalize("NFKD", s)
            s = "".join(ch for ch in s if not unicodedata.combining(ch))
            # Remplace toute ponctuation/séparateur par des espaces
            s = re.sub(r"[^a-z0-9]+", " ", s)
            s = re.sub(r"\s+", " ", s).strip()
            return s

        self.corpus_plain = [_normalize_plain(t) for t in texts]


        # Branche ST : encode tout le corpus directement
        if self.backend == "sentence_transformers":
            full_texts = self.corpus_gdf["corpus_texte"].fillna("").tolist()
            embs = self._embed_texts_st(full_texts)
            self.doc_embeddings = embs
            self.is_ready = True
            self.current_model_name = "paraphrase-multilingual-MiniLM-L12-v2"
            nonzero = int((np.any(embs, axis=1)).sum())
            return {
                "success": True,
                "documents": nonzero,
                "dimension": int(embs.shape[1]),
                "model": self.current_model_name
            }

        # Branche Word2Vec (ta logique d’origine)
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

    def _keyword_coverage(self, query: str) -> np.ndarray:
        """
        Fraction de mots de la requête présents (0..1).
        1.0 uniquement si TOUS les mots sont présents (AND).
        Tolère les séparateurs + / - _ , ; : grâce à corpus_plain.
        """
        if not self.corpus_tokens:
            return np.zeros(0, dtype=np.float32)

        q_tokens = [t for t in tokens_from_corpus(query) if t]
        q_tokens = list(dict.fromkeys(q_tokens))  # uniques
        if len(q_tokens) == 0:
            return np.zeros(len(self.corpus_tokens), dtype=np.float32)

        cover = np.zeros(len(self.corpus_tokens), dtype=np.float32)
        for i, doc_tok in enumerate(self.corpus_tokens):
            plain = self.corpus_plain[i] if self.corpus_plain and i < len(self.corpus_plain) else ""
            plain_set = set(plain.split()) if plain else set()
            found = 0
            for tok in q_tokens:
                if tok in doc_tok or tok in plain_set:
                    found += 1
            cover[i] = found / float(len(q_tokens))
        return cover

    # ----------------- Recherche -----------------
    def search(self, query: str, top_k: int = 50, mode: str = "semantic") -> pd.DataFrame:
        """
        Recherche NLP selon deux modes :
        - mode="keyword"  : score = couverture des mots-clés (AND). 100% si tous les mots saisis sont présents.
        - mode="semantic" : score = similarité cosinus (comme avant).
        Retourne un DataFrame avec les colonnes : row_idx, id_zone, score, similarite, couverture, mode.
        """
        if not self.is_ready or self.corpus_gdf is None:
            return pd.DataFrame()

        mode = (mode or "semantic").strip().lower()
        if mode not in {"semantic", "keyword"}:
            mode = "semantic"

        # 1️ Couverture mots-clés (0..1)
        coverage = self._keyword_coverage(query)

        # 2️ Similarité sémantique si demandée
        if mode == "semantic":
            if self.backend == "sentence_transformers":
                qv = self._embed_texts_st([query])
            else:
                kv = self._load_kv()
                toks = tokens_from_corpus(query)
                vecs = [kv[w] for w in toks if kv is not None and w in kv]
                if not vecs:
                    return pd.DataFrame()
                qv = np.mean(vecs, axis=0, dtype=np.float32, keepdims=True)

            sim_full = cosine_similarity(qv, self.doc_embeddings)[0]
            # re-normalisation [0,1]
            sim = ((sim_full + 1.0) / 2.0).astype(np.float32)
            score = sim.copy()

        # 3️ Mode mots-clés (100% si tous les mots présents)
        else:  # mode == "keyword"
            sim = np.zeros(len(self.corpus_gdf), dtype=np.float32)
            score = coverage.copy()

        # 4️ Tri
        idx = np.argsort(score)[::-1][:top_k]
        return pd.DataFrame({
            "row_idx": idx,
            "id_zone": self.corpus_gdf.iloc[idx]["id_zone"].to_numpy(),
            "score": score[idx],
            "similarite": sim[idx],
            "couverture": coverage[idx],
            "mode": [mode] * len(idx),
        })


    # ----------------- Sortie GeoJSON -----------------
    def to_geojson(self, df: pd.DataFrame):
        """
        Style la carte avec la colonne 'score' si présente (mode 'keyword'),
        sinon retombe sur 'similarite' (mode 'semantic').
        La légende est recalculée sur la métrique affichée.
        """
        if df.empty or self.corpus_gdf is None:
            return {"type": "FeatureCollection", "features": []}, {"type": "continuous", "items": []}, None

        # Quelle valeur cartographier ?
        if "score" in df.columns and (df["score"].notna().any()):
            values = df["score"].to_numpy()
            thematic_field = "score"
            value_fmt = lambda v: f"{float(v)*100:.1f}%"
        else:
            values = df["similarite"].to_numpy()
            thematic_field = "similarite"
            value_fmt = lambda v: f"{float(v):.3f}"

        # Sous-ensemble géométrique dans l’ordre du ranking
        sel = self.corpus_gdf.iloc[df["row_idx"]][["id_zone", "corpus_texte", "geometry"]].reset_index(drop=True)
        sel["__val__"] = values

        # Légende basée sur la métrique choisie
        vals = sel["__val__"].to_numpy().astype(float)
        uniq = np.unique(np.round(vals, 6))
        classes = int(max(1, min(6, uniq.size)))  # ≤ nb de valeurs distinctes
        legend = legend_from_scores(vals, classes=classes)

        # bornes/couleurs pour coloriser
        items = legend["items"]
        bounds = [float(it["label"].split(" - ")[0]) for it in items] + [legend["max_value"]]
        colors = [it["color"] for it in items]

        def color_for(v: float) -> str:
            for i in range(len(bounds) - 1):
                if bounds[i] <= v <= bounds[i + 1]:
                    return colors[i]
            return colors[-1]

        feats = []
        for i, r in sel.iterrows():
            col = color_for(float(r["__val__"]))
            props = {
                "id_zone": r["id_zone"],
                "nlp_backend": self.backend,
                "nlp_model": self.current_model_name,
                "nlp_content_preview": str(r["corpus_texte"])[:200],
                "thematic_field": thematic_field,      # 'score' (keyword) ou 'similarite' (semantic)
                "thematic_value": value_fmt(r["__val__"]),
                "style": {"fillColor": col, "color": col, "fillOpacity": 0.7, "weight": 2, "opacity": 0.9},
            }

            # ➜ N'expose 'nlp_similarite' que si mode=semantic
            row_mode = str(df.iloc[i]["mode"]) if "mode" in df.columns else "semantic"
            props["nlp_mode"] = row_mode
            if "couverture" in df.columns:
                props["nlp_couverture"] = float(df.iloc[i]["couverture"])
            if row_mode == "semantic" and "similarite" in df.columns:
                props["nlp_similarite"] = float(df.iloc[i]["similarite"])

            gj = json.loads(gpd.GeoSeries([r.geometry], crs="EPSG:4326").to_json())["features"][0]["geometry"]
            feats.append({"type": "Feature", "properties": props, "geometry": gj})


        tb = self.corpus_gdf.total_bounds
        leaflet_bounds = [[tb[1], tb[0]], [tb[3], tb[2]]]
        return {"type": "FeatureCollection", "features": feats}, legend, leaflet_bounds


    # ----------------- Statut -----------------
    def stats(self) -> Dict[str, Any]:
        return {
            "ready": self.is_ready,
            "doc_count": int(self.corpus_gdf.shape[0]) if self.corpus_gdf is not None else 0,
            "model": self.current_model_name,
            "backend": self.backend,
        }