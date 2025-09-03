# ============================================================================
# app/modules/column_analyzer.py - Alignement auto des colonnes (sans dictionnaire)
# ============================================================================

from __future__ import annotations

import re
import json
import unicodedata
import logging
from dataclasses import dataclass
from collections import defaultdict
from typing import Dict, List, Tuple, Set, Any

import pandas as pd
import geopandas as gpd

logger = logging.getLogger(__name__)

try:
    # pip install rapidfuzz
    from rapidfuzz import fuzz
    _HAVE_RAPIDFUZZ = True
except Exception:
    _HAVE_RAPIDFUZZ = False
    logger.warning("RapidFuzz non installé : `pip install rapidfuzz` (requis pour l'alignement auto).")


# --- utils -------------------------------------------------------------------

def _norm_col(s: str) -> str:
    """Normalise un nom de colonne (ascii, minuscules, _1/_2 retirés, pluriel naïf)."""
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode("ascii")
    s = s.strip().lower()
    s = re.sub(r"[\s\-]+", "_", s)
    s = re.sub(r"[^\w]", "_", s)
    s = re.sub(r"__+", "_", s).strip("_")
    s = re.sub(r"_(\d+)$", "", s)       # suffixes _1/_2
    if len(s) > 3 and s.endswith("s"):  # pluriel naïf
        s = s[:-1]
    return s or "col"

def _concat_dedup(values, sep: str = ", "):
    """Concatène en dédupliquant, en éclatant déjà 'a, b|c;d'."""
    out, seen = [], set()
    for v in values:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            continue
        for part in re.split(r"[,\|;]", str(v)):
            t = part.strip()
            if t and t not in seen:
                seen.add(t); out.append(t)
    return sep.join(out) if out else None


# --- config ------------------------------------------------------------------

@dataclass
class ColumnAlignConfig:
    fuzzy_threshold: float = 84.0      # 78 = tolérant ; 88 = strict
    join_sep: str = ", "
    reserved_cols: Tuple[str, ...] = (
        "geometry", "original_source_id", "original_source_name", "type", "sources", "source_names"
    )
    save_mapping_json: str | None = "out/col_mapping.json"  # persistance (optionnel)
    load_mapping_json: str | None = None                    # réappliquer mapping validé
    auto_grow: bool = True          # apprend les nouveaux noms à la volée sur runs suivants


# --- classe principale --------------------------------------------------------

class ColumnAnalyzer:
    """
    Aligne automatiquement les colonnes SANS dictionnaire :
    - normalisation des noms
    - regroupement par similarité (RapidFuzz token_set_ratio)
    - fusion ligne-à-ligne des colonnes d'un même groupe
      (valeurs dédupliquées, séparées par une virgule)
    - mapping persistant pour stabiliser les runs et accueillir de nouvelles données
    """

    def __init__(self, cfg: ColumnAlignConfig | None = None) -> None:
        self.cfg = cfg or ColumnAlignConfig()
        self._canonical_map: Dict[str, str] = {}
        self._groups: Dict[str, List[str]] = {}

    # -------- API rétro-compat: simple analyse (non utilisée par l’alignement) --------
    @staticmethod
    def analyze_columns(geodataframes: List[gpd.GeoDataFrame]) -> Dict:
        """Conserve votre méthode d'analyse 'communes vs conflictuelles' (pour logs)."""
        columns_by_file = {}
        all_columns = set()

        for i, gdf in enumerate(geodataframes):
            columns = set(gdf.columns) - {'geometry'}
            columns_by_file[i] = columns
            all_columns.update(columns)

        shared_columns = []
        for col in all_columns:
            files_with_column = [i for i in range(len(geodataframes))
                                 if col in columns_by_file[i]]
            if len(files_with_column) > 1:
                shared_columns.append((col, files_with_column))

        common_columns, conflicting_columns = [], []
        for col, files in shared_columns:
            values_by_file = {}
            for fid in files:
                gdf = geodataframes[fid]
                if col in gdf.columns:
                    unique_values = set(gdf[col].dropna().astype(str).unique()[:10])
                    values_by_file[fid] = unique_values

            overlaps, file_ids = [], list(values_by_file.keys())
            for i in range(len(file_ids)):
                for j in range(i+1, len(file_ids)):
                    a, b = values_by_file[file_ids[i]], values_by_file[file_ids[j]]
                    if a and b:
                        inter, uni = len(a & b), len(a | b)
                        overlaps.append(inter/uni if uni else 0.0)

            avg_overlap = sum(overlaps)/len(overlaps) if overlaps else 0.0
            (common_columns if avg_overlap > 0.3 else conflicting_columns).append(col)

        logger.info("Colonnes communes: %d", len(common_columns))
        logger.info("Colonnes conflictuelles: %d", len(conflicting_columns))
        return {'common': common_columns, 'conflicting': conflicting_columns,
                'details': {'shared': shared_columns, 'by_file': columns_by_file}}

    # ------------------------- Alignement automatique -------------------------

    def _build_mapping(self, gdfs: List[gpd.GeoDataFrame]) -> Tuple[Dict[str, str], Dict[str, List[str]]]:
        """Construit (ou charge) le mapping 'colonne originale' -> 'canon'."""
        if self.cfg.load_mapping_json:
            with open(self.cfg.load_mapping_json, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._canonical_map = data.get("canonical_map", {})
            self._groups = data.get("groups", {})
            return self._canonical_map, self._groups

        if not _HAVE_RAPIDFUZZ:
            raise RuntimeError("RapidFuzz requis : installez-le avec `pip install rapidfuzz`.")

        # 1) collecter colonnes (hors réservées)
        all_cols: List[str] = []
        reserved = set(self.cfg.reserved_cols)
        for gdf in gdfs:
            for c in gdf.columns:
                if c not in reserved:
                    all_cols.append(c)
        all_cols = list(dict.fromkeys(all_cols))  # unique + ordre

        # 2) normaliser
        norm = {c: _norm_col(c) for c in all_cols}

        # 3) chaque nom normalisé crée un groupe
        groups: Dict[str, Set[str]] = defaultdict(set)
        for c in all_cols:
            groups[norm[c]].add(c)

        # 4) merge des groupes proches (fuzzy)
        keys = list(groups.keys())
        merged_to: Dict[str, str] = {}
        for i in range(len(keys)):
            a = keys[i]
            for j in range(i + 1, len(keys)):
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

        # 5) choisir un nom canonique lisible par groupe
        def pretty(members: Set[str]) -> str:
            return min((_norm_col(m) for m in members), key=lambda s: (len(s), s))

        canonical_map: Dict[str, str] = {}
        groups_named: Dict[str, List[str]] = {}
        for key, members in groups.items():
            canon = pretty(members)
            groups_named[canon] = sorted(list(members))
            for m in members:
                canonical_map[m] = canon

        self._canonical_map = canonical_map
        self._groups = groups_named

        if self.cfg.save_mapping_json:
            with open(self.cfg.save_mapping_json, "w", encoding="utf-8") as f:
                json.dump({"canonical_map": canonical_map, "groups": groups_named}, f, ensure_ascii=False, indent=2)

        return canonical_map, groups_named

    def _assign_unknown(self, col_name: str) -> str:
        """Attribue une nouvelle colonne à un canon existant ou crée un nouveau canon (auto_grow)."""
        nm = _norm_col(col_name)
        if not self._groups:
            return nm
        # meilleur match fuzzy contre les canons existants
        best, best_sc = None, -1.0
        for canon in self._groups.keys():
            sc = float(fuzz.token_set_ratio(nm, canon)) if _HAVE_RAPIDFUZZ else 0.0
            if sc > best_sc:
                best, best_sc = canon, sc
        if best is not None and best_sc >= self.cfg.fuzzy_threshold:
            return best
        return nm

    def transform_one(self, gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        """Applique le mapping et fusionne les valeurs (séparées par virgule)."""
        reserved = set(self.cfg.reserved_cols)
        cols = [c for c in gdf.columns if c not in reserved]
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

    # --------------------------- API de haut niveau ---------------------------

    def fit(self, gdfs: List[gpd.GeoDataFrame]) -> Dict[str, str]:
        self._build_mapping(gdfs)
        return self._canonical_map

    def transform(self, gdfs: List[gpd.GeoDataFrame]) -> Tuple[List[gpd.GeoDataFrame], Dict[str, Any]]:
        if not self._canonical_map and not self.cfg.load_mapping_json:
            self._build_mapping(gdfs)
        transformed = [self.transform_one(g) for g in gdfs]

        # persistance continue si auto_grow
        if self.cfg.save_mapping_json:
            try:
                with open(self.cfg.save_mapping_json, "w", encoding="utf-8") as f:
                    json.dump({"canonical_map": self._canonical_map, "groups": self._groups}, f, ensure_ascii=False, indent=2)
            except Exception:
                pass

        analysis = {
            "mode": "auto_fuzzy",
            "fuzzy_threshold": self.cfg.fuzzy_threshold,
            "groups": self._groups,
            "canonical_map": self._canonical_map,
        }
        return transformed, analysis

    def fit_transform(self, gdfs: List[gpd.GeoDataFrame]) -> Tuple[List[gpd.GeoDataFrame], Dict[str, Any]]:
        self.fit(gdfs)
        return self.transform(gdfs)
