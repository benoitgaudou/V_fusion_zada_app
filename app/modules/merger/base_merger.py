from __future__ import annotations

from abc import abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Sequence, Optional

import geopandas as gpd

from app.modules.merger.config import MergeConfig


class BaseMerger:
    """
    Interface commune pour tous les moteurs de fusion ZADA.
    """

    def __init__(self, config: Optional[MergeConfig] = None) -> None:
        self.config = config or MergeConfig()
        self._sources: List[gpd.GeoDataFrame] = []
        self._column_analysis: Optional[Dict[str, Any]] = None


    @abstractmethod
    def load_sources(self, paths: Sequence[Path | str] ) -> None:
        """
        Charge les sources géographiques.
        """
        pass

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