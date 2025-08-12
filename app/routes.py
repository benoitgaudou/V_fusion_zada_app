# app/routes.py
from __future__ import annotations

from flask import Blueprint, render_template, request, flash, redirect, url_for, jsonify, session, current_app, send_file
from pathlib import Path
from typing import Iterable, List
import re
import unicodedata
import geopandas as gpd
import pandas as pd
import logging
import traceback
import json
import io
import datetime as dt

from app.forms import FileUploadForm, FusionSIGForm, NLPQueryForm
from app.modules.file_loader import FileLoader, FileLoaderConfig
from app.modules.zada_fusion import ZadaMerger, MergeConfig
from app.modules.map_generator import MapDataGenerator  # version simplifiée: generate_thematic_geojson + get_map_bounds
from app.modules.exceptions import ZADAException, FileLoadingError

main_bp = Blueprint('main', __name__)
logger = logging.getLogger(__name__)

# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------

def _get_paths():
    uploads = Path(current_app.config["UPLOAD_FOLDER"])
    stage   = Path(current_app.config["STAGE_FOLDER"])
    results = Path(current_app.config["RESULTS_FOLDER"])
    for p in (uploads, stage, results):
        p.mkdir(parents=True, exist_ok=True)
    return uploads, stage, results

def _get_loader() -> FileLoader:
    uploads, _, _ = _get_paths()
    cfg = FileLoaderConfig(
        upload_folder=uploads,
        force_output_crs=current_app.config["DEFAULT_CRS"],
        assume_input_crs=current_app.config["DEFAULT_CRS"],
        max_features_debug=None,
        allow_network_proj=bool(current_app.config.get("PROJ_NETWORK", False)),
        keep_extracted=False,
    )
    return FileLoader(cfg)

def _get_merger(area_threshold: float | None = None) -> ZadaMerger:
    at = float(
        area_threshold
        if area_threshold is not None
        else session.get("area_threshold", current_app.config["DEFAULT_AREA_THRESHOLD"])
    )
    mcfg = MergeConfig(
        area_threshold_m2=at,
        input_crs_fallback=current_app.config["DEFAULT_CRS"],
        output_crs=current_app.config["DEFAULT_CRS"],
        metric_crs=current_app.config["METRIC_CRS"],
        sample_unique_values=10,
        similarity_threshold=0.30,
    )
    return ZadaMerger(mcfg)


#
def _non_tech_columns(
    gdf: gpd.GeoDataFrame,
    excluded_exact: Iterable[str] = (
        "geometry", "Original_source_id", "Original_source_name",
        "intersection_type", "type", "sources", "source_names", "id"
    ),
    excluded_patterns: Iterable[str] = ()
) -> List[str]:
    """
    Retourne les colonnes 'métier' en excluant :
      - les noms exacts (insensible à la casse)
      - les préfixes avec '*', ex: 'nom*', 'id*', 'source_*'
    """

    def norm(s: str) -> str:
        # normalisation simple: strip + minuscules + sans accents
        s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode("ascii")
        return s.strip().lower()

    # géométrie réelle (peut ne pas s'appeler 'geometry')
    try:
        geom_col = gdf.geometry.name
    except Exception:
        geom_col = "geometry"

    # 1) Set pour exclusions exactes (normalisées)
    exact = {norm(x) for x in set(excluded_exact) | {geom_col}}

    # 2) Préfixes à exclure (soit donnés via excluded_patterns, soit tu peux
    #    décider d'interpréter AUSSI certains exacts comme préfixes)
    #    On accepte des motifs avec étoile finale 'xxx*'
    prefixes = []
    for pat in excluded_patterns:
        p = norm(pat)
        if p.endswith("*"):
            prefixes.append(p[:-1])  # retire l’étoile -> vrai préfixe
        else:
            # si l’utilisateur met un motif sans '*', on le traite comme exact
            exact.add(p)

    def is_excluded(col: str) -> bool:
        c = norm(col)
        if c in exact:
            return True
        return any(c.startswith(pfx) for pfx in prefixes if pfx)

    # retourne seulement les colonnes non exclues (ordre préservé)
    return [c for c in gdf.columns if not is_excluded(c)]


# -------------------------------------------------------------------
# Accueil
# -------------------------------------------------------------------
@main_bp.route('/')
def home():
    """Page d'accueil avec formulaire de chargement"""
    form = FileUploadForm()
    loaded_files = session.get('loaded_files', [])
    return render_template('home.html', form=form, loaded_files=loaded_files)

# -------------------------------------------------------------------
# Upload + staging
# -------------------------------------------------------------------
@main_bp.route('/upload', methods=['POST'])
def upload_files():
    """
    1) Charge les fichiers
    2) Écrit des GeoJSON en stage
    3) Lance la fusion backend immédiatement
    4) Stocke le résultat + métadonnées en session
    5) Redirige vers /fusion_sig (page 2) pour choisir un champ et générer la carte
    """
    form = FileUploadForm()
    if not form.validate_on_submit():
        for field, errors in form.errors.items():
            for error in errors:
                flash(f"{field}: {error}", "error")
        return redirect(url_for('main.home'))

    try:
        loader = _get_loader()
        _, stage_folder, results_folder = _get_paths()

        uploaded_files = request.files.getlist('files')
        if not uploaded_files:
            flash("Aucun fichier sélectionné", "error")
            return redirect(url_for('main.home'))

        # Charge en GeoDataFrames puis écrit des GeoJSON stage (un par source)
        loaded = loader.process_uploaded_files(uploaded_files)  # [(gdf, stem), ...]
        stage_paths = []
        loaded_files_info = []

        candidate_fields_intersection = None
        for gdf, stem in loaded:
            # écrire stage
            stage_path = stage_folder / f"{stem}.geojson"
            stage_path.write_text(loader.to_geojson_str(gdf), encoding="utf-8")
            stage_paths.append(str(stage_path))

            # infos pour UI
            cols = _non_tech_columns(
                gdf,
                excluded_exact={ "geometry", "Original_source_id", "Original_source_name",
                                "intersection_type", "type", "sources", "source_names", "id"},
                excluded_patterns={"nom*", "Id*", "source_*", "Original_souurce","source_names*","intersection*", "Origin*"}
                )
            
            loaded_files_info.append({
                'name': stem,
                'count': int(len(gdf)),
                'columns': cols,
                'bounds': gdf.total_bounds.tolist() if not gdf.empty else []
            })

            # champs candidats (catégoriels ou peu de modalités)
            cat_cols = [c for c in cols if (str(gdf[c].dtype) == "object") or (gdf[c].nunique(dropna=True) <= 25)]
            candidate_fields_intersection = (
                set(cat_cols) if candidate_fields_intersection is None
                else (candidate_fields_intersection & set(cat_cols))
            )
            
        def _wipe_fusion_session():
            for key in ('fusion_result_metadata', 'candidate_fields', 'loaded_files', 'stage_paths'):
                session.pop(key, None)
        _wipe_fusion_session()

        # enregistre seuil pour merger
        session['area_threshold'] = float(form.area_threshold.data or 100.0)
        session['loaded_files'] = loaded_files_info
        session['stage_paths'] = stage_paths
        session['candidate_fields'] = sorted(candidate_fields_intersection) if candidate_fields_intersection else []

        # >>> LANCE LA FUSION ICI (sans critère) <<<
        # >>> LANCE LA FUSION ICI (sans critère) <<<
        if len(stage_paths) < 2:
            flash("Au moins 2 sources sont nécessaires pour fusionner.", "error")
            return redirect(url_for('main.home'))

        # Seuil à utiliser (déjà stocké juste au-dessus, on le relit par sécurité)
        at = float(session.get('area_threshold', 100.0))

        # Construire un merger prêt avec le bon seuil (ne pas modifier la config ensuite)
        merger = _get_merger(area_threshold=at)

        merger.load_sources(stage_paths)
        result_gdf = merger.merge()
        if result_gdf is None or result_gdf.empty:
            flash("Fusion vide.", "error")
            return redirect(url_for('main.home'))


        # Sauvegarder le GeoJSON de résultat
        out_geojson = results_folder / f"fusion_result_all.geojson"
        result_gdf.to_file(out_geojson, driver="GeoJSON")

        # Métadonnées pour la page 2
        _wipe_fusion_session()
        session['fusion_result_metadata'] = {
            'export_path': str(out_geojson),
            'available_fields': _non_tech_columns(
                result_gdf,
                excluded_exact={ "geometry", "Original_source_id", "Original_source_name",
                                "intersection_type", "type", "sources", "source_names", "id"},
                excluded_patterns={"nom*", "id*", "source*", "Original_source","source_names*","intersection*", "Origin*"}
                ),
            'total_features': int(len(result_gdf)),
            'crs': str(result_gdf.crs),
            'bounds': result_gdf.total_bounds.tolist() if not result_gdf.empty else []
        }

        flash(f"Succès ! {len(stage_paths)} fichier(s) chargé(s) et fusion réalisée.", "success")
        return redirect(url_for('main.fusion_sig'))

    except FileLoadingError as e:
        logger.error(f"Erreur chargement: {e}")
        flash(f"Erreur lors du chargement: {str(e)}", "error")
        return redirect(url_for('main.home'))
    except Exception as e:
        logger.exception("Erreur upload/fusion inattendue")
        flash(f"Erreur lors du chargement/fusion: {str(e)}", "error")
        return redirect(url_for('main.home'))

# -------------------------------------------------------------------
# Page 2 : UI champs + génération de carte (pas de POST de fusion ici)
# -------------------------------------------------------------------
@main_bp.route('/fusion_sig', methods=["GET"])
def fusion_sig():
    loaded_files = session.get('loaded_files', [])
    meta = session.get('fusion_result_metadata')
    if not loaded_files or not meta:
        flash("Veuillez d'abord charger des fichiers (et fusionner).", "warning")
        return redirect(url_for('main.home'))

    form = FusionSIGForm()  # on réutilise seulement le select côté front (ou rien si tu préfères)
    # Le front va appeler /api/fields puis /api/thematic-map/<field>
    return render_template('fusion_sig.html', form=form, loaded_files=loaded_files)

# -------------------------------------------------------------------
# APIs “thématiques” pour la page 2
# -------------------------------------------------------------------

@main_bp.route('/api/fields')
@main_bp.route('/api/fields-analysis')
def api_fields_analysis():
    """Retourne la liste des champs disponibles sur le résultat fusionné, avec stats."""
    meta = session.get('fusion_result_metadata')
    if not meta or not Path(meta.get('export_path', '')).exists():
        return jsonify({'success': False, 'error': 'Aucun résultat de fusion disponible.'}), 400

    try:
        gdf = gpd.read_file(meta['export_path'])
        fields = []
        for col in _non_tech_columns(gdf):
            s = gdf[col]
            dtype = 'numeric' if pd.api.types.is_numeric_dtype(s) else 'categorical'
            sample = list(s.dropna().unique()[:5])
            fields.append({
                'name': col,
                'label': col.replace('_', ' ').title(),
                'type': dtype,
                'unique_count': int(s.nunique(dropna=True)),
                'sample_values': sample
            })
        return jsonify({'success': True, 'fields': fields})
    except Exception as e:
        logger.exception("Erreur /api/fields-analysis")
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/api/field-analysis/<field_name>', methods=['GET'])
def api_field_analysis(field_name):
    """Analyse légère d’un champ (type, uniques, stats si numérique)."""
    meta = session.get('fusion_result_metadata')
    if not meta:
        return jsonify({'success': False, 'error': 'Aucun résultat de fusion en session.'}), 400

    try:
        gdf = gpd.read_file(meta['export_path'])
        if field_name not in gdf.columns:
            return jsonify({'success': False, 'error': f"Champ '{field_name}' introuvable"}), 404

        s = gdf[field_name]
        non_null = s.dropna()
        is_numeric = str(non_null.dtype).startswith(('int', 'float'))

        analysis = {
            'field_name': field_name,
            'data_type': 'numeric' if is_numeric else 'categorical',
            'total_values': int(len(s)),
            'unique_count': int(non_null.nunique()),
            'null_values': int(s.isna().sum()),
            'sample_values': list(non_null.unique()[:10])
        }

        if is_numeric and not non_null.empty:
            analysis.update({
                'min_value': float(non_null.min()),
                'max_value': float(non_null.max()),
                'mean_value': float(non_null.mean())
            })

        return jsonify({'success': True, 'analysis': analysis})
    except Exception as e:
        logger.error("field-analysis error: %s", e)
        return jsonify({'success': False, 'error': 'Erreur analyse champ.'}), 500

@main_bp.route('/api/thematic-map/<field_name>', methods=['GET'])
def api_thematic_map(field_name):
    """Génère le GeoJSON stylé + légende + bounds pour le champ demandé."""
    palette = request.args.get('palette', 'default')
    meta = session.get('fusion_result_metadata')
    if not meta:
        return jsonify({'success': False, 'error': 'Aucun résultat de fusion en session.'}), 400

    try:
        gdf = gpd.read_file(meta['export_path'])
        if field_name not in gdf.columns:
            return jsonify({'success': False, 'error': f"Champ '{field_name}' introuvable"}), 404

        gen = MapDataGenerator()
        res = gen.generate_thematic_geojson(gdf, field_name=field_name, palette_name=palette)
        if not res.get('success'):
            return jsonify({'success': False, 'error': res.get('error', 'Erreur générateur')}), 400

        bounds = gen.get_map_bounds(gdf)
        # Normalise la légende pour le front (items)
        legend_items = []
        if 'legend' in res and res['legend'].get('items'):
            legend_items = res['legend']['items']
        else:
            # Legacy: si ta version retourne colors + counts
            legend = res.get('legend') or {}
            legend_items = legend.get('items', [])

        return jsonify({
            'success': True,
            'geojson': res['geojson'],
            'legend': {'type': res['legend'].get('type', 'discrete'), 'items': legend_items},
            'analysis': res.get('analysis', {}),
            'palette': res.get('palette_name', palette),
            'bounds': bounds
        })
    except Exception as e:
        logger.error("thematic-map error: %s", e)
        return jsonify({'success': False, 'error': 'Erreur génération carte thématique.'}), 500

@main_bp.route('/api/export-thematic-map/<field_name>', methods=['GET'])
def api_export_thematic_map(field_name):
    """Exporte le GeoJSON thématique (download)."""
    palette = request.args.get('palette', 'default')
    meta = session.get('fusion_result_metadata')
    if not meta:
        return jsonify({'success': False, 'error': 'Aucun résultat de fusion en session.'}), 400

    try:
        gdf = gpd.read_file(meta['export_path'])
        if field_name not in gdf.columns:
            return jsonify({'success': False, 'error': f"Champ '{field_name}' introuvable"}), 404

        gen = MapDataGenerator()
        res = gen.generate_thematic_geojson(gdf, field_name=field_name, palette_name=palette)
        if not res.get('success'):
            return jsonify({'success': False, 'error': res.get('error', 'Erreur générateur')}), 400

        # Sérialise en mémoire et renvoie un fichier
        buf = io.BytesIO()
        buf.write(json.dumps(res['geojson']).encode('utf-8'))
        buf.seek(0)

        fname = f"thematic_{field_name}_{palette}_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.geojson"
        return send_file(buf, mimetype='application/geo+json', as_attachment=True, download_name=fname)
    except Exception as e:
        logger.error("export-thematic error: %s", e)
        return jsonify({'success': False, 'error': 'Erreur export.'}), 500


# -------------------------------------------------------------------
# Les autres endpoints que tu avais (NLP, palettes, fields, etc.)
# → tu peux les laisser tels quels; ils liront la session/les fichiers.
# -------------------------------------------------------------------
@main_bp.route('/nlp_query')
def nlp_query():
    form = NLPQueryForm()
    return render_template('nlp_query.html', form=form)

@main_bp.route('/api/download_result/<path:filename>')
def download_result(filename):
    try:
        results_folder = Path(current_app.config.get('RESULTS_FOLDER', 'out'))
        file_path = results_folder / filename
        if not file_path.exists():
            return jsonify({'error': 'Fichier non trouvé'}), 404
        from flask import send_file
        return send_file(file_path, as_attachment=True)
    except Exception as e:
        logger.error(f"Erreur téléchargement: {e}")
        return jsonify({'error': 'Erreur lors du téléchargement'}), 500
