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

                # Ajout simple du nom source sous forme z1, z2, etc.
                gdf["source_names"] = f"z{idx + 1}"

                self._sources.append(gdf)
                logger.info("Chargé: %s (%d entités, CRS=%s)", path.name, len(gdf), gdf.crs)
            except Exception as exc:
                logger.error("Erreur de chargement %s: %s", path, exc)

        if len(self._sources) < 2:
            raise ValueError("Au moins deux sources sont nécessaires pour la fusion.")




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
        intersections = self._compute_pairwise_intersections(self._sources)
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
            # Normalisation anti-"nan" sur les colonnes texte
            result = self._sanitize_object_columns(result, sep=", ")
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
        """Concaténation NA-safe + déduplication; ne casse PAS les valeurs contenant '+'."""
        seen, out = set(), []
        for v in vals:
            # ignore None/NaN pour tous types
            if v is None or pd.isna(v):
                continue
            # éclate seulement sur , ; | (on NE touche PAS aux '+')
            for part in re.split(r"[,\|;]", str(v)):
                t = part.strip()
                if t and t.lower() != "nan" and t not in seen:
                    seen.add(t)
                    out.append(t)
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
    
    def _sanitize_object_columns(self, gdf: gpd.GeoDataFrame, sep: str = ", ") -> gpd.GeoDataFrame:
        """
        - remplace NaN par None
        - remplace les séparateurs [ , ; | ] par 'sep' (sans toucher aux '+')
        - supprime les chaînes vides
        """
        pat = re.compile(r"\s*[,\|;]\s*")
        out = gdf.copy()
        for c in out.columns:
            if c == "geometry" or not pd.api.types.is_object_dtype(out[c]):
                continue
            col = out[c]
            col = col.where(~col.isna(), None)
            col = col.apply(lambda x: pat.sub(sep, x) if isinstance(x, str) else x)
            col = col.apply(lambda x: None if (isinstance(x, str) and not x.strip()) else x)
            out[c] = col
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

    # --------------------------------------------------------------------- #
    # Intersections & Différences
    # --------------------------------------------------------------------- #
          
    def _compute_pairwise_intersections(
        self, geo_dfs: Sequence[gpd.GeoDataFrame]
    ) -> List[gpd.GeoDataFrame]:
        """Intersections par paires avec conservation des attributs."""
        logger.info("=== PHASE 2: INTERSECTIONS ===")
        results: List[gpd.GeoDataFrame] = []
        n = len(geo_dfs)

        for i in range(n):
            for j in range(i + 1, n):
                gdf1, gdf2 = geo_dfs[i], geo_dfs[j]
                name1 = str(gdf1["original_source_name"].iloc[0])
                name2 = str(gdf2["original_source_name"].iloc[0])
                logger.info("Intersection %s ↔ %s", name1, name2)

                try:
                    inter = gpd.overlay(gdf1, gdf2, how="intersection")
                    if inter.empty:
                        logger.info("Aucune intersection trouvée.")
                        continue

                    inter["type"] = "intersection"
                    inter["sources"] = f"{i}+{j}"
                    inter["source_names"] = f"{name1}+{name2}"
                    results.append(inter)
                    logger.info("→ %d intersections", len(inter))
                except Exception as exc:
                    logger.error("Erreur intersection %d-%d: %s", i, j, exc)
                    logger.debug("Colonnes GDF1: %s", list(gdf1.columns))
                    logger.debug("Colonnes GDF2: %s", list(gdf2.columns))
        return results

    def _compute_differences(
        self,
        geo_dfs: Sequence[gpd.GeoDataFrame],
        intersections: Sequence[gpd.GeoDataFrame],
    ) -> List[gpd.GeoDataFrame]:
        """Soustraction des intersections pour obtenir les zones uniques."""
        logger.info("=== PHASE 3: DIFFÉRENCES ===")
        diffs: List[gpd.GeoDataFrame] = []

        if intersections:
            logger.info("Calcul de l'union des intersections…")
            all_inter = gpd.GeoDataFrame(
                pd.concat(intersections, ignore_index=True),
                crs=geo_dfs[0].crs,
            )
            union_geom = unary_union(all_inter.geometry)
            union_gdf = gpd.GeoDataFrame([{"geometry": union_geom}], crs=geo_dfs[0].crs)

            for i, gdf in enumerate(geo_dfs):
                name = str(gdf["original_source_name"].iloc[0])
                logger.info("Différence pour %s…", name)
                try:
                    diff = gpd.overlay(gdf, union_gdf, how="difference")
                    if diff.empty:
                        logger.info("→ Aucune zone unique.")
                        continue
                    diff["type"] = "difference"
                    diff["sources"] = str(i)
                    diff["source_names"] = name
                    diffs.append(diff)
                    logger.info("→ %d zones uniques", len(diff))
                except Exception as exc:
                    logger.error("Erreur différence %d: %s", i, exc)
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
            