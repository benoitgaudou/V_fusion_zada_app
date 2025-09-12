from pathlib import Path
from datetime import datetime
import sys
import geopandas as gpd
import pandas as pd
from shapely.geometry import Polygon

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from app.modules.map_exports import export_thematic_map

def make_dummy_gdf():
    geoms = [
        Polygon([(0,0),(1,0),(1,1),(0,1)]),
        Polygon([(2,0),(3,0),(3,1),(2,1)]),
        Polygon([(0,2),(1,2),(1,3),(0,3)]),
        Polygon([(2,2),(3,2),(3,3),(2,3)]),
    ]
    gdf = gpd.GeoDataFrame(
        {
            "id_zone": ["Z1","Z2","Z3","Z4"],
            "classe":  ["A","B","A","C"],   # champ catégoriel
        },
        geometry=geoms, crs="EPSG:4326"
    )
    return gdf

if __name__ == "__main__":
    out = ROOT / "exports_test"
    out.mkdir(exist_ok=True, parents=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    gdf = make_dummy_gdf()

    for fmt in ("geojson","gpkg","shp"):
        data = export_thematic_map(gdf, field_name="classe", palette_name="vibrant", fmt=fmt, layer="test_layer")
        p = out / f"thematic_classe_{stamp}.{ 'shp.zip' if fmt=='shp' else fmt }"
        p.write_bytes(data)
        print("OK ->", p, p.stat().st_size, "bytes")
