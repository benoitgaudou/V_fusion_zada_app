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
      - Carte thématique par champ (catégoriel / discret, <= 500 modalités)
      - Légende simple
      - Bounds (pour ajuster la vue Leaflet)
    """

    # Nombre max de classes gérables en "simple" (sinon on renvoie une erreur)
    MAX_CLASSES = 500

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
        Génère un GeoJSON stylé + une légende pour un champ donné,
        en conservant toutes les valeurs valides et en excluant seulement:
        - NaN/None
        - (optionnel) une liste stricte de tokens invalides (ex. 'nsp').
        """
        try:
            if gdf is None or gdf.empty:
                return {"success": False, "error": "GeoDataFrame vide"}

            if field_name not in gdf.columns:
                return {"success": False, "error": f"Champ '{field_name}' introuvable"}

            series = gdf[field_name]

            # ---- 1) Filtre "valeur valide" minimaliste ----
            # Ne retire que NaN/None + tokens invalides EXACTS (optionnel)
            valid_mask = series.notna()

            # Tokens invalides exacts (normalisés en minuscules, sans espaces)
            # -> tu peux les piloter via self.placeholder_tokens si tu veux.
            invalid_tokens = set(getattr(self, "placeholder_tokens", {"nan"}))

            if pd.api.types.is_object_dtype(series):
                s_norm = series.astype(str).str.strip().str.lower()
                valid_mask &= ~s_norm.isin(invalid_tokens)
                # NE PAS exclure les chaînes vides si tu en as besoin ? Ici on les exclut:
                valid_mask &= s_norm.ne("")  # retire les blancs purs uniquement

            # ---- 2) Géométrie présente / non vide ----
            geom_mask = gdf.geometry.notna()
            try:
                geom_mask &= ~gdf.geometry.is_empty
            except Exception:
                pass
            # (Optionnel) pour exclure les géométries invalides topologiquement:
            # try:
            #     geom_mask &= gdf.geometry.is_valid
            # except Exception:
            #     pass

            # ---- 3) On ne conserve que les lignes réellement valides ----
            gdf_valid = gdf[valid_mask & geom_mask].copy()
            if gdf_valid.empty:
                return {
                    "success": False,
                    "error": f"Aucune entité valide pour '{field_name}' (valeur et/ou géométrie manquante)."
                }

            s_valid = gdf_valid[field_name]
            is_numeric = pd.api.types.is_numeric_dtype(s_valid)
            unique_vals = pd.Index(s_valid.unique())
            nunique = unique_vals.size

            # ---- 4) Garde-fous sur le nombre de classes ----
            if is_numeric and nunique > self.MAX_CLASSES:
                return {
                    "success": False,
                    "error": (
                        f"Le champ '{field_name}' contient trop de valeurs uniques ({nunique}). "
                        f"Choisissez ≤ {self.MAX_CLASSES} modalités ou activez un mode 'classes continues'."
                    ),
                }
            if (not is_numeric) and nunique > self.MAX_CLASSES:
                return {
                    "success": False,
                    "error": (
                        f"Trop de catégories pour '{field_name}' ({nunique}). "
                        f"Choisissez ≤ {self.MAX_CLASSES} catégories."
                    ),
                }

            # ---- 5) Palette & mapping ----
            palette = self.categorical_palettes.get(palette_name, self.categorical_palettes["default"])
            values_sorted = (
                pd.Series(unique_vals)
                .astype(str)
                .sort_values(key=lambda s: s.str.lower())
                .tolist()
            )
            color_map = {val: palette[i % len(palette)] for i, val in enumerate(values_sorted)}

            # ---- 6) Comptages (sur les valides conservées) ----
            value_counts = s_valid.astype(str).value_counts()

            # ---- 7) Styles (aucun "N/A": on n’exporte pas les nulls) ----
            gdf_valid["__thematic_value__"] = s_valid.astype(str)
            gdf_valid["__thematic_color__"] = gdf_valid["__thematic_value__"].map(color_map)

            # ---- 8) Projection WGS84 si besoin ----
            if gdf_valid.crs and gdf_valid.crs.to_string() != "EPSG:4326":
                gdf_wgs84 = gdf_valid.to_crs("EPSG:4326")
            else:
                gdf_wgs84 = gdf_valid

            # ---- 9) GeoJSON uniquement avec les features valides ----
            geojson = json.loads(gdf_wgs84.to_json())

            # ---- 10) Injection style + props ----
            for i, feat in enumerate(geojson.get("features", [])):
                row = gdf_valid.iloc[i]
                color = row["__thematic_color__"]
                value = row["__thematic_value__"]

                props = feat.setdefault("properties", {})
                props["style"] = {
                    "fillColor": color,
                    "color": color,
                    "fillOpacity": 0.7,
                    "weight": 2,
                    "opacity": 0.9,
                }
                props["thematic_field"] = field_name
                props["thematic_value"] = value

            # ---- 11) Légende (discrete) ----
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
                # utiles pour l'UI:
                "total_input": int(len(gdf)),
                "kept": int(len(gdf_valid)),
                "filtered_out": int(len(gdf) - len(gdf_valid)),
                "invalid_tokens": sorted(invalid_tokens),
            }

        except Exception as e:
            logger.exception("Erreur génération thématique: %s", e)
            return {"success": False, "error": f"Erreur génération carte: {e}"}

    # ------------------------------------------------------------------ #
    # (Optionnel) Pour aider l’UI à proposer des champs pertinents
    # ------------------------------------------------------------------ #*

    # Normalisation pour la comparaison :  trim, lowercase, espaces internes compactés
    def build_thematic_gdf(
        self,
        gdf: gpd.GeoDataFrame,
        field_name: str,
        palette_name: str = "default",
    ) -> tuple[gpd.GeoDataFrame, dict, Optional[list[list[float]]]]:
            """
            Construit un GDF prêt à l'export à partir d'un champ 'field_name'.
            Colonnes: id_zone (si présent), thematic_value, thematic_color, geometry (EPSG:4326).
            Retourne (gdf_export, legend, bounds).
            """
            res = self.generate_thematic_geojson(gdf, field_name=field_name, palette_name=palette_name)
            if not res.get("success"):
                raise ValueError(res.get("error") or "Génération thématique échouée.")

            # Reproduire le mapping valeur->couleur utilisé dans generate_thematic_geojson
            geojson = res["geojson"]
            legend = res["legend"]
            bounds = self.get_map_bounds(gdf)  # en WGS84

            # On repart du GDF original pour éviter la conversion JSON->GDF
            gdf_copy = gdf.copy()
            if gdf_copy.crs and gdf_copy.crs.to_string() != "EPSG:4326":
                gdf_copy = gdf_copy.to_crs("EPSG:4326")
            elif gdf_copy.crs is None:
                gdf_copy = gdf_copy.set_crs("EPSG:4326")

            # Refaire la logique value/color comme dans generate_thematic_geojson
            series = gdf[field_name]
            s_valid = series.dropna()
            unique_vals = s_valid.unique()
            palette = self.categorical_palettes.get(palette_name, self.categorical_palettes["default"])
            values_sorted = pd.Series(unique_vals).astype(str).sort_values(key=lambda s: s.str.lower()).tolist()
            color_map = {val: palette[i % len(palette)] for i, val in enumerate(values_sorted)}

            gdf_export = gdf_copy.copy()
            gdf_export["thematic_value"] = series.astype(str).where(series.notna(), other="N/A")
            gdf_export["thematic_color"] = gdf_export["thematic_value"].map(color_map).fillna("#808080")

            # Garder id_zone si présent
            cols = ["thematic_value", "thematic_color", "geometry"]
            if "id_zone" in gdf_export.columns:
                cols = ["id_zone"] + cols
                gdf_export["id_zone"] = gdf_export["id_zone"].astype(str)

            gdf_export = gdf_export[cols]
            return gdf_export, legend, bounds
    
    @staticmethod
    def _norm(name: str) -> str:
        
        return re.sub(r'\s+',' ', str(name).strip().lower())
    
    def prepare_criterion_options(self, gdf: gpd.GeoDataFrame) -> List[Dict]:
        if gdf is None or gdf.empty:
            return []

        excluded = {
            "geometry", "original_source_id", "original_source_name",
            "intersection_type", "source_pair", "source_names", "sources",
        }
        # normaliser excluded
        excluded_norm = {self._norm(x) for x in excluded}

        candidates = []
        for col in sorted([c for c in gdf.columns if self._norm(c) not in excluded_norm]):
            s = gdf[col]
            if s.notna().any():
                nunique = s.nunique(dropna=True)
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