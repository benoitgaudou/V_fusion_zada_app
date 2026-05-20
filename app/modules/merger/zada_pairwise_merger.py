from typing import Sequence

from app.modules.merger.base_merger import BaseMerger
import geopandas as gpd
from pathlib import Path

class ZadaPairwiseMerger(BaseMerger):   
    """
    Moteur de fusion ZADA "pairwise" (Titouan).
    """

    def load_sources(
        self,
        paths: Sequence[Path | str]
    ) -> None:
        for path in paths:
            gdf = self._read_vector_file(Path(path))
            self._sources.append(gdf)

    def merge(self) -> gpd.GeoDataFrame:
        if not self._sources:
            raise ValueError("No sources loaded")

        # Fusion pairwise
        merged = self._sources[0]
        for gdf in self._sources[1:]:
            merged = gpd.overlay(merged, gdf, how="union")

        return merged