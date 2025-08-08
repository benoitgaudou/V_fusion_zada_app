# ============================================================================
# app/config.py - Configuration de l'application
# ============================================================================

import os
from pathlib import Path

class Config:
    """Configuration de l'application ZADA"""
    
    # Configuration Flask
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'your-secret-key-here'
    
    # Dossiers de travail
    BASE_DIR = Path(__file__).parent.parent
    UPLOAD_FOLDER = BASE_DIR / 'uploads'
    RESULTS_FOLDER = BASE_DIR / 'results'
    
    # Configuration des fichiers
    MAX_CONTENT_LENGTH = 100 * 1024 * 1024  # 100MB max
    ALLOWED_EXTENSIONS = {'.shp', '.shx', '.dbf', '.prj', '.geojson'}
    
    # Configuration ZADA
    DEFAULT_AREA_THRESHOLD = 100  # m²
    DEFAULT_CRS = "EPSG:4326"
    METRIC_CRS = "EPSG:3857"  # Web Mercator pour calculs métriques
    
    # Configuration NLP (pour plus tard)
    NLP_MODEL_PATH = BASE_DIR / 'models'
    DEFAULT_SIMILARITY_THRESHOLD = 0.7
    
    @staticmethod
    def init_app(app):
        """Initialise les dossiers nécessaires"""
        # Créer les dossiers s'ils n'existent pas
        Config.UPLOAD_FOLDER.mkdir(exist_ok=True, parents=True)
        Config.RESULTS_FOLDER.mkdir(exist_ok=True, parents=True)
        Config.NLP_MODEL_PATH.mkdir(exist_ok=True, parents=True)
        
        # Log de configuration
        print(f" Dossiers configurés:")
        print(f"   Upload: {Config.UPLOAD_FOLDER}")
        print(f"   Results: {Config.RESULTS_FOLDER}")
        print(f"   Models: {Config.NLP_MODEL_PATH}")


class DevelopmentConfig(Config):
    """Configuration pour développement"""
    DEBUG = True
    TESTING = False


class ProductionConfig(Config):
    """Configuration pour production"""
    DEBUG = False
    TESTING = False


class TestingConfig(Config):
    """Configuration pour tests"""
    DEBUG = True
    TESTING = True
    WTF_CSRF_ENABLED = False


# Configuration par défaut
config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'default': DevelopmentConfig
}