from typing import Sequence

from app.modules.merger.base_merger import BaseMerger
import geopandas as gpd
from pathlib import Path

from app.modules.merger.important_process import fusion_zada

class ZadaPairwiseMerger(BaseMerger):   
    """
    Moteur de fusion ZADA "pairwise" (Titouan).
    """

    def merge(self) -> gpd.GeoDataFrame:
        if not self._sources:
            raise ValueError("No sources loaded")

        # Fusion pairwise
#        merged = self._sources[0]
#        for gdf in self._sources[1:]:
#            merged = gpd.overlay(merged, gdf, how="union")

        merged_zada = fusion_zada(self._sources, col_zada='zada', col_to_remove=[])

        return merged_zada