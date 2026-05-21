from __future__ import annotations

import logging
import warnings
import geopandas as gpd

from pathlib import Path
from abc import abstractmethod
from typing import Any, Dict, List, Sequence, Optional
from shapely.geometry import Polygon, MultiPolygon, GeometryCollection, base as shapely_base

from app.modules.merger.config import MergeConfig

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

class BaseMerger:
    """
    Interface commune pour tous les moteurs de fusion ZADA.
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

    @abstractmethod
    def merge(self) -> gpd.GeoDataFrame:
        """
        Exécute la fusion géographique.
        """
        pass

    @staticmethod
    def save(gdf: gpd.GeoDataFrame, path: Path | str ) -> None:
        """
        Sauvegarde utilitaire commune.
        """
        out = Path(path)

        ext = out.suffix.lower()

        driver = {
            ".geojson": "GeoJSON",
            ".json": "GeoJSON",
            ".gpkg": "GPKG",
            ".shp": "ESRI Shapefile",
        }.get(ext)

        if driver is None:
            raise ValueError(
                f"Unsupported extension: {ext}"
            )

        gdf.to_file(out, driver=driver)    


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