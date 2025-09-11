# app/modules/column_auto_align.py
from __future__ import annotations
import re, json, unicodedata
from dataclasses import dataclass
from typing import Dict, List, Tuple, Set, Any
from collections import defaultdict

import pandas as pd
import geopandas as gpd

# --- dépendances optionnelles ---
try:
    from rapidfuzz import fuzz
    HAVE_RAPIDFUZZ = True
except Exception:
    HAVE_RAPIDFUZZ = False

try:
    from sentence_transformers import SentenceTransformer
    import numpy as np
    HAVE_EMB = True    # Forcer l'utilisation de rappidfuzz en desactivant les embeddings
except Exception:
    HAVE_EMB = False


# --------- utils ----------
def _norm_col(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode("ascii")
    s = s.strip().lower()
    s = re.sub(r"[\s\-]+", "_", s)
    s = re.sub(r"[^\w]", "_", s)
    s = re.sub(r"__+", "_", s)
    s = re.sub(r"^_|_$", "", s)
    s = re.sub(r"_(\d+)$", "", s)            # retire suffixes _1/_2
    if len(s) > 3 and s.endswith("s"):       # pluriel naïf
        s = s[:-1]
    return s or "col"

def _concat_dedup(values, sep=", "):
    out, seen = [], set()
    for v in values:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            continue
        for part in re.split(r"[,\|;]", str(v)):
            t = part.strip()
            if t and t not in seen:
                seen.add(t); out.append(t)
    return sep.join(out) if out else None


@dataclass
class AutoAlignCfg:
    fuzzy_threshold: float = 84.0        # RapidFuzz token_set_ratio (0-100)
    use_embeddings: bool = True         # True si vous installez sentence-transformers
    emb_model: str = "paraphrase-multilingual-MiniLM-L12-v2"
    emb_threshold: float = 0.78          # cosine
    join_sep: str = ", "
    reserved_cols: Tuple[str, ...] = ("geometry","original_source_id","original_source_name","type","sources","source_names")
    save_mapping_json: str | None = "out/col_mapping.json"  # persistance
    load_mapping_json: str | None = None   # pour réappliquer un mapping validé
    auto_grow: bool = True               # apprend les nouvelles colonnes à la volée


class ColumnAutoAligner:
    """
    Alignement automatique SANS dictionnaire :
      - normalisation
      - regroupement par similarité (fuzzy + embeddings optionnels)
      - fusion des colonnes en une colonne canonique (valeurs concaténées, dédupliquées)
      - mapping persistant + adaptation aux nouvelles données (auto_grow)
    """
    def __init__(self, cfg: AutoAlignCfg):
        self.cfg = cfg
        self._canonical_map: Dict[str, str] = {}
        self._groups: Dict[str, List[str]] = {}
        self._emb = None
        self._canon_vecs: Dict[str, "np.ndarray"] = {}

        # Pour forcer l'utilisation de rapidfuzz même si sentence-transformers est installé
        
        
        if self.cfg.use_embeddings and HAVE_EMB:
            try:
                self._emb = SentenceTransformer(self.cfg.emb_model)
            except Exception:
                self._emb = None

        if self.cfg.load_mapping_json:
            try:
                with open(self.cfg.load_mapping_json, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._canonical_map = data.get("canonical_map", {})
                self._groups = data.get("groups", {})
                if self._emb and self._groups:
                    keys = list(self._groups.keys())
                    vecs = self._emb.encode(keys, normalize_embeddings=True)
                    self._canon_vecs = {k: vecs[i] for i, k in enumerate(keys)}
            except Exception:
                pass

    def fit(self, gdfs: List[gpd.GeoDataFrame]) -> Dict[str, str]:
        if self._canonical_map:
            return self._canonical_map

        all_cols: List[str] = []
        for gdf in gdfs:
            for c in gdf.columns:
                if c not in self.cfg.reserved_cols:
                    all_cols.append(c)
        all_cols = list(dict.fromkeys(all_cols))
        norm = {c: _norm_col(c) for c in all_cols}

        groups: Dict[str, Set[str]] = {norm[c]: set([c]) for c in all_cols}

        keys = list(groups.keys())
        if self._emb is not None and len(keys) > 1:
            vecs = self._emb.encode(keys, normalize_embeddings=True)
            sims = (vecs @ vecs.T)
            parent = {k: k for k in keys}
            def find(x):
                while parent[x] != x:
                    parent[x] = parent[parent[x]]
                    x = parent[x]
                return x
            def union(x, y):
                rx, ry = find(x), find(y)
                if rx != ry:
                    parent[ry] = rx
            for i in range(len(keys)):
                for j in range(i+1, len(keys)):
                    if sims[i, j] >= self.cfg.emb_threshold:
                        union(keys[i], keys[j])
            merged = defaultdict(set)
            for k, cols in groups.items():
                r = find(k)
                root = min(r, k, key=len)
                merged[root] |= cols
            groups = {k: v for k, v in merged.items() if v}
            canon_keys = list(groups.keys())
            vecs = self._emb.encode(canon_keys, normalize_embeddings=True)
            self._canon_vecs = {k: vecs[i] for i, k in enumerate(canon_keys)}

        if HAVE_RAPIDFUZZ and len(groups) > 1:
            keys = list(groups.keys())
            merged_to = {}
            for i in range(len(keys)):
                a = keys[i]
                for j in range(i+1, len(keys)):
                    b = keys[j]
                    if merged_to.get(a) or merged_to.get(b):
                        continue
                    score = float(fuzz.token_set_ratio(a, b))
                    if score >= self.cfg.fuzzy_threshold:
                        tgt = min(a, b, key=len)
                        src = b if tgt == a else a
                        groups[tgt] |= groups[src]
                        groups[src].clear()
                        merged_to[src] = tgt
            groups = {k: v for k, v in groups.items() if v}

        def pretty_name(members: Set[str]) -> str:
            return min((_norm_col(m) for m in members), key=lambda s: (len(s), s))

        canonical_map: Dict[str, str] = {}
        groups_named: Dict[str, List[str]] = {}
        for key, members in groups.items():
            canon = pretty_name(members)
            groups_named[canon] = sorted(list(members))
            for m in members:
                canonical_map[m] = canon

        self._canonical_map = canonical_map
        self._groups = groups_named

        if self._emb and groups_named:
            canon_keys = list(groups_named.keys())
            vecs = self._emb.encode(canon_keys, normalize_embeddings=True)
            self._canon_vecs = {k: vecs[i] for i, k in enumerate(canon_keys)}

        if self.cfg.save_mapping_json:
            try:
                with open(self.cfg.save_mapping_json, "w", encoding="utf-8") as f:
                    json.dump({"canonical_map": canonical_map, "groups": groups_named}, f, ensure_ascii=False, indent=2)
            except Exception:
                pass

        return canonical_map

    def _assign_unknown(self, col_name: str) -> str:
        nm = _norm_col(col_name)
        if self._emb and self._canon_vecs:
            k_list = list(self._canon_vecs.keys())
            import numpy as np
            q = self._emb.encode([nm], normalize_embeddings=True)[0]
            sims = np.array([float(q @ self._canon_vecs[k]) for k in k_list])
            j = int(np.argmax(sims))
            if sims[j] >= self.cfg.emb_threshold:
                return k_list[j]
        if HAVE_RAPIDFUZZ and self._groups:
            best, best_sc = None, -1.0
            for canon in self._groups.keys():
                sc = float(fuzz.token_set_ratio(nm, canon))
                if sc > best_sc:
                    best, best_sc = canon, sc
            if best is not None and best_sc >= self.cfg.fuzzy_threshold:
                return best
        return nm

    def transform_one(self, gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        RESERVED = set(self.cfg.reserved_cols)
        cols = [c for c in gdf.columns if c not in RESERVED]
        by_canon: Dict[str, List[str]] = defaultdict(list)
        for c in cols:
            canon = self._canonical_map.get(c)
            if not canon and self.cfg.auto_grow:
                canon = self._assign_unknown(c)
                self._canonical_map[c] = canon
                self._groups.setdefault(canon, []).append(c)
            if not canon:
                canon = _norm_col(c)
            by_canon[canon].append(c)

        out = gdf.copy()
        for canon, members in by_canon.items():
            if len(members) == 1:
                m = members[0]
                if m != canon:
                    out.rename(columns={m: canon}, inplace=True)
            else:
                merged = out[members].apply(lambda row: _concat_dedup(row.values, sep=self.cfg.join_sep), axis=1)
                out.drop(columns=members, inplace=True)
                out[canon] = merged

        if "geometry" not in out.columns:
            out["geometry"] = gdf.geometry

        return out

    def transform(self, gdfs: List[gpd.GeoDataFrame]) -> Tuple[List[gpd.GeoDataFrame], Dict[str, Any]]:
        if not self._canonical_map and not self.cfg.load_mapping_json:
            self.fit(gdfs)
        transformed = [self.transform_one(g) for g in gdfs]

        if self.cfg.save_mapping_json:
            try:
                with open(self.cfg.save_mapping_json, "w", encoding="utf-8") as f:
                    json.dump({"canonical_map": self._canonical_map, "groups": self._groups}, f, ensure_ascii=False, indent=2)
            except Exception:
                pass

        analysis = {
            "mode": "auto_nlp_no_dict",
            "fuzzy": HAVE_RAPIDFUZZ,
            "embeddings": (self._emb is not None),
            "fuzzy_threshold": self.cfg.fuzzy_threshold,
            "emb_model": self.cfg.emb_model if self._emb is not None else None,
            "emb_threshold": self.cfg.emb_threshold,
            "groups": self._groups,
            "canonical_map": self._canonical_map,
        }
        return transformed, analysis
