# scripts/test_nlp_exports.py
import os, sys, io, zipfile
from pathlib import Path
from datetime import datetime

import pandas as pd
import geopandas as gpd
from shapely.geometry import Polygon

# --- Rendez "app/" importable ---
ROOT = Path(__file__).resolve().parents[1]  # racine du repo
sys.path.append(str(ROOT))  # permet: from app.modules... import ...

# --- importe tes fonctions d'export ---
from app.modules.nlp.card_exports import (
    build_selection_gdf,
    export_from_results,
)

def make_dummy_corpus() -> gpd.GeoDataFrame:
    # 3 petits carrés en EPSG:4326
    geoms = [
        Polygon([(0,0),(1,0),(1,1),(0,1)]),
        Polygon([(2,0),(3,0),(3,1),(2,1)]),
        Polygon([(0,2),(1,2),(1,3),(0,3)]),
    ]
    gdf = gpd.GeoDataFrame(
        {
            "id_zone": ["Z1", "Z2", "Z3"],
            "corpus_texte": [
                "sécheresse agricole et feux",
                "biodiversité altérée",
                "risques d'inondation"
            ],
            "geometry": geoms,
        },
        crs="EPSG:4326",
    )
    return gdf

def make_dummy_results() -> pd.DataFrame:
    # Simule un résultat de search(): indices + similarités
    # On prend Z3 (0.91) puis Z1 (0.73)
    return pd.DataFrame({
        "row_idx": [2, 0],
        "similarite": [0.91, 0.73],
    })

def main():
    out_dir = ROOT / "exports_test"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    corpus = make_dummy_corpus()
    results = make_dummy_results()

    # 1) Vérifie le GDF de sélection (colonnes & CRS)
    sel_gdf = build_selection_gdf(corpus, results)
    print("=== Selection GDF ===")
    print(sel_gdf.head())
    print("CRS:", sel_gdf.crs)
    print("Colonnes:", list(sel_gdf.columns))

    # 2) Export GeoJSON
    geojson_bytes = export_from_results("geojson", corpus, results)
    geojson_path = out_dir / f"zada_nlp_{stamp}.geojson"
    geojson_path.write_bytes(geojson_bytes)
    print("OK ->", geojson_path, f"({geojson_path.stat().st_size} bytes)")

    # 3) Export GPKG
    gpkg_bytes = export_from_results("gpkg", corpus, results)
    gpkg_path = out_dir / f"zada_nlp_{stamp}.gpkg"
    gpkg_path.write_bytes(gpkg_bytes)
    print("OK ->", gpkg_path, f"({gpkg_path.stat().st_size} bytes)")

    # 4) Export SHP (zip)
    shp_zip_bytes = export_from_results("shp", corpus, results)
    shp_zip_path = out_dir / f"zada_nlp_{stamp}.shp.zip"
    shp_zip_path.write_bytes(shp_zip_bytes)
    print("OK ->", shp_zip_path, f"({shp_zip_path.stat().st_size} bytes)")

    # (Optionnel) Petit contrôle lecture GeoJSON/GPKG
    try:
        gdf_check = gpd.read_file(geojson_path)
        print("GeoJSON lu, lignes:", len(gdf_check))
    except Exception as e:
        print("Lecture GeoJSON a échoué:", e)

    try:
        gdf_check = gpd.read_file(gpkg_path)
        print("GPKG lu, lignes:", len(gdf_check))
    except Exception as e:
        print("Lecture GPKG a échoué:", e)

    # (Optionnel) Inspecte le contenu du ZIP shapefile
    try:
        with zipfile.ZipFile(shp_zip_path, "r") as z:
            print("Contenu ZIP SHP:", z.namelist())
    except Exception as e:
        print("ZIP SHP invalide:", e)

if __name__ == "__main__":
    main()
