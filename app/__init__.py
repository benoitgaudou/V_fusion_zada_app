# ============================================================================
# app/__init__.py - Initialisation de l'application Flask
# ============================================================================

from flask import Flask
from app.config import Config
import os

def create_app():
    """Factory pattern pour créer l'application Flask"""
    app = Flask(__name__)
    app.config.from_object(Config)
    
    # Création des dossiers nécessaires
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    os.makedirs(app.config['RESULTS_FOLDER'], exist_ok=True)
    
    # Enregistrement des routes
    from app.routes import main_bp
    app.register_blueprint(main_bp)
    
    print(app.url_map)
    
    return app


