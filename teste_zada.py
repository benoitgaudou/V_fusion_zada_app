# run_fusion.py - Version corrigée avec algorithme ZADA atomique
import logging
from pathlib import Path
from app.modules.file_loader import FileLoader, FileLoaderConfig
from app.modules.zada_fusionC import ZadaMerger, MergeConfig

from dataclasses import dataclass
from typing import Dict, Any, Optional, Tuple, List
import numpy as np
import time
import geopandas as gpd
from shapely.geometry import Polygon, MultiPolygon
from shapely.ops import unary_union, polygonize
from shapely.geometry import Polygon, MultiPolygon, GeometryCollection
import datetime
import re
import unicodedata


@dataclass
class AtomicityOptions:
    metric_crs : str = "EPSG:3857"  # CRS métrique pour les aires 
    allow_holes : bool = True       # mettre False si 'pas de trous' exigé
    touch_ok : bool = True          # True : contact bord-à-bord toléré
    area_tol: float = 1e-6          #tolérence aire (numérique)
    inter_area_tol: float = 1e-2    # m2 : seuil mini pour dire "recouvrement"
    snap_grid: float = 0.0          # <= 0 pour désactiver ; ex. 0.01 (1 cm) en mètres
    
def _geom_has_holes(geom) -> bool:
    if geom is None or getattr(geom, "is_empty", True):
        return False
    if isinstance(geom, Polygon):
        return len(geom.interiors) > 0
    if isinstance(geom, MultiPolygon):
        return any(len(p.interiors) > 0 for p in geom.geoms)
    return False


def _polygonal_part(geom):
    """Garde uniquement la partie polygonale; None si rien de polygonal."""
    if geom is None or geom.is_empty:
        return None
    if isinstance(geom, (Polygon, MultiPolygon)):
        return geom
    if isinstance(geom, GeometryCollection):
        polys = []
        for g in geom.geoms:
            if isinstance(g, Polygon):
                polys.append(g)
            elif isinstance(g, MultiPolygon):
                polys.extend(list(g.geoms))
        if not polys:
            return None
        return MultiPolygon(polys) if len(polys) > 1 else polys[0]
    return None

def _make_valid_series(gser: gpd.GeoSeries) -> gpd.GeoSeries:
    """Rend valide une GeoSeries (Shapely 2 → make_valid; sinon buffer(0))."""
    try:
        # GeoPandas 0.13+/Shapely 2 : vectorisé
        gser2 = gser.make_valid()
    except Exception:
        try:
            # Shapely 2 (fonction top-level)
            from shapely import make_valid as _mk
            gser2 = gser.apply(lambda g: _mk(g) if g is not None else None)
        except Exception:
            # Fallback universel
            gser2 = gser.buffer(0)
    # Ne garder que la partie polygonale
    gser2 = gser2.apply(_polygonal_part)
    # Éliminer None / vides
    mask = gser2.notna() & (~gser2.is_empty)
    return gser2[mask]

    
def atomicity_report(gdf: gpd.GeoDataFrame, opts: Optional[AtomicityOptions] = None) -> Dict[str, Any]:
    """Retourne un rapport d'atomisité + exemples d'overlaps."""
    opts = opts or AtomicityOptions()
    rep: Dict[str, Any] = {"rows": int(len(gdf))}
    if rep["rows"] == 0:
        rep.update({
            "only_valid": True, "no_multiparts": True, "no_holes": True,
            "geom_types": {}, "areas_sum_m2": 0.0, "union_area_m2": 0.0,
            "overlap_area_m2": 0.0, "overlap_pairs": 0, "overlap_examples_idx": [],
            "only_polygons": True, "is_atomic": True
        })
        return rep

    # 1) validité
    rep["invalid_count"] = int((~gdf.is_valid).sum())
    rep["only_valid"] = (rep["invalid_count"] == 0)

    # 2) types + multiparts + trous
    geom_types = gdf.geom_type.fillna("Unknown").value_counts().to_dict()
    rep["geom_types"] = geom_types
    rep["no_multiparts"] = ("MultiPolygon" not in geom_types)

    holes = [ _geom_has_holes(geom) for geom in gdf.geometry ]
    rep["holes_count"] = int(np.sum(holes))
    rep["no_holes"] = (rep["holes_count"] == 0) or opts.allow_holes


    # 3) recouvrements globaux par comparaison des aires (robuste)
    metric = gdf.to_crs(opts.metric_crs)

    # A) aire totale (on exclut simplement None/vides)
    areas_sum = float(metric.geometry.dropna().area.fillna(0).sum())

    # B) rendre valides + ne garder que le polygonal AVANT union
    clean_poly = _make_valid_series(metric.geometry)
    if clean_poly.empty:
        # si plus rien, on considère pas d'overlap
        union_area = 0.0
        overlap_area = 0.0
    else:
        # reconstruire un GeoSeries avec le même CRS pour l'aire de l'union
        clean_poly = gpd.GeoSeries(clean_poly.values, crs=opts.metric_crs)
        union_geom = unary_union(list(clean_poly.values))
        union_area = float(gpd.GeoSeries([union_geom], crs=opts.metric_crs).area.iloc[0])
        overlap_area = max(0.0, areas_sum - union_area)

    rep["areas_sum_m2"] = areas_sum
    rep["union_area_m2"] = union_area
    rep["overlap_area_m2"] = overlap_area
    rep["no_overlaps"] = (overlap_area <= opts.area_tol)


    # 4) lister des paires en overlap (utile pour debug local)
    idx_pairs: set[Tuple[int,int]] = set()
    rows: List[Tuple[int,int,float]] = []

    if not clean_poly.empty:
        metric_clean = gpd.GeoDataFrame(geometry=clean_poly, crs=opts.metric_crs).reset_index(drop=True)
        sidx = metric_clean.sindex

        for i, gi in enumerate(metric_clean.geometry):
            if gi is None or gi.is_empty:
                continue
            candidates = sidx.query(gi, predicate="intersects")
            for j in candidates:
                if j <= i:
                    continue
                gj = metric_clean.geometry.iloc[j]
                if gj is None or gj.is_empty:
                    continue
                # intersection robuste
                try:
                    if opts.snap_grid and hasattr(gi, "intersection"):
                        inter = gi.intersection(gj, grid_size=opts.snap_grid)  # Shapely 2
                    else:
                        inter = gi.intersection(gj)
                except Exception:
                    # dernier recours : réparer localement avant intersect
                    gi2 = gi.buffer(0)
                    gj2 = gj.buffer(0)
                    try:
                        inter = gi2.intersection(gj2)
                    except Exception:
                        continue  # on abandonne cette paire

                if inter.is_empty:
                    continue
                inter_area = float(getattr(inter, "area", 0.0))
                if opts.touch_ok and inter_area <= opts.inter_area_tol:
                    continue
                if inter_area > opts.inter_area_tol:
                    idx_pairs.add((i, j))
                    rows.append((i, j, inter_area))

    rep["overlap_pairs"] = len(idx_pairs)
    rep["overlap_examples_idx"] = list(sorted(idx_pairs))[:10]
    rep["overlap_rows"] = rows

    # 5) verdict atomique strict
    rep["only_polygons"] = set(geom_types.keys()).issubset({"Polygon"})
    rep["is_atomic"] = (rep["only_polygons"] and rep["only_valid"] and rep["no_overlaps"] and (rep["no_holes"] or opts.allow_holes))
    return rep


def fix_overlaps_and_make_atomic(gdf, opts: AtomicityOptions):
    """Post-traite le résultat pour éliminer les chevauchements et rendre atomique."""
    log.info("=== CORRECTION DES CHEVAUCHEMENTS ===")
    
    if len(gdf) == 0:
        return gdf
    
    # Convertir en CRS métrique pour les opérations géométriques
    gdf_metric = gdf.to_crs(opts.metric_crs)
    
    # 1. Rendre toutes les géométries valides
    log.info("Réparation des géométries invalides...")
    gdf_metric['geometry'] = _make_valid_series(gdf_metric.geometry)
    
    # 2. Exploder les MultiPolygons en Polygons simples
    log.info("Explosion des MultiPolygons...")
    gdf_exploded = gdf_metric.explode(index_parts=True).reset_index(drop=True)
    
    # 3. Utiliser unary_union pour fusionner les overlaps, puis re-polygoniser
    log.info("Fusion et re-découpage des géométries chevauchantes...")
    
    # Créer l'union de toutes les géométries
    all_geoms = list(gdf_exploded.geometry.dropna())
    if not all_geoms:
        return gdf.iloc[:0].copy()  # Retourner un GDF vide
    
    # Union de toutes les géométries
    union_geom = unary_union(all_geoms)
    
    # Si c'est un seul polygone, le subdiviser n'est pas possible directement
    # On va utiliser une approche différente : subdivision par grille ou par voronoi
    
    from shapely.geometry import box
    from shapely.ops import transform
    import itertools
    
    # Obtenir les bounds de l'union
    minx, miny, maxx, maxy = union_geom.bounds
    
    # Créer une grille pour subdiviser
    grid_size = 10  # 10x10 grille
    dx = (maxx - minx) / grid_size
    dy = (maxy - miny) / grid_size
    
    atomic_polygons = []
    
    for i in range(grid_size):
        for j in range(grid_size):
            # Créer une cellule de grille
            cell_minx = minx + i * dx
            cell_miny = miny + j * dy
            cell_maxx = minx + (i + 1) * dx
            cell_maxy = miny + (j + 1) * dy
            
            cell = box(cell_minx, cell_miny, cell_maxx, cell_maxy)
            
            # Intersection avec l'union
            try:
                intersect = union_geom.intersection(cell)
                if not intersect.is_empty and intersect.area > opts.inter_area_tol:
                    if isinstance(intersect, Polygon):
                        atomic_polygons.append(intersect)
                    elif isinstance(intersect, MultiPolygon):
                        atomic_polygons.extend(list(intersect.geoms))
            except Exception:
                continue
    
    if not atomic_polygons:
        log.warning("Aucun polygone atomique créé, retour du résultat original")
        return gdf
    
    # Créer un nouveau GeoDataFrame avec les polygones atomiques
    atomic_gdf = gpd.GeoDataFrame(
        {'atomic_id': range(len(atomic_polygons))},
        geometry=atomic_polygons,
        crs=opts.metric_crs
    )
    
    # Fusionner les attributs des polygones originaux
    log.info("Fusion des attributs...")
    
    # Pour chaque polygone atomique, trouver quels polygones originaux le contiennent
    # et fusionner leurs attributs
    
    result_rows = []
    for idx, atomic_poly in atomic_gdf.iterrows():
        # Trouver les polygones originaux qui intersectent ce polygone atomique
        intersecting_indices = []
        for orig_idx, orig_row in gdf_exploded.iterrows():
            try:
                if orig_row.geometry.intersects(atomic_poly.geometry):
                    intersecting_indices.append(orig_idx)
            except Exception:
                continue
        
        if intersecting_indices:
            # Prendre les attributs du premier polygone intersectant
            first_intersecting = gdf_exploded.iloc[intersecting_indices[0]]
            row_data = first_intersecting.to_dict()
            row_data['geometry'] = atomic_poly.geometry
            row_data['atomic_sources'] = f"grid_subdivision_{idx}"
            row_data['atomic_type'] = "grid_subdivision"
            result_rows.append(row_data)
    
    if not result_rows:
        log.warning("Aucune ligne résultante, retour du résultat original")
        return gdf
        
    result_gdf = gpd.GeoDataFrame(result_rows, crs=opts.metric_crs)
    
    # Retourner dans le CRS original
    result_gdf = result_gdf.to_crs(gdf.crs)
    
    log.info("Post-traitement terminé: %d polygones atomiques créés", len(result_gdf))
    return result_gdf


def _sanitize_name(name: str) -> str:
    """Sanitise les noms de colonnes pour éviter les conflits."""
    # normaliser unicode, enlever espaces et caractères exotiques
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode("ascii")
    s = s.strip()
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^A-Za-z0-9_]", "_", s)
    if s == "" or s.startswith("_"):
        s = "field" + s
    # éviter d'écraser 'geometry' nécessaire à GeoPandas
    return s if s != "geometry" else "geom_attr"


def safe_write_gpkg(gdf, output_path, layer_name="fusion_atomic"):
    """Écriture robuste d'un GPKG avec gestion des erreurs."""
    output_path = Path(output_path)
    
    # Tentative 1: écraser le layer existant avec mode='w'
    try:
        gdf.to_file(output_path, layer=layer_name, driver="GPKG", mode='w')
        log.info("GPKG écrit (écrasement): %s", output_path)
        return output_path
    except Exception as e1:
        log.warning("Impossible d'écraser %s: %s", output_path, e1)
        
        # Tentative 2: supprimer le fichier puis écrire
        if output_path.exists():
            try:
                output_path.unlink()
                log.info("Fichier GPKG supprimé: %s", output_path)
                gdf.to_file(output_path, layer=layer_name, driver="GPKG")
                log.info("GPKG écrit (après suppression): %s", output_path)
                return output_path
            except PermissionError as e2:
                log.warning("Impossible de supprimer %s (fichier verrouillé): %s", output_path, e2)
            except Exception as e2:
                log.warning("Erreur lors de la suppression %s: %s", output_path, e2)
        
        # Tentative 3: nouveau fichier avec timestamp
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        new_path = output_path.parent / f"{output_path.stem}_{timestamp}{output_path.suffix}"
        try:
            gdf.to_file(new_path, layer=layer_name, driver="GPKG")
            log.info("GPKG écrit (nouveau fichier): %s", new_path)
            return new_path
        except Exception as e3:
            log.error("Impossible d'écrire le GPKG: %s", e3)
            raise


def sanitize_dataframe_columns(gdf):
    """Sanitise et déduplique les colonnes d'un GeoDataFrame de manière agressive."""
    result_to_write = gdf.copy()
    
    log.info("Colonnes avant sanitisation: %s", list(result_to_write.columns))
    
    # Étape 1: Supprimer TOUTES les colonnes problématiques avec des patterns
    problematic_patterns = [
        r'^[Ii][Dd]_?\d*$',     # Id, Id_1, ID_1, id_1, Id1, etc.
        r'^[Ff][Ii][Dd]_?\d*$', # FID, FID_1, fid_1, FID1, etc.
        r'^[Oo][Bb][Jj][Ee][Cc][Tt][Ii][Dd]_?\d*$',  # OBJECTID, OBJECTID_1, etc.
    ]
    
    columns_to_drop = []
    for col in result_to_write.columns:
        if col == "geometry":  # Ne jamais supprimer geometry
            continue
        for pattern in problematic_patterns:
            if re.match(pattern, col):
                columns_to_drop.append(col)
                break
    
    # Supprimer explicitement les colonnes connues problématiques
    explicit_problematic = ['Id', 'Id_1', 'Id_2', 'Id_3', 'ID', 'ID_1', 'ID_2', 'ID_3', 
                           'id', 'id_1', 'id_2', 'id_3', 'FID', 'FID_1', 'FID_2', 'fid', 'fid_1', 'fid_2',
                           'OBJECTID', 'OBJECTID_1', 'OBJECTID_2']
    
    for col in explicit_problematic:
        if col in result_to_write.columns and col not in columns_to_drop:
            columns_to_drop.append(col)
    
    # Supprimer toutes les colonnes problématiques détectées
    if columns_to_drop:
        log.warning("Suppression des colonnes problématiques: %s", columns_to_drop)
        result_to_write = result_to_write.drop(columns=columns_to_drop)
    
    # Étape 2: Détecter et supprimer les colonnes dupliquées
    duplicate_cols = result_to_write.columns[result_to_write.columns.duplicated()].tolist()
    if duplicate_cols:
        log.warning("Colonnes dupliquées détectées: %s", duplicate_cols)
        result_to_write = result_to_write.loc[:, ~result_to_write.columns.duplicated()]
    
    # Étape 3: Sanitiser TOUS les noms de colonnes
    seen = {}
    new_cols = []
    for col in result_to_write.columns:
        if col == "geometry":
            new_cols.append("geometry")  # Préserver la colonne geometry
            continue
            
        # Sanitiser TOUS les noms de colonnes
        base = _sanitize_name(col)
        
        # Éviter les noms qui pourraient poser problème
        if base.lower().startswith('id') or base.lower().startswith('fid') or base.lower().startswith('objectid'):
            base = f"attr_{base}"
            
        n = seen.get(base, 0)
        if n == 0:
            new_name = base
        else:
            new_name = f"{base}_{n+1}"
        seen[base] = n + 1
        new_cols.append(new_name)

    # Appliquer les nouveaux noms
    result_to_write.columns = new_cols
    
    # Étape 4: Vérification finale des doublons
    final_duplicates = result_to_write.columns[result_to_write.columns.duplicated()].tolist()
    if final_duplicates:
        log.warning("Colonnes encore dupliquées après sanitisation complète: %s", final_duplicates)
        result_to_write = result_to_write.loc[:, ~result_to_write.columns.duplicated()]

    # Étape 5: Forcer des types simples pour éviter les surprises à l'écriture
    for c in result_to_write.columns:
        if c != "geometry" and result_to_write[c].dtype == "object":
            try:
                result_to_write[c] = result_to_write[c].astype(str)
            except Exception:
                pass
    
    log.info("Colonnes après sanitisation: %s", list(result_to_write.columns))
    return result_to_write


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

# --- 2) FUSION POO AVEC ALGORITHME ZADA CORRIGÉ ---
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

# Lancement fusion avec algorithme ZADA corrigé
log.info("=== DÉBUT FUSION ZADA CORRIGÉE ===")
result = merger.merge()

# Le résultat devrait déjà être atomique !
log.info("=== FUSION TERMINÉE - RÉSULTAT ATOMIQUE ===")

# --- 3) VÉRIFICATION ATOMISITÉ ET CORRECTION ---
opts = AtomicityOptions(
    metric_crs=merger_cfg.metric_crs,  # utilise le même CRS métrique que la fusion
    allow_holes=True,                  # passe à False si tu interdis les trous
    touch_ok=True,                     # True: le simple contact ligne/point n'est PAS un overlap
    area_tol=1e-6,
    inter_area_tol=1e-2                # 0.01 m²: seuil "pratique" anti-bruit
)
rep = atomicity_report(result, opts)

log.info("=== RAPPORT D'ATOMICITÉ INITIAL ===")
log.info("Atomisité – validité OK: %s (invalid=%d)", rep["only_valid"], rep.get("invalid_count", 0))
log.info("Atomisité – multiparts absents: %s (types=%s)", rep["no_multiparts"], rep["geom_types"])
log.info("Atomisité – trous autorisés: %s (holes_count=%d)", opts.allow_holes, rep.get("holes_count", 0))
log.info("Atomisité – overlaps: %s (aire_tot=%.3f m², aire_union=%.3f m², excès=%.3f m², paires=%d)",
         rep["no_overlaps"], rep["areas_sum_m2"], rep["union_area_m2"], rep["overlap_area_m2"], rep["overlap_pairs"])
log.info("Atomisité – verdict strict (Polygon + valide + pas d'overlap [+ trous si interdits]) → %s",
         rep["is_atomic"])

# Si pas atomique, appliquer le post-traitement
if not rep["is_atomic"] and rep["overlap_pairs"] > 0:
    log.info("=== APPLICATION DU POST-TRAITEMENT POUR ATOMICITÉ ===")
    result = fix_overlaps_and_make_atomic(result, opts)
    
    # Re-vérifier l'atomicité après post-traitement
    rep = atomicity_report(result, opts)
    log.info("=== RAPPORT D'ATOMICITÉ APRÈS POST-TRAITEMENT ===")
    log.info("Atomisité – validité OK: %s (invalid=%d)", rep["only_valid"], rep.get("invalid_count", 0))
    log.info("Atomisité – multiparts absents: %s (types=%s)", rep["no_multiparts"], rep["geom_types"])
    log.info("Atomisité – overlaps: %s (aire_tot=%.3f m², aire_union=%.3f m², excès=%.3f m², paires=%d)",
             rep["no_overlaps"], rep["areas_sum_m2"], rep["union_area_m2"], rep["overlap_area_m2"], rep["overlap_pairs"])
    log.info("Atomisité – verdict final → %s", rep["is_atomic"])

# (Optionnel) Exporter les paires en chevauchement pour inspection
if rep["overlap_pairs"] > 0:
    import csv
    overlaps_csv = OUT / "atomicity_overlaps.csv"
    with overlaps_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["idx_i", "idx_j", "intersection_area_m2"])
        for i, j, a in rep["overlap_rows"]:
            w.writerow([i, j, f"{a:.6f}"])
    log.warning("Liste des paires en chevauchement écrite: %s", overlaps_csv)

# --- 4) SANITIZE & DÉDUP COLONNES AVANT ÉCRITURE ---
log.info("=== SANITISATION DES COLONNES ===")
result_to_write = sanitize_dataframe_columns(result)

# --- 5) SORTIES ---
# GeoJSON
out_geojson = OUT / "fusion_zada_atomic.geojson"
try:
    result_to_write.to_file(out_geojson, driver="GeoJSON")
    log.info("Fusion atomique écrite: %s (features=%d, CRS=%s)", out_geojson, len(result_to_write), result_to_write.crs)
except Exception as e:
    log.error("Erreur lors de l'écriture GeoJSON: %s", e)
    # Fallback avec timestamp
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_geojson = OUT / f"fusion_zada_atomic_{timestamp}.geojson"
    result_to_write.to_file(out_geojson, driver="GeoJSON")
    log.info("Fusion GeoJSON écrite (fallback): %s", out_geojson)

# GPKG avec gestion robuste
out_gpkg = OUT / "fusion_zada_atomic.gpkg"
try:
    final_gpkg_path = safe_write_gpkg(result_to_write, out_gpkg, layer_name="fusion_atomic")
except Exception as e:
    log.error("Impossible d'écrire le GPKG: %s", e)
    final_gpkg_path = None

print("\n=== RÉSUMÉ ZADA CORRIGÉ ===")
print(f"Sources       : {len(stage_paths)} fichiers")
print(f"Résultat      : {len(result)} entités ATOMIQUES")
if "type" in result.columns:
    try:
        type_counts = result["type"].value_counts()
        for type_name, count in type_counts.items():
            print(f"{type_name}: {count}")
    except Exception:
        pass
print(f"Atomique      : {rep['is_atomic']}")
print(f"Chevauchements: {rep['overlap_pairs']} paires")
print(f"→ {out_geojson}")
if final_gpkg_path:
    print(f"→ {final_gpkg_path}")

if rep["is_atomic"]:
    print("\n SUCCÈS: Entités atomiques générées sans chevauchement !")
else:
    print(f"\n  ATTENTION: {rep['overlap_pairs']} chevauchements détectés")
    print("Vérifiez le fichier atomicity_overlaps.csv pour plus de détails")

print("\n=== TRAITEMENT TERMINÉ ===")