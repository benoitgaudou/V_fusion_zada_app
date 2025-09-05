# ============================================================================
# app/modules/map_generator.py - Version minimaliste (thématique par champ)
# ============================================================================

from __future__ import annotations
import json
import logging
from typing import Dict, List, Optional

import geopandas as gpd
import pandas as pd
import re

logger = logging.getLogger(__name__)


class MapDataGenerator:
    """
    Générateur minimal pour :
      - Carte thématique par champ (catégoriel / discret, <= 20 modalités)
      - Légende simple
      - Bounds (pour ajuster la vue Leaflet)
    """

    # Nombre max de classes gérables en "simple" (sinon on renvoie une erreur)
    MAX_CLASSES = 170

    def __init__(self):
        # Palettes catégorielles simples (on cycle si plus de valeurs que de couleurs)
        self.categorical_palettes = {
            "default": ['#ffffcc','#c7e9b4','#7fcdbb','#41b6c4','#1d91c0','#225ea8','#0c2c84'],
            "pastel":  ['#d73027','#fc8d59','#fee08b','#ffffbf','#d9ef8b','#91cf60','#1a9850'],
            "vibrant": ['#e41a1c','#377eb8','#4daf4a','#984ea3','#ff7f00','#ffff33','#a65628'],
            "earth":   ['#edf8fb','#ccece6','#99d8c9','#66c2a4','#41ae76','#238b45','#005824'],
        }

    # ------------------------------------------------------------------ #
    # Bounds pour Leaflet
    # ------------------------------------------------------------------ #
    def get_map_bounds(self, gdf: gpd.GeoDataFrame) -> Optional[List[List[float]]]:
        """
        Retourne [[lat_min, lng_min], [lat_max, lng_max]] pour Leaflet
        """
        if gdf is None or gdf.empty:
            return None

        try:
            gdf_wgs84 = gdf.to_crs("EPSG:4326") if gdf.crs and gdf.crs.to_string() != "EPSG:4326" else gdf
            minx, miny, maxx, maxy = gdf_wgs84.total_bounds
            return [[miny, minx], [maxy, maxx]]
        except Exception as e:
            logger.error("Erreur calcul bounds: %s", e)
            return None

    # ------------------------------------------------------------------ #
    # Carte thématique par champ (catégories / discret)
    # ------------------------------------------------------------------ #
    def generate_thematic_geojson(
        self,
        gdf: gpd.GeoDataFrame,
        field_name: str,
        palette_name: str = "default",
    ) -> Dict:
        """
        Génère un GeoJSON stylé + une légende pour un champ donné.
        - Supporte les champs 'catégoriels' (dtype object) ou 'discrets' (numériques avec peu de valeurs).
        - Si > MAX_CLASSES modalités, renvoie une erreur (on ajoutera les classes continues plus tard).

        Retour:
            {
              "success": True/False,
              "geojson": {...},               # FeatureCollection stylée
              "legend": {"type":"discrete","items":[{"label","color","count"}...]},
              "field_name": str,
              "palette_name": str,
              "unique_count": int,
              "error": str (si échec)
            }
        """
        try:
            if gdf is None or gdf.empty:
                return {"success": False, "error": "GeoDataFrame vide"}

            if field_name not in gdf.columns:
                return {"success": False, "error": f"Champ '{field_name}' introuvable"}

            series = gdf[field_name]
            # valeurs non nulles
            s_valid = series.dropna()

            if s_valid.empty:
                return {"success": False, "error": f"Le champ '{field_name}' ne contient aucune valeur valide"}

            # Détection "simple" : catégoriel OU numérique mais peu de valeurs
            is_numeric = pd.api.types.is_numeric_dtype(s_valid)
            unique_vals = s_valid.unique()

            # Si numérique avec trop de classes → on refuse pour l'instant (pas de classes continues dans cette version)
            if is_numeric and s_valid.nunique() > self.MAX_CLASSES:
                return {
                    "success": False,
                    "error": (
                        f"Le champ '{field_name}' contient trop de valeurs uniques ({s_valid.nunique()}). "
                        f"Choisissez un champ avec ≤ {self.MAX_CLASSES} modalités ou attendez la version 'classes continues'."
                    ),
                }

            # Si non numérique mais trop de catégories → idem
            if (not is_numeric) and len(unique_vals) > self.MAX_CLASSES:
                return {
                    "success": False,
                    "error": (
                        f"Trop de catégories pour '{field_name}' ({len(unique_vals)}). "
                        f"Choisissez un champ avec ≤ {self.MAX_CLASSES} catégories."
                    ),
                }

            # Palette
            palette = self.categorical_palettes.get(palette_name, self.categorical_palettes["default"])

            # Mapping valeur → couleur (on stringifie pour éviter les soucis de clés numpy types)
            values_sorted = pd.Series(unique_vals).astype(str).sort_values(key=lambda s: s.str.lower()).tolist()
            color_map = {val: palette[i % len(palette)] for i, val in enumerate(values_sorted)}

            # Comptages pour la légende
            value_counts = s_valid.astype(str).value_counts()

            # Appliquer le style (copie du gdf)
            gdf_styled = gdf.copy()
            gdf_styled["__thematic_value__"] = series.astype(str).where(series.notna(), other="N/A")
            gdf_styled["__thematic_color__"] = gdf_styled["__thematic_value__"].map(color_map).fillna("#808080")

            # Conversion WGS84 pour Leaflet si nécessaire
            gdf_wgs84 = gdf_styled.to_crs("EPSG:4326") if gdf_styled.crs and gdf_styled.crs.to_string() != "EPSG:4326" else gdf_styled

            # Création GeoJSON
            geojson = json.loads(gdf_wgs84.to_json())

            # Injecter style + propriétés thématiques
            # on assume l'ordre aligné (to_json conserve l'ordre des lignes)
            for i, feat in enumerate(geojson.get("features", [])):
                # récupére la ligne correspondante (même index i)
                row = gdf_styled.iloc[i]
                color = row["__thematic_color__"]
                value = row["__thematic_value__"]

                props = feat.setdefault("properties", {})
                # style Leaflet
                props["style"] = {
                    "fillColor": color,
                    "color": color,
                    "fillOpacity": 0.7,
                    "weight": 2,
                    "opacity": 0.9,
                }
                props["thematic_field"] = field_name
                props["thematic_value"] = value

            # Légende simple (discrete)
            legend_items = [
                {"label": k, "color": color_map.get(k, "#808080"), "count": int(value_counts.get(k, 0))}
                for k in values_sorted
            ]
            legend = {"type": "discrete", "items": legend_items}

            return {
                "success": True,
                "geojson": geojson,
                "legend": legend,
                "field_name": field_name,
                "palette_name": palette_name,
                "unique_count": int(len(values_sorted)),
            }

        except Exception as e:
            logger.exception("Erreur génération thématique: %s", e)
            return {"success": False, "error": f"Erreur génération carte: {e}"}

    # ------------------------------------------------------------------ #
    # (Optionnel) Pour aider l’UI à proposer des champs pertinents
    # ------------------------------------------------------------------ #*
    
    
    # les correctifs de cette fonction 
    # Normalisation pour la comparaison :  trim, lowercase, espaces internes compactés
    
    def _norm(name: str) -> str:
        
        return re.sub(r'\s+',' ', str(name).strip().lower())
    
    def prepare_criterion_options(self, gdf: gpd.GeoDataFrame) -> List[Dict]:
        """
        Retourne des champs candidats (catégoriels / discrets) pour l’UI.
        On filtre les colonnes techniques évidentes.
        """
        if gdf is None or gdf.empty:
            return []

        excluded = {
            "geometry", "Original_source_id", "Original_source_name",
            "intersection_type", "source_pair", "source_names", "sources",
        }

        candidates = []
        for col in sorted([c for c in gdf.columns if c not in excluded]):
            s = gdf[col]
            if s.notna().any():
                nunique = s.nunique(dropna=True)
                # critère "simple": dtype object OU numérique avec peu de classes
                if s.dtype == "object" or nunique <= self.MAX_CLASSES:
                    sample = [str(v) for v in s.dropna().unique()[:5].tolist()]
                    candidates.append({
                        "name": col,
                        "label": col.replace("_", " ").title(),
                        "unique_count": int(nunique),
                        "sample_values": sample,
                    })
        return candidates


# ----------------------------------------------------------------------------
# Alias de compatibilité : certaines routes utilisent encore ThematicMapGenerator
# ----------------------------------------------------------------------------
class ThematicMapGenerator(MapDataGenerator):
    """
    Alias pour compatibilité. On garde la même API minimale:
    - generate_thematic_geojson(...)
    - get_map_bounds(...)
    """
    pass