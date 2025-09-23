# test.py
import logging
from pathlib import Path
from app.modules.file_loader import FileLoader, FileLoaderConfig

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

cfg = FileLoaderConfig(
    upload_folder=Path("uploads"),
    force_output_crs="EPSG:4326",
    assume_input_crs="EPSG:4326",
    max_features_debug=None,
    allow_network_proj=False,
    keep_extracted=False,
)

loader = FileLoader(cfg)

# Dossier contenant tes ZIP (utilise une raw string ou des /)
data_dir = Path(r"D:\Dev\Projets\fusion_zada_app\Donnees")

# Récupère automatiquement tous les .zip (insensible à la casse)
files = sorted(
    [p for p in data_dir.glob("*.zip")]
)

if not files:
    raise SystemExit(f"Aucun .zip trouvé dans: {data_dir.resolve()}")

# Dossier de sortie
out_dir = Path("out"); out_dir.mkdir(parents=True, exist_ok=True)

loaded = loader.process_uploaded_files(files)
for gdf, stem in loaded:
    out_path = out_dir / f"{stem}.geojson"
    geojson_str = loader.to_geojson_str(gdf)
    out_path.write_text(geojson_str, encoding="utf-8")
    print(f"OK → {out_path} ({len(gdf)} features)")
