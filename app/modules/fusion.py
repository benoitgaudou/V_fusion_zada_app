from __future__ import annotations

import logging
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple, Dict, Any

import geopandas as gpd
import pandas as pd
from shapely.geometry import Polygon, MultiPolygon, GeometryCollection, base as shapely_base
from shapely.ops import unary_union

logger = logging.getLogger(__name__)
if not logger.handlers:
	handler = logging.StreamHandler()
	formatter = logging.Formatter(
		"[%(levelname)s] %(asctime)s - %(name)s: %(message)s", "%H:%M:%S")
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

		self._sources, self._column_analysis = self._harmonize_columns_keep_all(self._sources)

	def merge_union_iterative(self) -> gpd.GeoDataFrame:
		if len(self._sources) < 2:
			raise ValueError("Au moins deux sources sont nécessaires pour la fusion.")
		
		gdf_merged = self._sources[0].copy()
		
		for idx in range(1, len(self._sources)):
			gdf_next = self._sources[idx].copy()
			logger.info(f"Fusion union itérative: couche 0 avec couche {idx}")
			try:
				gdf_merged = gpd.overlay(gdf_merged, gdf_next, how="union")
				gdf_merged["geometry"] = gdf_merged["geometry"].buffer(0)
				gdf_merged = gdf_merged[gdf_merged.geometry.notna() & ~gdf_merged.geometry.is_empty]
			except Exception as exc:
				logger.error(f"Erreur lors de l'overlay union entre couches : {exc}")
				raise exc
		if self.config.area_threshold_m2 > 0:
			gdf_merged = self._metric_filter(gdf_merged, self.config.area_threshold_m2)
		gdf_merged["source"] = "fused_union"
		logger.info(f"Fusion union itérative terminée: {len(gdf_merged)} entités atomiques.")
		return gdf_merged

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