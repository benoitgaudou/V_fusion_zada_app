# ============================================================================
# app/forms.py - Formulaires Flask-WTF
# ============================================================================

from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileAllowed, FileRequired
from wtforms import (StringField, FloatField, SelectField, TextAreaField, 
                     SubmitField, IntegerField)
from wtforms.validators import DataRequired, NumberRange, Optional

class FileUploadForm(FlaskForm):
    """Formulaire de chargement de fichiers géospatiaux"""
    
    files = FileField(
        'Fichiers Shapefiles/GeoJSON',
        validators=[
            FileRequired("Veuillez sélectionner au moins un fichier"),
            FileAllowed(['shp', 'geojson', 'zip'], 'Fichiers acceptés: .zip (shapefile), .geojson')
        ],
        render_kw={"multiple": True}
    )
    
    area_threshold = FloatField(
        'Seuil de superficie (m²)',
        default=100,
        validators=[NumberRange(min=0, message="Le seuil doit être positif")]
    )
    
    submit = SubmitField('Charger les fichiers')

class FusionSIGForm(FlaskForm):
    """Formulaire de fusion par critères SIG"""
    
    criterion = SelectField(
        'Critère de fusion',
        choices=[],  # Sera rempli dynamiquement
        validators=[DataRequired("Veuillez sélectionner un critère")]
    )
    
    area_threshold = FloatField(
        'Seuil de superficie (m²)',
        default=100,
        validators=[NumberRange(min=0)]
    )
    
    submit = SubmitField('Fusionner selon ce critère')

class NLPQueryForm(FlaskForm):
    """Formulaire de recherche sémantique NLP"""
    
    query = TextAreaField(
        'Requête de recherche',
        validators=[DataRequired("Veuillez saisir une requête")],
        render_kw={"rows": 3, "placeholder": "Ex: zones agricoles près des rivières"}
    )
    
    similarity_threshold = FloatField(
        'Seuil de similarité',
        default=0.7,
        validators=[NumberRange(min=0, max=1, message="Entre 0 et 1")]
    )
    
    submit = SubmitField('Rechercher')
