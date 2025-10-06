import io, os, tempfile, zipfile, unicodedata
from typing import Dict, Any, List, Tuple

import pandas as pd
import geopandas as gpd


#________________ sélection à partir des résultats_____________
# ---------------- sélection à partir des résultats ----------------
def build_selection_gdf(
    corpus_gdf: gpd.GeoDataFrame,
    results_df: pd.DataFrame
) -> gpd.GeoDataFrame:
    """
    Construit le GeoDataFrame exportable à partir du corpus et des résultats.
    - mode semantic  -> colonne 'nlp_similarity' (0..1)
    - mode keyword   -> colonne 'nlp_score'      (0..1, couverture AND)
    """
    if results_df is None or results_df.empty or corpus_gdf is None or corpus_gdf.empty:
        return gpd.GeoDataFrame(columns=["id_zone", "geometry"], geometry="geometry", crs="EPSG:4326")

    # 1) Déterminer le mode (priorité aux données du df)
    mode = str(results_df.iloc[0].get("mode", "semantic")).lower()

    # 2) Sous-ensemble géométrique dans l’ordre du ranking
    sel = corpus_gdf.iloc[results_df["row_idx"]][["id_zone", "corpus_texte", "geometry"]].copy()
    sel = sel.reset_index(drop=True)

    # 3) Colonnes communes
    sel["nlp_rank"]    = pd.Series(range(1, len(sel) + 1), dtype="int32")
    sel["nlp_preview"] = sel["corpus_texte"].astype(str).str.slice(0, 200)
    sel["nlp_mode"]    = mode

    # 4) Valeur selon le mode
    if mode == "keyword":
        # On privilégie 'score' (déjà la couverture), sinon 'couverture'
        if "score" in results_df.columns:
            vals = results_df["score"].astype(float).to_numpy()
        elif "couverture" in results_df.columns:
            vals = results_df["couverture"].astype(float).to_numpy()
        else:
            vals = np.zeros(len(sel), dtype=np.float32)
        sel["nlp_score"] = vals.astype("float32")
        keep_cols = ["id_zone", "nlp_score", "nlp_rank", "nlp_preview", "nlp_mode", "geometry"]
    else:
        # semantic
        if "similarite" in results_df.columns:
            vals = results_df["similarite"].astype(float).to_numpy()
        elif "score" in results_df.columns:
            # fallback si 'score' == similarité
            vals = results_df["score"].astype(float).to_numpy()
        else:
            vals = np.zeros(len(sel), dtype=np.float32)
        sel["nlp_similarity"] = vals.astype("float32")
        keep_cols = ["id_zone", "nlp_similarity", "nlp_rank", "nlp_preview", "nlp_mode", "geometry"]

    # 5) Normalisation CRS -> EPSG:4326
    if sel.geometry.crs is not None:
        sel = sel.to_crs("EPSG:4326")
    else:
        sel.set_crs("EPSG:4326", inplace=True)

    # 6) Types
    sel["id_zone"] = sel["id_zone"].astype(str)

    return sel[keep_cols]

#_______________ GeoJSON ____________

def export_geojson_bytes(gdf: gpd.GeoDataFrame) -> bytes:
    if gdf is None or gdf.empty:
        return b'{"type":"FeatureCollection", "features": []}'
    gdf = gdf.fillna("")
    bio = io.BytesIO()
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "export.geojson")
        gdf.to_file(path, driver="GeoJSON", encoding="utf-8", index=False)
        with open(path, "rb") as f:
            bio.write(f.read())
    bio.seek(0)
    return bio.getvalue()

# ________________GeoPackage____________
def export_gpkg_bytes(gdf: gpd.GeoDataFrame, layer: str = "zada_nlp") -> bytes:
    if gdf is None or gdf.empty:
        # GPKG vide (Optionnel) : renvoyer un GPKG minimal
        gdf = gpd.GeoDataFrame(columns=["id_zone", "geometry"], geometry="geometry", crs="EPSG:4326")
    gdf = gdf.fillna("")
    bio = io.BytesIO()
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "export.gpkg")
        gdf.to_file(path, driver="GPKG", layer=layer, index=False)
        with open(path, 'rb') as f:
            bio.write(f.read())
    bio.seek(0)
    return bio.getvalue()

#____________ Shapefile (ZIP) _____________

def _truncate_for_shapefile(cols: List[str]) -> List[str]:
    seen = {}
    out = []
    for c in cols:
        base = c[:10]
        name = base
        i = 1
        while name.lower() in seen:
            s = str(i)
            name = (base[: (10 -len(s))] + s)[:10]
            i += 1
        seen[name.lower()] = True
        out.append(name)
    return out

def _shp_field_names(cols: List[str]) -> List[str]:
    """
    Convertit les noms en ASCII (sans accents), remplace espaces par '_',
    tronque à 10 caractères et évite les collisions (contrainte DBF).
    """
    def ascii10(s: str) -> str:
        s_ascii = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
        s_ascii = s_ascii.replace(" ", "_")
        return s_ascii[:10] if len(s_ascii) > 10 else s_ascii

    seen = set()
    out = []
    for c in cols:
        base = ascii10(c) or "FIELD"
        name = base
        i = 1
        while name.upper() in seen:
            suffix = str(i)
            name = (base[: (10 - len(suffix))] + suffix)[:10]
            i += 1
        seen.add(name.upper())
        out.append(name)
    return out

def export_shapefile_zip(gdf: gpd.GeoDataFrame) -> bytes:
    """
    Produit un ZIP contenant .shp/.shx/.dbf/.prj/.cpg (UTF-8).
    Renomme automatiquement les colonnes non-geometry pour respecter le DBF.
    """
    if gdf is None or gdf.empty:
        gdf = gpd.GeoDataFrame(columns=["id_zone", "geometry"], geometry="geometry", crs="EPSG:4326")

    gdf = gdf.fillna("")

    # 1) Renommer les colonnes (max 10, ASCII), hors geometry
    non_geom = [c for c in gdf.columns if c != "geometry"]
    safe = _shp_field_names(non_geom)                 # <<< ICI on crée bien 'safe'
    ren = dict(zip(non_geom, safe))
    gdf = gdf.rename(columns=ren)

    # 2) Types DBF-friendly
    for c in gdf.columns:
        if c == "geometry":
            continue
        if pd.api.types.is_float_dtype(gdf[c]):
            gdf[c] = gdf[c].astype("float64")
        elif pd.api.types.is_integer_dtype(gdf[c]):
            gdf[c] = gdf[c].astype("int64")
        else:
            gdf[c] = gdf[c].astype(str)

    # 3) Écriture dans un dossier temporaire puis zippage
    bio = io.BytesIO()
    with tempfile.TemporaryDirectory() as tmp:
        folder = os.path.join(tmp, "shp")
        os.makedirs(folder, exist_ok=True)
        shp_path = os.path.join(folder, "export.shp")

        gdf.to_file(shp_path, driver="ESRI Shapefile", index=False, encoding="utf-8")

        # .cpg pour forcer UTF-8 sur les valeurs texte
        with open(os.path.join(folder, "export.cpg"), "w", encoding="utf-8") as cpg:
            cpg.write("UTF-8")

        with zipfile.ZipFile(bio, "w", compression=zipfile.ZIP_DEFLATED) as z:
            for fname in os.listdir(folder):
                z.write(os.path.join(folder, fname), arcname=fname)

    bio.seek(0)
    return bio.getvalue()

# ----------------- Export direct d'un GeoDataFrame -----------------
def export_gdf(fmt: str, gdf: gpd.GeoDataFrame, layer: str = "zada_fusion") -> bytes:
    """
    Exporte directement un GeoDataFrame dans le format demandé.
    Utilisable pour les résultats de fusion.
    
    Parameters
    ----------
    fmt : str
        Format d'export: 'geojson', 'gpkg', 'shp'
    gdf : gpd.GeoDataFrame
        GeoDataFrame à exporter
    layer : str
        Nom de la couche (pour GPKG)
    
    Returns
    -------
    bytes
        Fichier exporté sous forme de bytes
    """
    fmt = (fmt or "").lower()
    
    # Nettoyer le GeoDataFrame pour l'export
    if gdf is None or gdf.empty:
        # Créer un GeoDataFrame vide minimal si nécessaire
        gdf = gpd.GeoDataFrame(columns=["id_zone", "geometry"], geometry="geometry", crs="EPSG:4326")
    
    gdf = gdf.fillna("")
    
    # Utiliser les fonctions existantes
    if fmt == "geojson":
        return export_geojson_bytes(gdf)
    elif fmt == "gpkg":
        return export_gpkg_bytes(gdf, layer=layer)
    elif fmt == "shp":
        return export_shapefile_zip(gdf)
    else:
        raise ValueError("Format non supporté (utiliser: shp|gpkg|geojson)")



# ----------------- Orchestrateur -----------------
def export_from_results(
    fmt: str,
    corpus_gdf: gpd.GeoDataFrame,
    results_df: pd.DataFrame,
    layer: str = "zada_nlp"
) -> bytes:
    """
    fmt: 'geojson' | 'gpkg' | 'shp'
    """
    sel = build_selection_gdf(corpus_gdf, results_df)
    fmt = (fmt or "").lower()
    if fmt == "geojson":
        return export_geojson_bytes(sel)
    if fmt == "gpkg":
        return export_gpkg_bytes(sel, layer=layer)
    if fmt == "shp":
        return export_shapefile_zip(sel)
    raise ValueError("Format non supporté (utiliser: shp|gpkg|geojson)")
    
        
    
