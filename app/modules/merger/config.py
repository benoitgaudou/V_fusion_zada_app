from dataclasses import dataclass

@dataclass(frozen=True)
class MergeConfig:
    """
    Paramètres de fusion ZADA.

    Attributes
    ----------
    area_threshold_m2 : float
        Seuil de surface (m²) pour supprimer les micro-polygones (0 pour désactiver).
    input_crs_fallback : str
        CRS assumé si un fichier n'a pas de CRS (par défaut WGS84).
    output_crs : str
        CRS de sortie (par défaut WGS84).
    metric_crs : str
        CRS métrique temporaire pour les calculs de surface.
    sample_unique_values : int
        Taille d'échantillon max par colonne pour l'analyse sémantique légère.
    similarity_threshold : float
        Seuil de chevauchement moyen (Jaccard) au‑delà duquel une colonne est dite
        “commune” (sinon “conflictuelle”).
    """
    area_threshold_m2: float = 5.0
    input_crs_fallback: str = "EPSG:4326"
    output_crs: str = "EPSG:4326"
    metric_crs: str = "EPSG:3857"
    sample_unique_values: int = 10
    similarity_threshold: float = 0.30
