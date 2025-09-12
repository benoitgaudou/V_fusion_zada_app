from typing import Tuple, Optional, Dict, Any
import geopandas as gpd

from app.modules.map_generator import MapDataGenerator
from app.modules.nlp.card_exports import export_geojson_bytes, export_gpkg_bytes, export_shapefile_zip

def export_thematic_map( gdf: gpd.GeoDataFrame,
                        field_name: str,
                        palette_name: str,
                        fmt: str,
                        layer: str = "zada_thematic"
                        ) -> bytes:
    """
    Génère le GDF thématique puis l'exporte au format demandé ('geojson' | 'gpkg' | 'shp')
    """ 
    
    gen = MapDataGenerator()
    gdf_export, _, _ = gen.build_thematic_gdf(gdf, field_name=field_name, palette_name=palette_name)
    fmt = (fmt or "").lower()
    if fmt == "geojson":
        return export_geojson_bytes(gdf_export)
    if fmt == "gpkg":
        return export_gpkg_bytes(gdf_export, layer=layer)
    if fmt == "shp":
        return export_shapefile_zip(gdf_export)
    raise ValueError("Format non supporté (utiliser : shp| gpkg | geojson)")