# ============================================================================
# STRUCTURE DE L'APPLICATION ZADA FLASK
# ============================================================================

# run.py - Point d'entrée de l'application
"""
Point d'entrée principal de l'application ZADA Flask
"""
from app import create_app
import os


app = create_app(config_name='production')

if __name__ == '__main__':
    # Configuration pour le développement
    app.run(debug=True, host='0.0.0.0', port=5000)