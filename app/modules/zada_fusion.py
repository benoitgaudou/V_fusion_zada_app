# ============================================================================
# app/modules/zada_fusion.py - Moteur de fusion ZADA
# ============================================================================

import geopandas as gpd
import pandas as pd
from shapely.geometry import Polygon, MultiPolygon, GeometryCollection
from shapely.ops import unary_union
from pathlib import Path
from tqdm import tqdm
from typing import List, Tuple, Dict, Optional
import logging

from .geometry_utils import GeometryProcessor
from .column_analyzer import ColumnAnalyzer
from .exceptions import FusionError, GeometryProcessingError

logger = logging.getLogger(__name__)

class ZADAFusionEngine:
    """
    Moteur de fusion ZADA amélioré pour l'intégration Flask
    
    Ce moteur implémente l'algorithme de fusion ZADA avec une architecture
    modulaire adaptée à une application web.
    """
    
    def __init__(self, area_threshold: float = 100, metric_crs: str = "EPSG:3857"):
        """
        Initialise le moteur de fusion
        
        Args:
            area_threshold: Seuil de superficie en mètres carrés
            metric_crs: Système de coordonnées métrique pour les calculs
        """
        self.area_threshold = area_threshold
        self.metric_crs = metric_crs
        self.geometry_processor = GeometryProcessor()
        self.column_analyzer = ColumnAnalyzer()
        
        # Statistiques de la fusion
        self.fusion_stats = {}
    
    def prepare_geodataframes(self, 
                            geodataframes: List[Tuple[gpd.GeoDataFrame, str]]) -> List[gpd.GeoDataFrame]:
        """
        Phase 1: Préparation des GeoDataFrames
        
        Args:
            geodataframes: Liste de tuples (GeoDataFrame, nom_source)
            
        Returns:
            Liste des GeoDataFrames préparés
        """
        logger.info("=== PHASE 1: PRÉPARATION ===")
        prepared_gdfs = []
        
        for i, (gdf, source_name) in enumerate(geodataframes):
            try:
                # Copie de travail
                gdf_work = gdf.copy()
                
                # Filtrer les géométries nulles
                gdf_work = gdf_work[gdf_work.geometry.notna()]
                
                if gdf_work.empty:
                    logger.warning(f"Source {source_name}: aucune géométrie valide")
                    continue
                
                # Nettoyage géométrique
                logger.info(f"Nettoyage géométrique: {source_name}")
                gdf_work['geometry'] = gdf_work['geometry'].apply(
                    self.geometry_processor.clean_geometry
                )
                gdf_work = gdf_work[gdf_work.geometry.notna()]
                
                if gdf_work.empty:
                    logger.warning(f"Source {source_name}: aucune géométrie après nettoyage")
                    continue
                
                # Ajouter métadonnées de traçabilité
                gdf_work['original_source_id'] = i
                gdf_work['original_source_name'] = source_name
                
                prepared_gdfs.append(gdf_work)
                logger.info(f"Préparé: {source_name} ({len(gdf_work)} entités)")
                
            except Exception as e:
                logger.error(f"Erreur préparation {source_name}: {e}")
                continue
        
        if len(prepared_gdfs) < 2:
            raise FusionError("Au moins 2 fichiers valides requis pour la fusion")
        
        return prepared_gdfs
    
    def harmonize_columns(self, 
                         geodataframes: List[gpd.GeoDataFrame]) -> Tuple[List[gpd.GeoDataFrame], Dict]:
        """
        Harmonisation des colonnes entre GeoDataFrames
        
        Args:
            geodataframes: Liste des GeoDataFrames à harmoniser
            
        Returns:
            Tuple (GeoDataFrames harmonisés, métadonnées d'harmonisation)
        """
        logger.info("Analyse et harmonisation des colonnes...")
        
        # Analyser les colonnes
        analysis = self.column_analyzer.analyze_columns(geodataframes)
        
        common_columns = analysis['common']
        conflicting_columns = analysis['conflicting']
        
        logger.info(f"Colonnes communes détectées: {len(common_columns)}")
        if common_columns:
            logger.info(f"  → {', '.join(common_columns)}")
        
        logger.info(f"Colonnes conflictuelles détectées: {len(conflicting_columns)}")
        if conflicting_columns:
            logger.info(f"  → {', '.join(conflicting_columns)}")
        
        # Conservation de toutes les colonnes (pas de préfixe)
        harmonized_gdfs = []
        for i, gdf in enumerate(geodataframes):
            gdf_copy = gdf.copy()
            
            # S'assurer que les métadonnées sont présentes
            if 'original_source_id' not in gdf_copy.columns:
                gdf_copy['original_source_id'] = i
            if 'original_source_name' not in gdf_copy.columns:
                gdf_copy['original_source_name'] = f"source_{i}"
            
            harmonized_gdfs.append(gdf_copy)
            logger.info(f"Source {i}: {len(gdf_copy.columns)} colonnes conservées")
        
        return harmonized_gdfs, {
            'common_columns': common_columns,
            'conflicting_columns': conflicting_columns,
            'analysis': analysis
        }
    
    def compute_intersections(self, geodataframes: List[gpd.GeoDataFrame]) -> List[gpd.GeoDataFrame]:
        """
        Phase 2: Calcul des intersections entre toutes les paires de GeoDataFrames
        
        Args:
            geodataframes: Liste des GeoDataFrames harmonisés
            
        Returns:
            Liste des GeoDataFrames d'intersections
        """
        logger.info("=== PHASE 2: INTERSECTIONS ===")
        intersections = []
        n_files = len(geodataframes)
        
        # Intersections par paires
        for i in range(n_files):
            for j in range(i + 1, n_files):
                gdf1, gdf2 = geodataframes[i], geodataframes[j]
                
                # Récupérer les noms pour l'affichage
                name1 = gdf1['original_source_name'].iloc[0] if not gdf1.empty else f"source_{i}"
                name2 = gdf2['original_source_name'].iloc[0] if not gdf2.empty else f"source_{j}"
                
                logger.info(f"Intersection {name1} ↔ {name2}")
                
                try:
                    # Calcul de l'intersection avec GeoPandas
                    intersection_result = gpd.overlay(gdf1, gdf2, how='intersection')
                    
                    if not intersection_result.empty:
                        # Ajouter métadonnées d'intersection
                        intersection_result['intersection_type'] = 'intersection'
                        intersection_result['source_pair'] = f"{i}+{j}"
                        intersection_result['source_names'] = f"{name1}+{name2}"
                        
                        # Nettoyer les géométries résultantes
                        intersection_result['geometry'] = intersection_result['geometry'].apply(
                            self.geometry_processor.clean_geometry
                        )
                        intersection_result = intersection_result[intersection_result.geometry.notna()]
                        
                        if not intersection_result.empty:
                            intersections.append(intersection_result)
                            logger.info(f"  → {len(intersection_result)} intersections valides")
                        else:
                            logger.info(f"  → Aucune intersection après nettoyage")
                    else:
                        logger.info(f"  → Aucune intersection géométrique")
                
                except Exception as e:
                    logger.error(f"Erreur intersection {name1}-{name2}: {e}")
                    continue
        
        logger.info(f"Total: {len(intersections)} groupes d'intersections")
        return intersections
    
    def compute_differences(self, 
                          geodataframes: List[gpd.GeoDataFrame], 
                          intersections: List[gpd.GeoDataFrame]) -> List[gpd.GeoDataFrame]:
        """
        Phase 3: Calcul des différences (zones uniques à chaque source)
        
        Args:
            geodataframes: GeoDataFrames originaux
            intersections: Liste des intersections calculées
            
        Returns:
            Liste des GeoDataFrames de différences
        """
        logger.info("=== PHASE 3: DIFFÉRENCES ===")
        differences = []
        
        if intersections:
            # Union de toutes les intersections
            logger.info("Calcul de l'union des intersections...")
            
            all_intersections = gpd.GeoDataFrame(
                pd.concat(intersections, ignore_index=True),
                crs=geodataframes[0].crs
            )
            
            # Créer l'union géométrique
            try:
                union_geometry = unary_union(all_intersections.geometry)
                union_gdf = gpd.GeoDataFrame(
                    [{'geometry': union_geometry}],
                    crs=geodataframes[0].crs
                )
                
                # Calculer les différences pour chaque source
                for i, gdf in enumerate(geodataframes):
                    name = gdf['original_source_name'].iloc[0] if not gdf.empty else f"source_{i}"
                    logger.info(f"Différence pour {name}...")
                    
                    try:
                        difference_result = gpd.overlay(gdf, union_gdf, how='difference')
                        
                        if not difference_result.empty:
                            # Ajouter métadonnées
                            difference_result['intersection_type'] = 'difference'
                            difference_result['source_pair'] = str(i)
                            difference_result['source_names'] = name
                            
                            # Nettoyer les géométries
                            difference_result['geometry'] = difference_result['geometry'].apply(
                                self.geometry_processor.clean_geometry
                            )
                            difference_result = difference_result[difference_result.geometry.notna()]
                            
                            if not difference_result.empty:
                                differences.append(difference_result)
                                logger.info(f"  → {len(difference_result)} zones uniques")
                            else:
                                logger.info(f"  → Aucune zone unique après nettoyage")
                        else:
                            logger.info(f"  → Aucune zone unique")
                    
                    except Exception as e:
                        logger.error(f"Erreur différence {name}: {e}")
                        continue
            
            except Exception as e:
                logger.error(f"Erreur calcul union: {e}")
                # Fallback: conserver les sources originales
                differences = self._fallback_to_originals(geodataframes)
        
        else:
            logger.info("Aucune intersection, conservation des sources originales")
            differences = self._fallback_to_originals(geodataframes)
        
        return differences
    
    def _fallback_to_originals(self, geodataframes: List[gpd.GeoDataFrame]) -> List[gpd.GeoDataFrame]:
        """Fallback: conservation des GeoDataFrames originaux"""
        fallback_differences = []
        
        for i, gdf in enumerate(geodataframes):
            gdf_copy = gdf.copy()
            name = gdf['original_source_name'].iloc[0] if not gdf.empty else f"source_{i}"
            
            gdf_copy['intersection_type'] = 'original'
            gdf_copy['source_pair'] = str(i)
            gdf_copy['source_names'] = name
            
            fallback_differences.append(gdf_copy)
        
        return fallback_differences
    
    def finalize_fusion(self, 
                       intersections: List[gpd.GeoDataFrame], 
                       differences: List[gpd.GeoDataFrame]) -> Optional[gpd.GeoDataFrame]:
        """
        Phase 4: Finalisation de la fusion
        
        Args:
            intersections: Liste des intersections
            differences: Liste des différences
            
        Returns:
            GeoDataFrame final fusionné
        """
        logger.info("=== PHASE 4: FUSION FINALE ===")
        
        all_results = intersections + differences
        
        if not all_results:
            logger.error("Aucun résultat à fusionner")
            return None
        
        try:
            # Concaténation de tous les résultats
            final_result = gpd.GeoDataFrame(
                pd.concat(all_results, ignore_index=True),
                crs=all_results[0].crs
            )
            
            logger.info("Nettoyage final...")
            
            # Supprimer les géométries vides/nulles
            initial_count = len(final_result)
            final_result = final_result[final_result.geometry.notna()]
            final_result = final_result[~final_result.geometry.is_empty]
            
            logger.info(f"Géométries vides supprimées: {initial_count - len(final_result)}")
            
            # Application du filtrage par superficie
            if self.area_threshold > 0:
                final_result = self.geometry_processor.apply_area_filter(
                    final_result, self.area_threshold, self.metric_crs
                )
            
            # Calculer les statistiques finales
            self._compute_fusion_statistics(final_result)
            
            logger.info(f" FUSION TERMINÉE: {len(final_result)} entités finales")
            
            return final_result
        
        except Exception as e:
            logger.error(f"Erreur fusion finale: {e}")
            raise FusionError(f"Erreur lors de la fusion finale: {e}")
    
    def _compute_fusion_statistics(self, result_gdf: gpd.GeoDataFrame):
        """Calcule les statistiques de la fusion"""
        if 'intersection_type' in result_gdf.columns:
            type_counts = result_gdf['intersection_type'].value_counts()
            
            self.fusion_stats = {
                'total_features': len(result_gdf),
                'intersections': type_counts.get('intersection', 0),
                'differences': type_counts.get('difference', 0),
                'originals': type_counts.get('original', 0),
                'area_threshold': self.area_threshold,
                'crs': str(result_gdf.crs)
            }
            
            logger.info("STATISTIQUES FINALES:")
            for key, value in self.fusion_stats.items():
                logger.info(f"  {key}: {value}")
        else:
            self.fusion_stats = {
                'total_features': len(result_gdf),
                'area_threshold': self.area_threshold,
                'crs': str(result_gdf.crs)
            }
    
    def execute_fusion(self, 
                      geodataframes: List[Tuple[gpd.GeoDataFrame, str]]) -> Optional[gpd.GeoDataFrame]:
        """
        Exécute la fusion complète ZADA
        
        Args:
            geodataframes: Liste de tuples (GeoDataFrame, nom_source)
            
        Returns:
            GeoDataFrame fusionné ou None si erreur
        """
        try:
            logger.info("Démarrage de la fusion ZADA...")
            
            # Phase 1: Préparation
            prepared_gdfs = self.prepare_geodataframes(geodataframes)
            
            # Harmonisation des colonnes
            harmonized_gdfs, column_metadata = self.harmonize_columns(prepared_gdfs)
            
            # Phase 2: Intersections
            intersections = self.compute_intersections(harmonized_gdfs)
            
            # Phase 3: Différences
            differences = self.compute_differences(harmonized_gdfs, intersections)
            
            # Phase 4: Fusion finale
            final_result = self.finalize_fusion(intersections, differences)
            
            if final_result is not None:
                logger.info("Fusion ZADA réussie!")
                return final_result
            else:
                logger.error("Échec de la fusion ZADA")
                return None
        
        except Exception as e:
            logger.error(f"Erreur critique fusion ZADA: {e}")
            raise FusionError(f"Fusion ZADA échouée: {e}")
    
    def get_fusion_statistics(self) -> Dict:
        """Retourne les statistiques de la dernière fusion"""
        return self.fusion_stats.copy()
    
    def filter_by_criterion(self, 
                           gdf: gpd.GeoDataFrame, 
                           criterion: str, 
                           values: Optional[List] = None) -> gpd.GeoDataFrame:
        """
        Filtre le résultat de fusion selon un critère spécifique
        
        Args:
            gdf: GeoDataFrame fusionné
            criterion: Nom de la colonne critère
            values: Valeurs spécifiques à filtrer (optionnel)
            
        Returns:
            GeoDataFrame filtré
        """
        if criterion not in gdf.columns:
            logger.warning(f"Critère '{criterion}' non trouvé dans les colonnes")
            return gdf
        
        try:
            if values is None:
                # Retourner toutes les entités avec une valeur non-nulle pour ce critère
                filtered = gdf[gdf[criterion].notna()]
            else:
                # Filtrer selon les valeurs spécifiées
                filtered = gdf[gdf[criterion].isin(values)]
            
            logger.info(f"Filtrage par '{criterion}': {len(filtered)}/{len(gdf)} entités")
            return filtered
        
        except Exception as e:
            logger.error(f"Erreur filtrage par critère: {e}")
            return gdf
    
    def export_to_geojson(self, 
                         gdf: gpd.GeoDataFrame, 
                         output_path: Path) -> bool:
        """
        Exporte le résultat en GeoJSON
        
        Args:
            gdf: GeoDataFrame à exporter
            output_path: Chemin de sortie
            
        Returns:
            True si succès, False sinon
        """
        try:
            # S'assurer que le dossier parent existe
            output_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Exporter en GeoJSON
            gdf.to_file(output_path, driver='GeoJSON')
            
            logger.info(f"Export GeoJSON réussi: {output_path}")
            return True
        
        except Exception as e:
            logger.error(f"Erreur export GeoJSON: {e}")
            return False