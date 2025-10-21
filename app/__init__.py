# ============================================================================
# app/__init__.py - Initialisation de l'application Flask
# ============================================================================

from flask import Flask
from app.config import config
import os

def create_app(config_name='default'):
    """Factory pattern pour créer l'application Flask avec config dynamique"""
    
    app = Flask(__name__)
    app.config.from_object(config[config_name])  # ← utiliser le dict 'config'
    
    # Création des dossiers nécessaires
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    os.makedirs(app.config['RESULTS_FOLDER'], exist_ok=True)
    
    # Enregistrement des routes
    from app.routes import main_bp
    app.register_blueprint(main_bp)
    
    #Affichage des routes pour debug
    #print(app.url_map)
    
    return app


