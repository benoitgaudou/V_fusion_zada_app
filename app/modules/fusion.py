from __future__ import annotations

import re
import unicodedata

import logging
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple, Dict, Any

import geopandas as gpd
import pandas as pd
from shapely.geometry import Polygon, MultiPolygon, GeometryCollection, base as shapely_base
from shapely.ops import unary_union  # (peut être inutilisé selon vos usages)

# si tu veux réutiliser le même aligneur que dans le projet
from app.modules.column_auto_align import ColumnAutoAligner, AutoAlignCfg

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
    area_threshold_m2: float = 5.0
    input_crs_fallback: str = "EPSG:4326"
    output_crs: str = "EPSG:4326"
    metric_crs: str = "EPSG:3857"
    sample_unique_values: int = 10
    similarity_threshold: float = 0.30


class ZadaMerger:
    def __init__(self, config: Optional[MergeConfig] = None) -> None:
        self.config = config or MergeConfig()
        self._sources: List[gpd.GeoDataFrame] = []
        self._column_analysis: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------------ #
    # Chargement + alignement NLP
    # ------------------------------------------------------------------ #
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

                gdf["original_source_id"] = idx
                gdf["original_source_name"] = path.stem

                self._sources.append(gdf)
                logger.info("Chargé: %s (%d entités, CRS=%s)", path.name, len(gdf), gdf.crs)
            except Exception as exc:
                logger.error("Erreur de chargement %s: %s", path, exc)

        if len(self._sources) < 2:
            raise ValueError("Au moins deux sources sont nécessaires pour la fusion.")

        # Harmonisation NLP des colonnes (amont)
        try:
            aligner = ColumnAutoAligner(AutoAlignCfg(
                fuzzy_threshold=84.0,      # 78 = plus tolérant ; 88 = plus strict
                use_embeddings=False,      # True si vous avez sentence-transformers + torch
                emb_threshold=0.78,
                join_sep=", ",
                save_mapping_json="out/col_mapping.json",
                load_mapping_json=None,    # ou "out/col_mapping.json" pour rejouer un mapping validé
                auto_grow=True
            ))
            self._sources, self._column_analysis = aligner.transform(self._sources)
            logger.info(
                "Alignement auto (réduit) : %d groupes – ex: %s",
                len(self._column_analysis.get('groups', {})),
                list(self._column_analysis.get('groups', {}).keys())[:10]
            )
        except Exception as e:
            logger.warning("Alignement auto indisponible (%s) – fallback keep_all.", e)
            self._sources, self._column_analysis = self._harmonize_columns_keep_all(self._sources)

    # ------------------------------------------------------------------ #
    # API de fusion (compat routes.py)
    # ------------------------------------------------------------------ #
    def merge(self) -> gpd.GeoDataFrame:
        """Compatibilité : routes.py appelle merger.merge()."""
        return self.merge_union_iterative()

    def merge_union_iterative(self) -> gpd.GeoDataFrame:
        if len(self._sources) < 2:
            raise ValueError("Au moins deux sources sont nécessaires pour la fusion.")

        # 1) Passe tout en métrique et “nettoie”
        sources_m = [self._prep_metric(g) for g in self._sources]

        # 2) Overlay itératif en métrique avec overlay robuste
        gdf_merged = sources_m[0].copy()
        for idx in range(1, len(sources_m)):
            gdf_next = sources_m[idx].copy()
            logger.info(f"Fusion union itérative (métrique): couche 0 avec couche {idx}")
            gdf_merged = self._safe_overlay(gdf_merged, gdf_next, how="union")
            gdf_merged = gdf_merged[gdf_merged.geometry.notna() & ~gdf_merged.geometry.is_empty]

            # 🧹 pliage post-overlay
            before = len(gdf_merged.columns)
            gdf_merged = self._fold_columns_after_overlay(gdf_merged, fuzzy_threshold=84, join_sep=", ")
            logger.info(f"Post-overlay {idx}: {before}→{len(gdf_merged.columns)} colonnes")

        # 3) Pliage final (sécurité)
        gdf_merged = self._fold_columns_after_overlay(gdf_merged, fuzzy_threshold=84, join_sep=", ")

        # 4) Filtrage métrique (on y est déjà), puis repasse en CRS de sortie
        if self.config.area_threshold_m2 > 0:
            gdf_merged = self._metric_filter(gdf_merged, self.config.area_threshold_m2)

        # Reviens au CRS de sortie
        if gdf_merged.crs is None or gdf_merged.crs.to_string() != self.config.output_crs:
            gdf_merged = gdf_merged.to_crs(self.config.output_crs)

        gdf_merged["source"] = "fused_union"
        logger.info(f"Fusion union itérative terminée: {len(gdf_merged)} entités atomiques.")
        return gdf_merged

    # ------------------------------------------------------------------ #
    # Helpers robustification overlay
    # ------------------------------------------------------------------ #
    def _set_precision_if_available(self, geom, grid: float):
        if geom is None:
            return None
        try:
            from shapely import set_precision
            return set_precision(geom, grid)
        except Exception:
            return geom  # shapely<2 : on laisse tel quel

    def _make_valid_series(self, gser: gpd.GeoSeries) -> gpd.GeoSeries:
        """Rend valide (vectorisé si dispo), sinon buffer(0)."""
        try:
            g2 = gser.make_valid()           # geopandas 0.13+ / shapely 2
        except Exception:
            try:
                from shapely import make_valid as _mk
                g2 = gser.apply(lambda g: _mk(g) if g is not None else None)
            except Exception:
                g2 = gser.buffer(0)
        return g2

    def _prep_metric(self, gdf: gpd.GeoDataFrame, grid_size_m: float = 0.5) -> gpd.GeoDataFrame:
        """
        Reprojette en métrique, make_valid, snap sur grille (set_precision), buffer(0)
        -> réduit drastiquement les erreurs de noding.
        NOTE: grid_size_m relevé à 0.5 m (au lieu de 0.01) pour des jeux volumineux.
        """
        metr = gdf.to_crs(self.config.metric_crs).copy()

        # try vectorized make_valid; fallback to buffer(0)
        try:
            metr["geometry"] = metr.geometry.make_valid()
        except Exception:
            try:
                from shapely import make_valid as _mk
                metr["geometry"] = metr.geometry.apply(lambda g: _mk(g) if g is not None else None)
            except Exception:
                metr["geometry"] = metr.geometry.buffer(0)

        # snap to grid + clean
        def _snap(g):
            if g is None:
                return None
            try:
                from shapely import set_precision
                g = set_precision(g, grid_size_m)
            except Exception:
                pass
            return g

        metr["geometry"] = metr.geometry.apply(_snap).buffer(0)
        metr = metr[metr.geometry.notna() & ~metr.geometry.is_empty].copy()
        return metr


    def _safe_overlay(self, a: gpd.GeoDataFrame, b: gpd.GeoDataFrame, how: str = "union") -> gpd.GeoDataFrame:
        """
        Overlay robuste : essaie normal, puis make_valid/buffer(0), puis set_precision
        avec grilles croissantes, puis simplify si besoin.
        Suppose que 'a' et 'b' sont déjà en CRS métrique.
        """
        # 0) tentative directe
        try:
            return gpd.overlay(a, b, how=how)
        except Exception as e0:
            logger.warning("overlay(%s) direct a échoué: %s", how, e0)

        # 1) make_valid + buffer(0)
        def _mk_valid(df):
            df2 = df.copy()
            try:
                df2["geometry"] = df2.geometry.make_valid()
            except Exception:
                try:
                    from shapely import make_valid as _mk
                    df2["geometry"] = df2.geometry.apply(lambda g: _mk(g) if g is not None else None)
                except Exception:
                    df2["geometry"] = df2.geometry.buffer(0)
            df2["geometry"] = df2.geometry.buffer(0)
            df2 = df2[df2.geometry.notna() & ~df2.geometry.is_empty]
            return df2

        a1, b1 = _mk_valid(a), _mk_valid(b)
        try:
            return gpd.overlay(a1, b1, how=how)
        except Exception as e1:
            logger.warning("overlay(%s) après make_valid/buffer(0) a échoué: %s", how, e1)

        # 2) set_precision avec grilles croissantes
        grids = (0.1, 0.5, 1.0, 5.0, 10.0)
        for grid in grids:
            def _snap(df):
                df2 = df.copy()
                try:
                    from shapely import set_precision
                    df2["geometry"] = df2.geometry.apply(lambda g: set_precision(g, grid) if g is not None else None)
                except Exception:
                    # si shapely<2, on retente juste buffer(0)
                    pass
                df2["geometry"] = df2.geometry.buffer(0)
                df2 = df2[df2.geometry.notna() & ~df2.geometry.is_empty]
                return df2

            a2, b2 = _snap(a1), _snap(b1)
            try:
                res = gpd.overlay(a2, b2, how=how)
                logger.info("overlay(%s) OK avec set_precision grid=%.2f m", how, grid)
                return res
            except Exception as e2:
                logger.warning("overlay(%s) échoue encore (grid=%.2f m): %s", how, grid, e2)

        # 3) simplify (petites tolérances)
        for tol in (0.2, 0.5, 1.0, 2.0):
            a3 = a1.copy(); b3 = b1.copy()
            a3["geometry"] = a3.geometry.simplify(tol, preserve_topology=True).buffer(0)
            b3["geometry"] = b3.geometry.simplify(tol, preserve_topology=True).buffer(0)
            a3 = a3[a3.geometry.notna() & ~a3.geometry.is_empty]
            b3 = b3[b3.geometry.notna() & ~b3.geometry.is_empty]
            try:
                res = gpd.overlay(a3, b3, how=how)
                logger.info("overlay(%s) OK après simplify tol=%.2f m", how, tol)
                return res
            except Exception as e3:
                logger.warning("overlay(%s) échoue encore après simplify tol=%.2f m: %s", how, tol, e3)

        # Dernier recours : on relance l'exception d’origine (meilleur signalement en logs)
        raise RuntimeError("overlay(%s) a échoué malgré les réparations successives." % how)


    # ------------------------------------------------------------------ #
    # Nettoyage géométrie d'entrée
    # ------------------------------------------------------------------ #
    @staticmethod
    def _clean_geometry(geom: Optional[shapely_base.BaseGeometry]) -> Optional[shapely_base.BaseGeometry]:
        if geom is None or geom.is_empty:
            return None
        try:
            if not geom.is_valid:
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

    # ------------------------------------------------------------------ #
    # Helpers post-overlay (pliage des colonnes similaires)
    # ------------------------------------------------------------------ #
    def _norm_after_overlay(self, name: str) -> str:
        s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode("ascii")
        s = s.strip().lower()
        s = re.sub(r"[^\w]+", "_", s)
        s = re.sub(r"__+", "_", s).strip("_")
        # retire suffixes générés par overlay/pandas
        s = re.sub(r"(?:_(?:left|right|l|r|x|y)|_\d+|\.\d+)$", "", s)
        # pluriel naïf
        if len(s) > 3 and s.endswith("s"):
            s = s[:-1]
        return "geom" if s == "geometry" else s

    def _concat_dedup_vals(self, vals, sep: str = ", "):
        seen, out = set(), []
        for v in vals:
            if v is None:
                continue
            for part in re.split(r"[,\|;]", str(v)):
                t = part.strip()
                if t and t not in seen:
                    seen.add(t)
                    out.append(t)
        return sep.join(out) if out else None

    def _fold_columns_after_overlay(
        self,
        gdf: gpd.GeoDataFrame,
        fuzzy_threshold: int = 84,
        join_sep: str = ", ",
        reserved: Tuple[str, ...] = ("geometry", "original_source_id", "original_source_name", "type", "sources", "source_names"),
    ) -> gpd.GeoDataFrame:
        try:
            from rapidfuzz import fuzz
        except Exception:
            logging.warning("RapidFuzz non installé : pliage post-overlay ignoré.")
            return gdf

        cols = [c for c in gdf.columns if c not in reserved]

        # 1) grouper par nom “normalisé sans suffixes”
        groups: Dict[str, List[str]] = {}
        for c in cols:
            key = self._norm_after_overlay(c)
            groups.setdefault(key, []).append(c)

        # 2) fusionner des clés proches (fuzzy)
        keys = list(groups.keys())
        merged_to: Dict[str, str] = {}
        for i in range(len(keys)):
            a = keys[i]
            for j in range(i + 1, len(keys)):
                b = keys[j]
                if merged_to.get(a) or merged_to.get(b):
                    continue
                try:
                    sc = float(fuzz.token_set_ratio(a, b))
                except Exception:
                    sc = 100.0 if a == b else 0.0
                if sc >= fuzzy_threshold:
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
                merged = out[members].apply(
                    lambda row: self._concat_dedup_vals(row.values, sep=join_sep), axis=1
                )
                out.drop(columns=members, inplace=True)
                out[canon] = merged

        if "geometry" not in out.columns:
            out["geometry"] = gdf.geometry
        return out

    # ------------------------------------------------------------------ #
    # Analyse/legacy helpers (fallback)
    # ------------------------------------------------------------------ #
    def _analyze_columns(
        self, geo_dfs: Sequence[gpd.GeoDataFrame]
    ) -> Dict[str, Any]:
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

    # ------------------------------------------------------------------ #
    # Filtrage + IO + logs
    # ------------------------------------------------------------------ #
    def _metric_filter(self, gdf: gpd.GeoDataFrame, area_threshold_m2: float) -> gpd.GeoDataFrame:
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

    @staticmethod
    def save(gdf: gpd.GeoDataFrame, path: Path | str) -> None:
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
        logger.info("=== PHASE 4: FUSION TERMINÉE ===")
        logger.info("Total: %d entités", len(gdf))
        if "type" in gdf.columns:
            inter = (gdf["type"] == "intersection").sum()
            diff = (gdf["type"] == "difference").sum()
            orig = (gdf["type"] == "original").sum() if "original" in gdf["type"].unique() else 0
            logger.info("Intersections: %d | Différences: %d | Originaux: %d", inter, diff, orig)
