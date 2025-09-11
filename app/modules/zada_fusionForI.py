#  Fusion zada qui marche bien mais la superposition 

# zada_merger.py
from __future__ import annotations
import re
import unicodedata

import logging
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple, Dict, Any

import geopandas as gpd
import pandas as pd
from shapely.geometry import Polygon, MultiPolygon, GeometryCollection, base as shapely_base
from shapely.ops import unary_union


# Ajout de l'utilisation de NLP pour harmoniser les colonnes
from app.modules.column_auto_align import ColumnAutoAligner, AutoAlignCfg
#from app.modules.column_analyzer import ColumnAnalyzer, ColumnAlignConfig



# --- Configuration logging minimale (modifie le niveau dans ton script principal) ---
logger = logging.getLogger(__name__)
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "[%(levelname)s] %(asctime)s - %(name)s: %(message)s", "%H:%M:%S"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
logger.setLevel(logging.INFO)

warnings.filterwarnings("ignore")


@dataclass(frozen=True)
class MergeConfig:
    """
    Paramètres de fusion ZADA.

    Attributes
    ----------
    area_threshold_m2 : float
        Seuil de surface (m²) pour supprimer les micro-polygones (0 pour désactiver).
    input_crs_fallback : str
        CRS assumé si un fichier n'a pas de CRS (par défaut WGS84).
    output_crs : str
        CRS de sortie (par défaut WGS84).
    metric_crs : str
        CRS métrique temporaire pour les calculs de surface.
    sample_unique_values : int
        Taille d'échantillon max par colonne pour l'analyse sémantique légère.
    similarity_threshold : float
        Seuil de chevauchement moyen (Jaccard) au‑delà duquel une colonne est dite
        “commune” (sinon “conflictuelle”).
    """
    area_threshold_m2: float = 5.0
    input_crs_fallback: str = "EPSG:4326"
    output_crs: str = "EPSG:4326"
    metric_crs: str = "EPSG:3857"
    sample_unique_values: int = 10
    similarity_threshold: float = 0.30


class ZadaMerger:
    """
    Pipeline de fusion ZADA (POO).

    Usage
    -----
    merger = ZadaMerger(MergeConfig(area_threshold_m2=5))
    merger.load_sources(["a.shp", "b.shp", "c.geojson"])
    result = merger.merge()
    result.to_file("fusion.geojson", driver="GeoJSON")
    """

    def __init__(self, config: Optional[MergeConfig] = None) -> None:
        self.config = config or MergeConfig()
        self._sources: List[gpd.GeoDataFrame] = []
        self._column_analysis: Optional[Dict[str, Any]] = None

    # --------------------------------------------------------------------- #
    # Chargement & Préparation
    # --------------------------------------------------------------------- #
    def load_sources(self, paths: Sequence[Path | str]) -> None:
        """
        Charge et prépare les GeoDataFrames (CRS, nettoyage, métadonnées).

        Parameters
        ----------
        paths : Sequence[Path | str]
            Liste de chemins vers des couches vectorielles.
        """
        self._sources.clear()
        for idx, p in enumerate(paths):
            path = Path(p)
            try:
                gdf = gpd.read_file(path)
                if gdf.crs is None:
                    logger.warning(
                        "Le fichier %s n'a pas de CRS. On suppose %s.",
                        path.name, self.config.input_crs_fallback
                    )
                    gdf = gdf.set_crs(self.config.input_crs_fallback, allow_override=True)
                elif gdf.crs.to_string() != self.config.output_crs:
                    gdf = gdf.to_crs(self.config.output_crs)

                gdf = gdf[gdf.geometry.notna()]
                gdf["geometry"] = gdf["geometry"].apply(self._clean_geometry)
                gdf = gdf[gdf.geometry.notna()]

                # Métadonnées
                gdf["original_source_id"] = idx
                gdf["original_source_name"] = path.stem

                self._sources.append(gdf)
                logger.info("Chargé: %s (%d entités, CRS=%s)", path.name, len(gdf), gdf.crs)
            except Exception as exc:
                logger.error("Erreur de chargement %s: %s", path, exc)

        if len(self._sources) < 2:
            raise ValueError("Au moins deux sources sont nécessaires pour la fusion.")

        
        #2.  nouveau : harmonisation NLP auto (sans dictionnaire)
        aligner = ColumnAutoAligner(AutoAlignCfg(
            fuzzy_threshold=84.0,      # 78=plus tolérant ; 88=plus strict
            join_sep=", ",
            save_mapping_json="out/col_mapping.json",  # garde une trace du mapping
            load_mapping_json=None,    # ou "out/col_mapping.json" pour réappliquer un mapping validé
            auto_grow=True
        ))
        self._sources, self._column_analysis = aligner.transform(self._sources)
        logger.info("Alignement auto : %d groupes – ex: %s",
                    len(self._column_analysis.get('groups', {})),
                    list(self._column_analysis.get('groups', {}).keys())[:10])



    # --------------------------------------------------------------------- #
    # Fusion principale
    # --------------------------------------------------------------------- #
    def merge(self) -> gpd.GeoDataFrame:
        """
        Exécute la fusion complète (intersections + différences + filtrage métrique).

        Returns
        -------
        geopandas.GeoDataFrame
            Résultat final en `config.output_crs`.
        """
        intersections = self._compute_multi_source_intersections(self._sources)
        differences = self._compute_differences(self._sources, intersections)

        all_parts: List[gpd.GeoDataFrame] = intersections + differences
        if not all_parts:
            raise RuntimeError("Aucun résultat généré (intersections + différences vides).")
        result = gpd.GeoDataFrame(
            pd.concat(all_parts, ignore_index=True), crs=self.config.output_crs
        )
        result = result[result.geometry.notna()]
        result = result[~result.geometry.is_empty]

        #  Repli des colonnes dupliquées par overlay (ex: activite_1/activite_2 → activite)
        try:
            before_cols = len(result.columns)
            result = self._fold_columns_after_overlay(
                result,
                fuzzy_threshold=84,   # 78 = plus tolérant ; 88 = plus strict
                join_sep=", "
            )
            logger.info("Post-overlay: %d→%d colonnes (pliage)", before_cols, len(result.columns))
        except Exception as e:
            logger.warning("Pliage post-overlay ignoré (RapidFuzz installé ?) : %s", e)

        if self.config.area_threshold_m2 > 0:
            result = self._metric_filter(result, self.config.area_threshold_m2)

        self._log_summary(result)
        return result


    # --------------------------------------------------------------------- #
    # Utilitaires: nettoyage & colonnes
    # --------------------------------------------------------------------- #
    @staticmethod
    def _clean_geometry(geom: Optional[shapely_base.BaseGeometry]) -> Optional[shapely_base.BaseGeometry]:
        """Nettoyage géométrique robuste, retourne Polygon/MultiPolygon ou None."""
        if geom is None or geom.is_empty:
            return None
        try:
            if not geom.is_valid:
                # buffer(0) pour corriger les self-intersections
                geom = geom.buffer(0)

            if isinstance(geom, (Polygon, MultiPolygon)):
                return geom

            if isinstance(geom, GeometryCollection):
                polys = [g for g in geom.geoms if isinstance(g, (Polygon, MultiPolygon))]
                if not polys:
                    return None
                return MultiPolygon(polys) if len(polys) > 1 else polys[0]
        except Exception:
            return None
        return None
    
    def _norm_after_overlay(self, name: str) -> str:
        s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode("ascii")
        s = s.strip().lower()
        s = re.sub(r"[^\w]+", "_", s)
        s = re.sub(r"__+", "_", s).strip("_")
        # retire suffixes générés par overlay / pandas
        s = re.sub(r"(?:_(?:left|right|l|r|x|y)|_\d+|\.\d+)$", "", s)
        # pluriel naïf
        if len(s) > 3 and s.endswith("s"):
            s = s[:-1]
        return "geom" if s == "geometry" else s

    def _concat_dedup_vals(self, vals, sep=", "):
        seen, out = set(), []
        for v in vals:
            if v is None:
                continue
            for part in re.split(r"[,\|;]", str(v)):
                t = part.strip()
                if t and t not in seen:
                    seen.add(t); out.append(t)
        return sep.join(out) if out else None

    def _fold_columns_after_overlay(
        self,
        gdf: gpd.GeoDataFrame,
        fuzzy_threshold: int = 84,
        join_sep: str = ", ",
        reserved=("geometry","original_source_id","original_source_name","type","sources","source_names"),
    ) -> gpd.GeoDataFrame:
        try:
            from rapidfuzz import fuzz
        except Exception:
            raise RuntimeError("RapidFuzz requis : pip install rapidfuzz")

        cols = [c for c in gdf.columns if c not in reserved]
        # 1) grouper par nom “normalisé sans suffixes”
        groups = {}
        for c in cols:
            key = self._norm_after_overlay(c)
            groups.setdefault(key, []).append(c)

        # 2) fusionner des clés très proches (sécurité)
        keys = list(groups.keys())
        merged_to = {}
        for i in range(len(keys)):
            a = keys[i]
            for j in range(i+1, len(keys)):
                b = keys[j]
                if merged_to.get(a) or merged_to.get(b):
                    continue
                if fuzz.token_set_ratio(a, b) >= fuzzy_threshold:
                    tgt = min(a, b, key=len)
                    src = b if tgt == a else a
                    groups[tgt] = groups.get(tgt, []) + groups.get(src, [])
                    groups[src] = []
                    merged_to[src] = tgt
        groups = {k: v for k, v in groups.items() if v}

        # 3) plier (concat/dédup) et renommer
        out = gdf.copy()
        for canon, members in groups.items():
            if len(members) == 1:
                m = members[0]
                if m != canon:
                    out.rename(columns={m: canon}, inplace=True)
            else:
                merged = out[members].apply(lambda row: self._concat_dedup_vals(row.values, sep=join_sep), axis=1)
                out.drop(columns=members, inplace=True)
                out[canon] = merged

        if "geometry" not in out.columns:
            out["geometry"] = gdf.geometry
        return out


    def _analyze_columns(
        self, geo_dfs: Sequence[gpd.GeoDataFrame]
    ) -> Dict[str, Any]:
        """
        Analyse sommaire pour distinguer colonnes communes vs conflictuelles.
        """
        columns_per_file: Dict[int, set] = {}
        all_cols: set = set()
        for i, gdf in enumerate(geo_dfs):
            cols = set(gdf.columns) - {"geometry"}
            columns_per_file[i] = cols
            all_cols.update(cols)

        shared: List[Tuple[str, List[int]]] = []
        for col in all_cols:
            files_with_col = [i for i in range(len(geo_dfs)) if col in columns_per_file[i]]
            if len(files_with_col) > 1:
                shared.append((col, files_with_col))

        commons: List[str] = []
        conflicts: List[str] = []

        for col, file_ids in shared:
            values_by_file: Dict[int, set] = {}
            for fid in file_ids:
                gdf = geo_dfs[fid]
                if col in gdf.columns:
                    sample = (
                        gdf[col]
                        .dropna()
                        .astype(str)
                        .unique()[: self.config.sample_unique_values]
                    )
                    values_by_file[fid] = set(sample)

            overlaps: List[float] = []
            keys = list(values_by_file.keys())
            for i in range(len(keys)):
                for j in range(i + 1, len(keys)):
                    a = values_by_file[keys[i]]
                    b = values_by_file[keys[j]]
                    if a and b:
                        inter = len(a.intersection(b))
                        uni = len(a.union(b))
                        overlaps.append(inter / uni if uni else 0.0)

            mean_overlap = sum(overlaps) / len(overlaps) if overlaps else 0.0
            if mean_overlap > self.config.similarity_threshold:
                commons.append(col)
            else:
                conflicts.append(col)

        return {
            "communes": commons,
            "conflictuelles": conflicts,
            "details": {"partagees": shared, "par_fichier": columns_per_file},
        }

    def _harmonize_columns_keep_all(
        self, geo_dfs: List[gpd.GeoDataFrame]
    ) -> Tuple[List[gpd.GeoDataFrame], Dict[str, Any]]:
        """
        Harmonise en conservant toutes les colonnes (sans préfixe). Ajoute des métadonnées.
        """
        logger.info("Analyse des colonnes…")
        analysis = self._analyze_columns(geo_dfs)
        logger.info(
            "Colonnes communes (contenu fusionné par overlay): %d",
            len(analysis["communes"])
        )
        if analysis["communes"]:
            logger.debug("→ %s", ", ".join(analysis["communes"]))
        logger.info(
            "Colonnes conflictuelles (gardées telles quelles): %d",
            len(analysis["conflictuelles"])
        )
        if analysis["conflictuelles"]:
            logger.debug("→ %s", ", ".join(analysis["conflictuelles"]))

        kept: List[gpd.GeoDataFrame] = []
        for i, gdf in enumerate(geo_dfs):
            gdf_copy = gdf.copy()
            if "original_source_id" not in gdf_copy.columns:
                gdf_copy["original_source_id"] = i
            if "original_source_name" not in gdf_copy.columns:
                gdf_copy["original_source_name"] = f"source_{i}"
            kept.append(gdf_copy)
            logger.info("Source %d: %d colonnes conservées (toutes).", i, len(gdf_copy.columns))

        return kept, {
            "colonnes_communes": analysis["communes"],
            "colonnes_conflictuelles": analysis["conflictuelles"],
            "toutes_colonnes": True,
        }
    @staticmethod
    def _convert_numpy_types(obj):
        """
        Convertit les types numpy en types Python natifs pour la sérialisation JSON.
        """
        import numpy as np
        import pandas as pd
        
        if isinstance(obj, (np.integer, np.int32, np.int64, np.int8, np.int16)):
            return int(obj)
        elif isinstance(obj, (np.floating, np.float32, np.float64, np.float16)):
            return float(obj)
        elif isinstance(obj, np.bool_):
            return bool(obj)
        elif isinstance(obj, np.ndarray):
            return obj.tolist()
        elif isinstance(obj, pd.Series):
            return obj.tolist()
        elif hasattr(obj, 'item'):  # autres types numpy scalaires
            return obj.item()
        elif isinstance(obj, dict):
            return {k: ZadaMerger._convert_numpy_types(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [ZadaMerger._convert_numpy_types(item) for item in obj]
        elif isinstance(obj, tuple):
            return tuple(ZadaMerger._convert_numpy_types(item) for item in obj)
        else:
            return obj
    # --------------------------------------------------------------------- #
    # Intersections & Différences
    # --------------------------------------------------------------------- #

    def _compute_multi_source_intersections(
        self, geo_dfs: Sequence[gpd.GeoDataFrame]
    ) -> List[gpd.GeoDataFrame]:
        """Calcule toutes les intersections multi-sources sans duplication."""
        logger.info("=== PHASE 2: INTERSECTIONS MULTI-SOURCES ===")
        results: List[gpd.GeoDataFrame] = []
        n = len(geo_dfs)
        
        # Préparer des dataframes simplifiés pour éviter les conflits de colonnes
        simple_dfs = []
        for i, gdf in enumerate(geo_dfs):
            # Créer un dataframe minimal avec seulement les colonnes essentielles
            simple_gdf = gpd.GeoDataFrame({
                'geometry': gdf.geometry,
                'original_source_name': gdf['original_source_name'],
                'source_index': i  # Ajouter un index de source
            }, crs=gdf.crs)
            simple_dfs.append(simple_gdf)
        
        from itertools import combinations
        
        # Pour gérer les intersections multiples, nous allons utiliser une approche différente
        # qui évite les problèmes de superposition en calculant les intersections de manière hiérarchique
        
        # D'abord, calculer toutes les intersections par paires
        pairwise_intersections = {}
        
        for i in range(n):
            for j in range(i + 1, n):
                gdf1, gdf2 = simple_dfs[i], simple_dfs[j]
                name1 = str(gdf1["original_source_name"].iloc[0])
                name2 = str(gdf2["original_source_name"].iloc[0])
                
                try:
                    # Renommer les colonnes pour éviter les conflits
                    gdf1_clean = gdf1.rename(columns={
                        'original_source_name': 'source_name_1',
                        'source_index': 'source_index_1'
                    })
                    gdf2_clean = gdf2.rename(columns={
                        'original_source_name': 'source_name_2',
                        'source_index': 'source_index_2'
                    })
                    
                    # Calculer l'intersection
                    inter = gpd.overlay(gdf1_clean, gdf2_clean, how="intersection")
                    
                    if not inter.empty:
                        # Stocker pour utilisation ultérieure
                        key = f"{i}+{j}"
                        pairwise_intersections[key] = inter
                        
                        # Ajouter aux résultats
                        inter = inter.copy()
                        inter["type"] = "intersection"
                        inter["sources"] = key
                        inter["source_names"] = f"{name1}+{name2}"
                        inter["intersection_level"] = 2
                        
                        # Garder seulement les colonnes standardisées
                        final_cols = ['geometry', 'type', 'sources', 'source_names', 'intersection_level']
                        inter = inter[[col for col in final_cols if col in inter.columns]].copy()
                        
                        results.append(inter)
                        logger.info("Intersection %s ↔ %s: %d intersections", name1, name2, len(inter))
                    
                except Exception as exc:
                    logger.error("Erreur intersection %d-%d: %s", i, j, exc)
        
        # Maintenant, pour les intersections de niveau supérieur (3+ sources),
        # nous allons les calculer de manière incrémentielle
        if n >= 3:
            # Pour chaque combinaison de 3 sources ou plus
            for k in range(3, n + 1):
                for source_indices in combinations(range(n), k):
                    source_names = [str(simple_dfs[i]["original_source_name"].iloc[0]) for i in source_indices]
                    logger.info("Traitement intersection %d sources: %s", k, "+".join(source_names))
                    
                    try:
                        # Construire l'intersection progressive à partir des intersections par paires
                        current_geom = None
                        
                        # Commencer par l'intersection des deux premières sources
                        key1 = f"{source_indices[0]}+{source_indices[1]}"
                        if key1 in pairwise_intersections:
                            current_geom = unary_union(pairwise_intersections[key1].geometry)
                        
                        # Ajouter progressivement les autres sources
                        for i in range(2, k):
                            if current_geom is None:
                                break
                                
                            # Vérifier s'il existe une intersection avec cette source
                            found_intersection = False
                            for j in range(i):
                                key = f"{source_indices[j]}+{source_indices[i]}"
                                if key in pairwise_intersections:
                                    new_geom = unary_union(pairwise_intersections[key].geometry)
                                    current_geom = current_geom.intersection(new_geom)
                                    found_intersection = True
                                    break
                            
                            if not found_intersection or current_geom.is_empty:
                                current_geom = None
                                break
                        
                        if current_geom is not None and not current_geom.is_empty:
                            # Créer le GeoDataFrame pour cette intersection multiple
                            inter_gdf = gpd.GeoDataFrame({
                                'geometry': [current_geom],
                                'type': 'intersection',
                                'sources': '+'.join(map(str, source_indices)),
                                'source_names': '+'.join(source_names),
                                'intersection_level': k
                            }, crs=simple_dfs[0].crs)
                            
                            results.append(inter_gdf)
                            logger.info("→ Intersection %d sources: 1 zone", k)
                    
                    except Exception as exc:
                        logger.error("Erreur intersection %s: %s", "+".join(map(str, source_indices)), exc)
        
        return results

    def _compute_differences(
        self,
        geo_dfs: Sequence[gpd.GeoDataFrame],
        intersections: Sequence[gpd.GeoDataFrame],
    ) -> List[gpd.GeoDataFrame]:
        """Soustraction correcte des intersections multiples."""
        logger.info("=== PHASE 3: DIFFÉRENCES ===")
        diffs: List[gpd.GeoDataFrame] = []

        if intersections:
            # Créer une union de toutes les géométries d'intersection
            all_geoms = []
            for inter in intersections:
                # Extraire les géométries de manière robuste
                if hasattr(inter, 'geometry'):
                    geoms = inter.geometry
                    for geom in geoms:
                        if geom is not None and not geom.is_empty:
                            # Convertir en liste de géométries simples si nécessaire
                            if isinstance(geom, (MultiPolygon, GeometryCollection)):
                                all_geoms.extend([g for g in geom.geoms if isinstance(g, (Polygon, MultiPolygon))])
                            elif isinstance(geom, (Polygon, MultiPolygon)):
                                all_geoms.append(geom)
            
            if all_geoms:
                try:
                    # Créer l'union de manière progressive pour éviter les problèmes de forme
                    if len(all_geoms) == 1:
                        union_geom = all_geoms[0]
                    else:
                        # Utiliser une approche progressive pour éviter les erreurs de forme
                        union_geom = all_geoms[0]
                        for geom in all_geoms[1:]:
                            if geom is not None and not geom.is_empty:
                                try:
                                    union_geom = union_geom.union(geom)
                                except Exception:
                                    # En cas d'erreur, passer à la géométrie suivante
                                    continue
                    
                    if union_geom.is_empty:
                        logger.info("Union des intersections vide.")
                        union_gdf = None
                    else:
                        # Créer un GeoDataFrame pour l'union
                        union_gdf = gpd.GeoDataFrame({'geometry': [union_geom]}, crs=geo_dfs[0].crs)
                    
                    if union_gdf is not None:
                        for i, gdf in enumerate(geo_dfs):
                            name = str(gdf["original_source_name"].iloc[0])
                            logger.info("Différence pour %s…", name)
                            
                            try:
                                # Utiliser overlay pour la différence (plus robuste)
                                diff = gpd.overlay(gdf, union_gdf, how='difference')
                                
                                if not diff.empty:
                                    diff["type"] = "difference"
                                    diff["sources"] = str(i)
                                    diff["source_names"] = name
                                    
                                    diffs.append(diff)
                                    logger.info("→ %d zones uniques", len(diff))
                                else:
                                    logger.info("→ Aucune zone unique.")
                                    
                            except Exception as exc:
                                logger.error("Erreur différence %d: %s", i, exc)
                    else:
                        logger.info("Aucune union valide des intersections.")
                        
                except Exception as exc:
                    logger.error("Erreur création union des intersections: %s", exc)
                    # Fallback: utiliser les sources originales
                    logger.info("Fallback: utilisation des sources originales")
                    for i, gdf in enumerate(geo_dfs):
                        copy = gdf.copy()
                        name = str(copy["original_source_name"].iloc[0])
                        copy["type"] = "original"
                        copy["sources"] = str(i)
                        copy["source_names"] = name
                        diffs.append(copy)
            else:
                logger.info("Aucune géométrie d'intersection valide.")
        else:
            logger.info("Aucune intersection → conservation des sources originales.")
            for i, gdf in enumerate(geo_dfs):
                copy = gdf.copy()
                name = str(copy["original_source_name"].iloc[0])
                copy["type"] = "original"
                copy["sources"] = str(i)
                copy["source_names"] = name
                diffs.append(copy)
        
        return diffs

    # --------------------------------------------------------------------- #
    # Filtrage métrique
    # --------------------------------------------------------------------- #
    def _metric_filter(self, gdf: gpd.GeoDataFrame, area_threshold_m2: float) -> gpd.GeoDataFrame:
        """
        Filtre les géométries dont l'aire (en m²) est < `area_threshold_m2`.
        """
        if gdf.empty or gdf.geometry.isna().all():
            return gdf

        if gdf.crs is None or gdf.crs.to_string() != self.config.output_crs:
            logger.info("Harmonisation CRS → %s avant filtrage.", self.config.output_crs)
            gdf = gdf.set_crs(self.config.output_crs, allow_override=True)

        logger.info(
            "Application du filtrage métrique (seuil=%.2f m²) via %s…",
            area_threshold_m2, self.config.metric_crs
        )
        metric = gdf.to_crs(self.config.metric_crs)
        areas = metric.geometry.area

        try:
            if areas.max() < area_threshold_m2:
                new_thr = float(max(areas.min() * 0.1, areas.quantile(0.05)))
                logger.warning(
                    "Seuil trop élevé (max=%.2f m²). Ajustement automatique → %.2f m²",
                    float(areas.max()), new_thr
                )
                area_threshold_m2 = new_thr
        except Exception:
            pass

        initial = len(metric)
        mask = areas >= area_threshold_m2
        filt = metric[mask].copy()
        removed = initial - len(filt)
        pct = (removed / initial * 100.0) if initial else 0.0
        logger.info("Filtrage: %d micro-polygones supprimés (%.1f%%).", removed, pct)

        return filt.to_crs(self.config.output_crs)

    # --------------------------------------------------------------------- #
    # Divers
    # --------------------------------------------------------------------- #
    @staticmethod
    def save(gdf: gpd.GeoDataFrame, path: Path | str) -> None:
        """
        Sauvegarde utilitaire (déduit le driver depuis l'extension).

        .geojson → GeoJSON, .gpkg → GPKG, .shp → ESRI Shapefile, etc.
        """
        out = Path(path)
        ext = out.suffix.lower()
        driver = {
            ".geojson": "GeoJSON",
            ".json": "GeoJSON",
            ".gpkg": "GPKG",
            ".shp": "ESRI Shapefile",
        }.get(ext, None)

        if driver is None:
            raise ValueError(
                f"Extension non supportée pour {out.name}. "
                "Utilise .geojson, .gpkg ou .shp."
            )
        gdf.to_file(out, driver=driver)
        logger.info("Fichier écrit: %s (%s)", out, driver)

    def _log_summary(self, gdf: gpd.GeoDataFrame) -> None:
        """Petit récapitulatif lisible dans les logs."""
        logger.info("=== PHASE 4: FUSION TERMINÉE ===")
        logger.info("Total: %d entités", len(gdf))
        if "type" in gdf.columns:
            inter = (gdf["type"] == "intersection").sum()
            diff = (gdf["type"] == "difference").sum()
            orig = (gdf["type"] == "original").sum() if "original" in gdf["type"].unique() else 0
            logger.info("Intersections: %d | Différences: %d | Originaux: %d", inter, diff, orig)
            