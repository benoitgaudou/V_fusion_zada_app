#!/usr/bin/env python3
"""
Script de test pour valider la correction de l'algorithme ZADA.
Ce script crée des données de test simples et teste l'algorithme corrigé.
"""

import geopandas as gpd
import pandas as pd
from shapely.geometry import Polygon
from pathlib import Path
import tempfile
import logging

# Configuration du logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

def create_test_data():
    """Crée des données de test avec des polygones qui se chevauchent."""
    
    # Créer 3 polygones qui se chevauchent partiellement
    poly1 = Polygon([(0, 0), (3, 0), (3, 3), (0, 3)])  # Carré de base
    poly2 = Polygon([(2, 2), (5, 2), (5, 5), (2, 5)])  # Carré décalé (chevauche poly1)
    poly3 = Polygon([(1, 1), (4, 1), (4, 4), (1, 4)])  # Carré central (chevauche poly1 et poly2)
    
    # Créer les GeoDataFrames
    gdf1 = gpd.GeoDataFrame({
        'id': [1],
        'zone': ['A'],
        'type': ['résidentiel'],
        'geometry': [poly1]
    }, crs="EPSG:4326")
    
    gdf2 = gpd.GeoDataFrame({
        'id': [2],
        'zone': ['B'], 
        'type': ['commercial'],
        'geometry': [poly2]
    }, crs="EPSG:4326")
    
    gdf3 = gpd.GeoDataFrame({
        'id': [3],
        'zone': ['C'],
        'type': ['industriel'],
        'geometry': [poly3]
    }, crs="EPSG:4326")
    
    return [gdf1, gdf2, gdf3]

def test_old_algorithm_overlap():
    """Simule l'ancien algorithme pour montrer le problème."""
    log.info("=== TEST ANCIEN ALGORITHME (DÉFAILLANT) ===")
    
    gdfs = create_test_data()
    
    # Calculer les aires individuelles
    total_area = sum(gdf.geometry.area.sum() for gdf in gdfs)
    
    # Union de toutes les géométries
    from shapely.ops import unary_union
    all_geoms = [geom for gdf in gdfs for geom in gdf.geometry]
    union_geom = unary_union(all_geoms)
    union_area = union_geom.area
    
    overlap_area = total_area - union_area
    overlap_percent = (overlap_area / total_area) * 100
    
    log.info(f"Aire totale des sources: {total_area:.2f}")
    log.info(f"Aire de l'union: {union_area:.2f}")
    log.info(f"Aire de chevauchement: {overlap_area:.2f} ({overlap_percent:.1f}%)")
    log.info(" PROBLÈME: Chevauchements détectés avec l'ancienne approche")
    
    return overlap_percent > 0

def test_zada_corrected():
    """Test l'algorithme ZADA corrigé."""
    log.info("\n=== TEST ALGORITHME ZADA CORRIGÉ ===")
    
    try:
        # Importer les modules corrigés
        from app.modules.zada_fusionC import ZadaMerger, MergeConfig
        
        # Créer les données de test
        gdfs = create_test_data()
        
        # Sauvegarder temporairement les GeoDataFrames
        temp_dir = Path(tempfile.mkdtemp())
        temp_files = []
        
        for i, gdf in enumerate(gdfs):
            temp_file = temp_dir / f"test_source_{i}.geojson"
            gdf.to_file(temp_file, driver="GeoJSON")
            temp_files.append(temp_file)
            log.info(f"Source {i}: {len(gdf)} entité(s), aire={gdf.geometry.area.sum():.2f}")
        
        # Configurer et exécuter ZADA corrigé
        config = MergeConfig(
            area_threshold_m2=0.1,  # Seuil très bas pour les tests
            output_crs="EPSG:4326"
        )
        
        merger = ZadaMerger(config)
        merger.load_sources(temp_files)
        
        # Fusion
        result = merger.merge()
        
        # Analyser les résultats
        log.info(f"Résultat: {len(result)} entités atomiques")
        
        if "type" in result.columns:
            type_counts = result["type"].value_counts()
            for type_name, count in type_counts.items():
                log.info(f"  {type_name}: {count}")
        
        # Vérifier l'atomicité
        from teste_zada import atomicity_report, AtomicityOptions
        
        opts = AtomicityOptions(
            metric_crs="EPSG:4326",  # Simplification pour test
            allow_holes=True,
            touch_ok=True,
            area_tol=1e-6,
            inter_area_tol=1e-6
        )
        
        report = atomicity_report(result, opts)
        
        log.info(f"Atomicité - Chevauchements: {report['overlap_pairs']} paires")
        log.info(f"Atomicité - Aire excès: {report['overlap_area_m2']:.6f}")
        log.info(f"Atomicité - Verdict: {report['is_atomic']}")
        
        # Nettoyage
        import shutil
        shutil.rmtree(temp_dir)
        
        if report["is_atomic"]:
            log.info(" SUCCÈS: Algorithme ZADA corrigé produit des entités atomiques !")
            return True
        else:
            log.error(" ÉCHEC: Chevauchements encore présents")
            return False
            
    except ImportError as e:
        log.error(f" Erreur d'import: {e}")
        log.error("Assurez-vous que les modules corrigés sont disponibles")
        return False
    except Exception as e:
        log.error(f"  Erreur lors du test: {e}")
        return False

def main():
    """Fonction principale de test."""
    log.info(" DÉBUT DES TESTS DE VALIDATION ZADA")
    
    # Test 1: Démontrer le problème avec l'ancien algorithme
    has_overlap_old = test_old_algorithm_overlap()
    
    # Test 2: Valider la correction
    is_atomic_new = test_zada_corrected()
    
    # Résumé
    log.info("\n" + "="*50)
    log.info(" RÉSUMÉ DES TESTS")
    log.info("="*50)
    log.info(f"Ancien algorithme - Chevauchements: {'OUI' if has_overlap_old else 'NON'}")
    log.info(f"Nouvel algorithme - Atomique: {'OUI' if is_atomic_new else 'NON'}")
    
    if has_overlap_old and is_atomic_new:
        log.info(" VALIDATION RÉUSSIE: Le problème est corrigé !")
        return True
    else:
        log.error(" VALIDATION ÉCHOUÉE: La correction n'est pas complète")
        return False

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)