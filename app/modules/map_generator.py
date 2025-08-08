# ============================================================================
# app/modules/map_generator.py - Générateur de cartes et données pour Leaflet
# ============================================================================

import geopandas as gpd
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import logging

logger = logging.getLogger(__name__)

class MapDataGenerator:
    """Générateur de données cartographiques pour l'interface Leaflet"""
    
    def __init__(self):
        self.color_palette = {
            'intersection': '#FF6B6B',  # Rouge pour intersections
            'difference': '#4ECDC4',    # Turquoise pour différences
            'original': '#45B7D1',      # Bleu pour originaux
            'filtered': '#96CEB4'       # Vert pour filtrés
        }
    
    def gdf_to_leaflet_geojson(self, 
                              gdf: gpd.GeoDataFrame, 
                              properties_to_include: Optional[List[str]] = None) -> Dict:
        """
        Convertit un GeoDataFrame en GeoJSON compatible Leaflet
        
        Args:
            gdf: GeoDataFrame source
            properties_to_include: Liste des propriétés à inclure (toutes par défaut)
            
        Returns:
            Dictionnaire GeoJSON
        """
        if gdf.empty:
            return {
                'type': 'FeatureCollection',
                'features': []
            }
        
        try:
            # Convertir en WGS84 si nécessaire
            if gdf.crs and gdf.crs.to_string() != 'EPSG:4326':
                gdf_wgs84 = gdf.to_crs('EPSG:4326')
            else:
                gdf_wgs84 = gdf.copy()
            
            # Sélectionner les propriétés
            if properties_to_include:
                available_props = [col for col in properties_to_include if col in gdf_wgs84.columns]
                if available_props:
                    gdf_props = gdf_wgs84[available_props + ['geometry']]
                else:
                    gdf_props = gdf_wgs84[['geometry']]
            else:
                gdf_props = gdf_wgs84
            
            # Convertir en GeoJSON
            geojson_dict = json.loads(gdf_props.to_json())
            
            # Ajouter des styles selon le type
            if 'intersection_type' in gdf.columns:
                for i, feature in enumerate(geojson_dict['features']):
                    intersection_type = gdf.iloc[i].get('intersection_type', 'original')
                    feature['properties']['style'] = {
                        'color': self.color_palette.get(intersection_type, '#808080'),
                        'fillColor': self.color_palette.get(intersection_type, '#808080'),
                        'fillOpacity': 0.6,
                        'weight': 2
                    }
            
            return geojson_dict
        
        except Exception as e:
            logger.error(f"Erreur conversion GeoJSON: {e}")
            return {
                'type': 'FeatureCollection',
                'features': []
            }
    
    def generate_legend_data(self, gdf: gpd.GeoDataFrame) -> Dict:
        """
        Génère les données de légende pour la carte
        
        Args:
            gdf: GeoDataFrame source
            
        Returns:
            Dictionnaire des données de légende
        """
        legend_data = {}
        
        if 'intersection_type' in gdf.columns:
            type_counts = gdf['intersection_type'].value_counts()
            
            for intersection_type, count in type_counts.items():
                legend_data[intersection_type] = {
                    'color': self.color_palette.get(intersection_type, '#808080'),
                    'label': self._get_type_label(intersection_type),
                    'count': int(count)
                }
        
        return legend_data
    
    def _get_type_label(self, intersection_type: str) -> str:
        """Convertit le type d'intersection en libellé français"""
        labels = {
            'intersection': 'Intersections',
            'difference': 'Zones uniques', 
            'original': 'Zones originales',
            'filtered': 'Zones filtrées'
        }
        return labels.get(intersection_type, intersection_type.title())
    
    def get_map_bounds(self, gdf: gpd.GeoDataFrame) -> Optional[List[List[float]]]:
        """
        Calcule les limites géographiques pour centrer la carte
        
        Args:
            gdf: GeoDataFrame
            
        Returns:
            Liste [[lat_min, lng_min], [lat_max, lng_max]] ou None
        """
        if gdf.empty:
            return None
        
        try:
            # Convertir en WGS84 si nécessaire
            if gdf.crs and gdf.crs.to_string() != 'EPSG:4326':
                gdf_wgs84 = gdf.to_crs('EPSG:4326')
            else:
                gdf_wgs84 = gdf
            
            bounds = gdf_wgs84.total_bounds  # [minx, miny, maxx, maxy]
            
            # Convertir en format Leaflet [[lat_min, lng_min], [lat_max, lng_max]]
            return [
                [bounds[1], bounds[0]],  # [lat_min, lng_min]
                [bounds[3], bounds[2]]   # [lat_max, lng_max]
            ]
        
        except Exception as e:
            logger.error(f"Erreur calcul bounds: {e}")
            return None
    
    def prepare_criterion_options(self, gdf: gpd.GeoDataFrame) -> List[Dict]:
        """
        Prépare les options de critères pour l'interface
        
        Args:
            gdf: GeoDataFrame source
            
        Returns:
            Liste des options de critères
        """
        criterion_options = []
        
        # Exclure les colonnes techniques
        excluded_columns = {
            'geometry', 'original_source_id', 'original_source_name',
            'intersection_type', 'source_pair', 'source_names'
        }
        
        available_columns = [col for col in gdf.columns if col not in excluded_columns]
        
        for col in sorted(available_columns):
            if gdf[col].notna().any():  # Seulement si la colonne a des valeurs
                unique_values = gdf[col].dropna().unique()
                
                criterion_options.append({
                    'name': col,
                    'label': col.replace('_', ' ').title(),
                    'unique_count': len(unique_values),
                    'sample_values': list(unique_values[:5])  # Échantillon pour preview
                })
        
        return criterion_options