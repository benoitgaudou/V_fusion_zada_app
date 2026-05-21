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
        "Fichiers (ZIP de Shapefile / ZIP de GEOJSON / multiples GeoJSON)",
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

    choix_zada_merger = SelectField(
        "Zada Merger choice",
        choices=[
            ("default", "DD"),
            ("titouan", "TT"),
        ],
        default="DD",
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



### Partie de NLP (NLP pour la partie de Recherche sémantique)

from wtforms import SelectField, IntegerField, BooleanField

# Étendre NLPQueryForm existant avec sélection de modèle
class NLPQueryForm(FlaskForm):
    """Formulaire de recherche sémantique NLP - Version étendue"""
    
    # Champ existant - garder tel quel
    query = TextAreaField(
        'Requête de recherche',
        validators=[DataRequired("Veuillez saisir une requête")],
        render_kw={"rows": 3, "placeholder": "Ex: zones agricoles près des rivières"}
    )
    
    # Champ existant - garder tel quel  
    similarity_threshold = FloatField(
        'Seuil de similarité',
        default=0.7,
        validators=[NumberRange(min=0, max=1, message="Entre 0 et 1")]
    )
    
    # NOUVEAU : Sélection du modèle
    model_selection = SelectField(
        'Modèle NLP',
        choices=[],  # Sera rempli dynamiquement
        validators=[Optional()],
        description="Modèle à utiliser pour la recherche (automatique si vide)"
    )
    
    # NOUVEAU : Nombre de résultats
    max_results = IntegerField(
        'Nombre de résultats',
        default=10,
        validators=[NumberRange(min=1, max=50, message="Entre 1 et 50")]
    )
    
    # NOUVEAU : Afficher sur carte
    show_map = BooleanField(
        'Afficher sur la carte',
        default=True
    )
    
    submit = SubmitField('Rechercher')

# Nouveau formulaire pour l'initialisation NLP
class NLPInitForm(FlaskForm):
    """Formulaire d'initialisation du système NLP"""
    
    model_selection = SelectField(
        'Modèle à charger',
        choices=[],  # Sera rempli dynamiquement
        validators=[Optional()],
        description="Laisser vide pour sélection automatique"
    )
    
    colonnes_exclues = TextAreaField(
        'Colonnes à exclure (optionnel)',
        validators=[Optional()],
        render_kw={"rows": 2, "placeholder": "Ex: id, code_postal, created_at"},
        description="Colonnes à exclure du corpus, séparées par des virgules"
    )
    
    submit = SubmitField('Initialiser NLP')
