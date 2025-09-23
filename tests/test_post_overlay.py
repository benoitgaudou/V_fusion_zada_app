# test_post_overlay.py
from shapely.geometry import Polygon
import geopandas as gpd

#  importe ta classe telle qu’elle est dans ton projet
from app.modules.zada_fusion import ZadaMerger, MergeConfig

def main():
    # Deux carrés qui se recouvrent partiellement
    poly1 = Polygon([(0,0),(2,0),(2,2),(0,2)])
    poly2 = Polygon([(1,1),(3,1),(3,3),(1,3)])

    # gdf1 et gdf2 avec colonnes homonymes/similaires
    gdf1 = gpd.GeoDataFrame(
        {
            "Activité": ["pêche, riz"],      # même nom que gdf2 -> overlay créera _1/_2
            "Territoires": ["Zone A"],       # pluriel vs singulier
            "original_source_id": [0],
            "original_source_name": ["src1"],
            "geometry": [poly1],
        },
        crs="EPSG:4326",
    )

    gdf2 = gpd.GeoDataFrame(
        {
            "Activité": ["riz | cacao"],     # même nom -> suffixes overlay
            "territoire": ["zone a"],        # proche de "Territoires"
            "original_source_id": [1],
            "original_source_name": ["src2"],
            "geometry": [poly2],
        },
        crs="EPSG:4326",
    )

    # Intersection -> colonnes dupliquées (Activité_1 / Activité_2) + colonnes proches
    inter = gpd.overlay(gdf1, gdf2, how="intersection")
    print("Colonnes AVANT pliage :", inter.columns.tolist())

    # Appel du pliage post-overlay (celui que tu as ajouté dans ZadaMerger)
    merger = ZadaMerger(MergeConfig())
    inter_folded = merger._fold_columns_after_overlay(
        inter,
        fuzzy_threshold=84,   # ↑ vers 88 si ça regroupe trop ; ↓ vers 80 si pas assez
        join_sep=", "
    )

    print("Colonnes APRÈS pliage :", inter_folded.columns.tolist())

    # Vérifie le résultat : une seule colonne 'activite' + une seule 'territoire'
    cols_to_show = [c for c in inter_folded.columns if c in ("activite", "territoire")]
    print(inter_folded[cols_to_show].head())

if __name__ == "__main__":
    main()
