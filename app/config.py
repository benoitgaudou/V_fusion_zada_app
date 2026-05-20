# ============================================================================
# app/config.py - Configuration de l'application
# ============================================================================
import os
from pathlib import Path

class Config:
    """Configuration de l'application ZADA"""

    # Flask
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'your-secret-key-here'
    
    NLP_BACKEND = "sentence_transformers"  # ou "sentence_transformers" selon choix par défaut

    # Dossiers
    BASE_DIR = Path(__file__).parent.parent
    UPLOAD_FOLDER = BASE_DIR / 'uploads'
    STAGE_FOLDER = BASE_DIR / 'stage_geojson'
    RESULTS_FOLDER = BASE_DIR / 'results'

    # Fichiers
    MAX_CONTENT_LENGTH = 100 * 1024 * 1024  # 100MB max
    # Aligné avec le FileLoader + formulaires (pas de .shp seul côté upload)
    ALLOWED_EXTENSIONS = {'.zip', '.geojson', '.json'}

    # ZADA
    DEFAULT_AREA_THRESHOLD = 100.0  # m²
    DEFAULT_CRS = "EPSG:4326"
    METRIC_CRS = "EPSG:3857"       # Web Mercator pour calculs métriques
    PROJ_NETWORK = False            # utile si besoin de l'activer

    # ZADA Merger
    ZADA_MERGER_CLASS = "default"  # clé pour choisir la classe de merger dans la factory

    # NLP (plus tard)
    NLP_MODEL_PATH = BASE_DIR / 'models'
    DEFAULT_SIMILARITY_THRESHOLD = 0.7

    @staticmethod
    def init_app(app):
        """Initialise les dossiers nécessaires"""
        Config.UPLOAD_FOLDER.mkdir(exist_ok=True, parents=True)
        Config.STAGE_FOLDER.mkdir(exist_ok=True, parents=True)   # ← ajouté
        Config.RESULTS_FOLDER.mkdir(exist_ok=True, parents=True)
        Config.NLP_MODEL_PATH.mkdir(exist_ok=True, parents=True)

        # Log de configuration
        print(" Dossiers configurés:")
        print(f"   Upload:  {Config.UPLOAD_FOLDER}")
        print(f"   Stage:   {Config.STAGE_FOLDER}")
        print(f"   Results: {Config.RESULTS_FOLDER}")
        print(f"   Models:  {Config.NLP_MODEL_PATH}")


class DevelopmentConfig(Config):
    DEBUG = True
    TESTING = False


class ProductionConfig(Config):
    DEBUG = False
    TESTING = False


class TestingConfig(Config):
    DEBUG = True
    TESTING = True
    WTF_CSRF_ENABLED = False


config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'default': DevelopmentConfig
}
