# run_fusion.py
import logging
from pathlib import Path
from app.modules.file_loader import FileLoader, FileLoaderConfig
from app.modules.zada_fusion import ZadaMerger, MergeConfig

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("run_fusion")

# --- 1) CHARGEMENT DES SOURCES (ZIP Shapefile) ---
DATA_DIR = Path(r"D:\Dev\Projets\fusion_zada_app\Donnees")   # <-- adapte si besoin
UPLOADS = Path("uploads")
STAGE   = Path("stage_geojson")  # GeoJSON intermédiaires
OUT     = Path("out")

STAGE.mkdir(parents=True, exist_ok=True)
OUT.mkdir(parents=True, exist_ok=True)

loader_cfg = FileLoaderConfig(
    upload_folder=UPLOADS,
    force_output_crs="EPSG:4326",
    assume_input_crs="EPSG:4326",
    max_features_debug=None,     # ex: 1000 si tu veux accélérer en dev
    allow_network_proj=False,
    keep_extracted=False,
)
loader = FileLoader(loader_cfg)

# Récupère tous les ZIP présents (ex: shipe_1.zip, shipe_2.zip, shipe_3.zip)
zip_files = sorted(DATA_DIR.glob("*.zip"))
if len(zip_files) < 2:
    raise SystemExit(f"Il faut au moins 2 ZIP dans {DATA_DIR.resolve()}")

log.info("Fichiers trouvés: %s", [p.name for p in zip_files])

loaded = loader.process_uploaded_files(zip_files)

# Sauvegarde en GeoJSON intermédaire (pour Merger basé sur fichiers)
stage_paths = []
for gdf, stem in loaded:
    stage_path = STAGE / f"{stem}.geojson"
    stage_path.write_text(loader.to_geojson_str(gdf), encoding="utf-8")
    stage_paths.append(stage_path)
    log.info("Stage écrit: %s (%d features)", stage_path.name, len(gdf))

# --- 2) FUSION POO ---
merger_cfg = MergeConfig(
    area_threshold_m2=5.0,         # seuil micro-polygones (en m²)
    input_crs_fallback="EPSG:4326",
    output_crs="EPSG:4326",
    metric_crs="EPSG:3857",
    sample_unique_values=10,
    similarity_threshold=0.30,
)
merger = ZadaMerger(merger_cfg)

# On charge les GeoJSON de stage dans le Merger
merger.load_sources(stage_paths)

# Lancement fusion
result = merger.merge()

# --- 3) SORTIES ---
out_geojson = OUT / "fusion_zada.geojson"
result.to_file(out_geojson, driver="GeoJSON")
log.info("Fusion écrite: %s (features=%d, CRS=%s)", out_geojson, len(result), result.crs)

# Optionnel: sortie GPKG aussi
out_gpkg = OUT / "fusion_zada.gpkg"
result.to_file(out_gpkg, layer="fusion", driver="GPKG")
log.info("Fusion GPKG écrite: %s", out_gpkg)

print("\n--- RÉSUMÉ ---")
print(f"Sources       : {len(stage_paths)} fichiers")
print(f"Résultat      : {len(result)} entités")
if "type" in result.columns:
    n_inter = (result["type"] == "intersection").sum()
    n_diff  = (result["type"] == "difference").sum()
    print(f"Intersections : {n_inter}")
    print(f"Différences   : {n_diff}")
print(f"→ {out_geojson}")
print(f"→ {out_gpkg}")
