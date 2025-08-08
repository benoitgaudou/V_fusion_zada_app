# ============================================================================
# app/modules/geometry_utils.py - Utilitaires géométriques
# ============================================================================

import geopandas as gpd
import pandas as pd
from shapely.geometry import Polygon, MultiPolygon, GeometryCollection
from shapely.ops import unary_union
from typing import Optional, List
import logging

from .exceptions import GeometryProcessingError

logger = logging.getLogger(__name__)

class GeometryProcessor:
    """Processeur pour les opérations géométriques avancées"""
    
    @staticmethod
    def clean_geometry(geom) -> Optional:
        """
        Nettoyage géométrique robuste
        
        Args:
            geom: Géométrie Shapely à nettoyer
            
        Returns:
            Géométrie nettoyée ou None si invalide
        """
        if geom is None or geom.is_empty:
            return None
        
        try:
            # Correction des géométries invalides
            if not geom.is_valid:
                geom = geom.buffer(0)
            
            # Filtrage des types géométriques
            if isinstance(geom, (Polygon, MultiPolygon)):
                return geom
            elif isinstance(geom, GeometryCollection):
                polys = [g for g in geom.geoms if isinstance(g, (Polygon, MultiPolygon))]
                if polys:
                    return MultiPolygon(polys) if len(polys) > 1 else polys[0]
            return None
        except Exception as e:
            logger.warning(f"Erreur nettoyage géométrie: {e}")
            return None
    
    @staticmethod
    def apply_area_filter(gdf: gpd.GeoDataFrame, 
                         threshold: float, 
                         metric_crs: str = "EPSG:3857") -> gpd.GeoDataFrame:
        """
        Applique un filtre de superficie en coordonnées métriques
        
        Args:
            gdf: GeoDataFrame à filtrer
            threshold: Seuil en mètres carrés
            metric_crs: CRS métrique pour le calcul
            
        Returns:
            GeoDataFrame filtré
        """
        if threshold <= 0 or gdf.empty:
            return gdf
        
        logger.info(f"Application filtre superficie: {threshold} m²")
        
        # Sauvegarder CRS original
        original_crs = gdf.crs
        
        # Conversion temporaire
        gdf_metric = gdf.to_crs(metric_crs)
        
        # Calcul superficies
        areas = gdf_metric.geometry.area
        
        # Diagnostic
        logger.info(f"Superficies - Min: {areas.min():.0f}, Max: {areas.max():.0f}, "
                   f"Moyenne: {areas.mean():.0f} m²")
        
        # Filtrage
        mask = areas >= threshold
        filtered_gdf = gdf_metric[mask].copy()
        
        # Retour CRS original
        # result = filtered_gdf.to_crs(original_crs)
        
        removed_count = len(gdf) - len(filtered_gdf) # changeons pour garder le crs len(result)
        logger.info(f"Filtrage: {removed_count} polygones supprimés "
                   f"({removed_count/len(gdf)*100:.1f}%)")
        
        return result
