# ============================================================================
# app/modules/column_analyzer.py - Analyseur de colonnes
# ============================================================================

from collections import defaultdict
from typing import Dict, List, Set, Tuple
import pandas as pd
import geopandas as gpd
import logging

logger = logging.getLogger(__name__)

class ColumnAnalyzer:
    """Analyseur des colonnes communes et conflictuelles entre GeoDataFrames"""
    
    @staticmethod
    def analyze_columns(geodataframes: List[gpd.GeoDataFrame]) -> Dict:
        """
        Analyse les colonnes partagées entre GeoDataFrames
        
        Args:
            geodataframes: Liste des GeoDataFrames à analyser
            
        Returns:
            Dictionnaire avec colonnes communes et conflictuelles
        """
        
        # Analyser colonnes par fichier
        columns_by_file = {}
        all_columns = set()
        
        for i, gdf in enumerate(geodataframes):
            columns = set(gdf.columns) - {'geometry'}
            columns_by_file[i] = columns
            all_columns.update(columns)
        
        # Identifier colonnes partagées
        shared_columns = []
        for col in all_columns:
            files_with_column = [i for i in range(len(geodataframes)) 
                               if col in columns_by_file[i]]
            if len(files_with_column) > 1:
                shared_columns.append((col, files_with_column))
        
        # Analyser similarité du contenu
        common_columns = []
        conflicting_columns = []
        
        for col, files in shared_columns:
            # Échantillonner valeurs uniques
            values_by_file = {}
            
            for file_id in files:
                gdf = geodataframes[file_id]
                if col in gdf.columns:
                    # Échantillon des valeurs uniques
                    unique_values = set(gdf[col].dropna().astype(str).unique()[:10])
                    values_by_file[file_id] = unique_values
            
            # Calculer chevauchement
            overlaps = []
            file_ids = list(values_by_file.keys())
            
            for i in range(len(file_ids)):
                for j in range(i+1, len(file_ids)):
                    fid1, fid2 = file_ids[i], file_ids[j]
                    val1, val2 = values_by_file[fid1], values_by_file[fid2]
                    
                    if val1 and val2:
                        intersection = len(val1.intersection(val2))
                        union = len(val1.union(val2))
                        overlap = intersection / union if union > 0 else 0
                        overlaps.append(overlap)
            
            # Classification
            avg_overlap = sum(overlaps) / len(overlaps) if overlaps else 0
            
            if avg_overlap > 0.3:  # Seuil de similarité
                common_columns.append(col)
            else:
                conflicting_columns.append(col)
        
        logger.info(f"Colonnes communes: {len(common_columns)}")
        logger.info(f"Colonnes conflictuelles: {len(conflicting_columns)}")
        
        return {
            'common': common_columns,
            'conflicting': conflicting_columns,
            'details': {
                'shared': shared_columns,
                'by_file': columns_by_file
            }
        }
