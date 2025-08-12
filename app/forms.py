# app/forms.py
# ============================================================================
# Formulaires Flask‑WTF pour chargement SIG et fusion par critère
# ============================================================================

from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileRequired, MultipleFileField
from wtforms import SelectField, FloatField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, NumberRange, Optional


class FileUploadForm(FlaskForm):
    """Formulaire de chargement des fichiers géospatiaux (ZIP Shapefile / GeoJSON)."""

    # MultipleFileField = vrai multi-upload (mieux que FileField + render_kw={"multiple": True})
    # forms.py (champ files)
    files = MultipleFileField(
        "Fichiers (ZIP Shapefile / GeoJSON)",
        validators=[
            FileRequired("Veuillez sélectionner au moins un fichier."),
            FileAllowed(["zip", "geojson", "json"], "Formats acceptés : .zip / .geojson / .json"),
        ],
        render_kw={"accept": ".zip,.geojson,.json"},
    )

    area_threshold = FloatField(
        "Seuil de superficie (m²)",
        default=100.0,
        validators=[NumberRange(min=0, message="Le seuil doit être positif.")],
        description="Micro‑polygones sous ce seuil seront filtrés après fusion.",
    )

    submit = SubmitField("Charger")


class FusionSIGForm(FlaskForm):
    """Formulaire de lancement de fusion + critère optionnel pour la carte."""

    criterion = SelectField(
        "Critère (optionnel)",
        choices=[("", "— Choisir un champ —")],  # sera surchargé dynamiquement
        validators=[Optional()],
        description="Champ attributaire pour colorer/filtrer la carte (catégoriel de préférence).",
    )

    area_threshold = FloatField(
        "Seuil de superficie (m²)",
        default=100.0,
        validators=[DataRequired(message="Le seuil de superficie est requis."),
                    NumberRange(min=0, message="Le seuil doit être positif.")],
    )

    submit = SubmitField("Lancer la fusion")


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
