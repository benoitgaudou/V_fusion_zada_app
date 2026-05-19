from __future__ import annotations

from flask import render_template, session

from . import main_bp
from app.forms import FileUploadForm


@main_bp.route('/')
def home():
    """Page d'accueil avec formulaire de chargement."""
    form = FileUploadForm()
    loaded_files = session.get('loaded_files', [])
    return render_template('home.html', form=form, loaded_files=loaded_files)
