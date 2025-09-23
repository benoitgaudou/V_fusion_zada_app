from shapely.geometry import Polygon
import geopandas as gpd
from app.modules.map_generator import MapDataGenerator

def make_dummy_gdf():
    # 4 polygones carrés avec un champ catégoriel "classe"
    polys = [
        Polygon([(0,0),(1,0),(1,1),(0,1)]),
        Polygon([(2,0),(3,0),(3,1),(2,1)]),
        Polygon([(0,2),(1,2),(1,3),(0,3)]),
        Polygon([(2,2),(3,2),(3,3),(2,3)]),
    ]
    classes = ["A", "B", "A", "C"]
    gdf = gpd.GeoDataFrame({"classe": classes}, geometry=polys, crs="EPSG:4326")
    return gdf

if __name__ == "__main__":
    gdf = make_dummy_gdf()

    gen = MapDataGenerator()
    # 1) GeoJSON thématique
    result = gen.generate_thematic_geojson(gdf, field_name="classe", palette_name="default")
    assert result["success"], result.get("error")

    print("Legend items:")
    for item in result["legend"]["items"]:
        print(f"- {item['label']} | {item['color']} | count={item['count']}")

    # 2) Bounds pour centrer la carte
    bounds = gen.get_map_bounds(gdf)
    print("Bounds:", bounds)

    # 3) Sauvegarder le GeoJSON pour l’ouvrir dans un viewer (ex: geojson.io)
    import json, pathlib
    out = pathlib.Path("dummy_thematic.geojson")
    out.write_text(json.dumps(result["geojson"]), encoding="utf-8")
    print(f" Écrit: {out.resolve()}")
