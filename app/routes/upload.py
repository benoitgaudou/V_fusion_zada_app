from __future__ import annotations

import datetime as dt
import os
from io import BytesIO

import geopandas as gpd
from flask import flash, redirect, render_template, request, session, send_file, url_for

from . import main_bp
from .utils import _get_loader, _get_merger, _get_paths, _non_tech_columns
from app.forms import FileUploadForm, FusionSIGForm
from app.modules.exceptions import FileLoadingError
from app.modules.nlp.card_exports import export_gdf


@main_bp.route('/upload', methods=['POST'])
def upload_files():
    form = FileUploadForm()
    if not form.validate_on_submit():
        for field, errors in form.errors.items():
            for error in errors:
                flash(f'{field}: {error}', 'error')
        return redirect(url_for('main.home'))

    try:
        loader = _get_loader()
        _, stage_folder, results_folder = _get_paths()

        uploaded_files = request.files.getlist('files')
        if not uploaded_files:
            flash('Aucun fichier sélectionné', 'error')
            return redirect(url_for('main.home'))

        session.clear()

        loaded = loader.process_uploaded_files(uploaded_files)
        stage_paths = []
        loaded_files_info = []

        candidate_fields_intersection = None
        for gdf, stem in loaded:
            stage_path = stage_folder / f'{stem}.geojson'
            stage_path.write_text(loader.to_geojson_str(gdf), encoding='utf-8')
            stage_paths.append(str(stage_path))

            cols = _non_tech_columns(gdf)
            loaded_files_info.append({
                'name': stem,
                'count': int(len(gdf)),
                'columns': cols,
                'bounds': gdf.total_bounds.tolist() if not gdf.empty else [],
            })

            cat_cols = [
                c for c in cols
                if (str(gdf[c].dtype) == 'object') or (gdf[c].nunique(dropna=True) <= 25)
            ]
            candidate_fields_intersection = (
                set(cat_cols) if candidate_fields_intersection is None
                else (candidate_fields_intersection & set(cat_cols))
            )

        session['area_threshold'] = float(form.area_threshold.data or 100.0)
        session['choix_zada_merger'] = form.choix_zada_merger.data
        session['loaded_files'] = loaded_files_info
        session['stage_paths'] = stage_paths
        session['candidate_fields'] = sorted(candidate_fields_intersection) if candidate_fields_intersection else []

        if len(stage_paths) < 2:
            flash('Au moins 2 sources sont nécessaires pour fusionner.', 'error')
            return redirect(url_for('main.home'))

        at = float(session.get('area_threshold', 100.0))
        merger = _get_merger(area_threshold=at)
        merger.load_sources(stage_paths)
        result_gdf = merger.merge()

        if result_gdf is None or result_gdf.empty:
            flash('Fusion vide.', 'error')
            return redirect(url_for('main.home'))

        out_geojson = results_folder / f'fusion_result_{dt.datetime.now().strftime("%Y%m%d_%H%M%S")}.geojson'
        result_gdf.to_file(out_geojson, driver='GeoJSON')

        session['fusion_file_path'] = str(out_geojson)
        session['fusion_result_metadata'] = {
            'export_path': str(out_geojson),
            'available_fields': _non_tech_columns(result_gdf),
            'total_features': int(len(result_gdf)),
            'crs': str(result_gdf.crs),
            'bounds': result_gdf.total_bounds.tolist() if not result_gdf.empty else [],
        }

        flash(f'Succès ! {len(stage_paths)} fichier(s) chargé(s) et fusion réalisée.', 'success')
        return redirect(url_for('main.fusion_sig'))

    except FileLoadingError as e:
        flash(f'Erreur lors du chargement: {str(e)}', 'error')
        return redirect(url_for('main.home'))
    except Exception as e:
        flash(f'Erreur lors du chargement/fusion: {str(e)}', 'error')
        return redirect(url_for('main.home'))


@main_bp.route('/export_fusion', methods=['POST'])
def export_fusion():
    try:
        if 'fusion_result_metadata' not in session:
            flash("Aucun résultat de fusion disponible. Veuillez d'abord effectuer une fusion.", 'error')
            return redirect(url_for('main.fusion_sig'))

        export_format = request.form.get('format', 'geojson')
        export_path = session['fusion_result_metadata']['export_path']
        if not os.path.exists(export_path):
            flash("Le fichier de fusion n'existe plus. Veuillez refaire la fusion.", 'error')
            return redirect(url_for('main.fusion_sig'))

        result_gdf = gpd.read_file(export_path)
        file_bytes = export_gdf(export_format, result_gdf, layer='zada_fusion')

        filenames = {
            'geojson': 'fusion_zada.geojson',
            'gpkg': 'fusion_zada.gpkg',
            'shp': 'fusion_zada.zip',
        }
        mimetypes = {
            'geojson': 'application/geo+json',
            'gpkg': 'application/geopackage+sqlite3',
            'shp': 'application/zip',
        }

        filename = filenames.get(export_format, 'fusion_zada.zip')
        mimetype = mimetypes.get(export_format, 'application/zip')
        return send_file(
            BytesIO(file_bytes),
            as_attachment=True,
            download_name=filename,
            mimetype=mimetype,
        )
    except Exception as e:
        flash(f"Erreur lors de l'export: {str(e)}", 'error')
        return redirect(url_for('main.fusion_sig'))


@main_bp.route('/fusion_sig', methods=['GET'])
def fusion_sig():
    loaded_files = session.get('loaded_files', [])
    meta = session.get('fusion_result_metadata')
    if not loaded_files or not meta:
        flash("Veuillez d'abord charger des fichiers (et fusionner).", 'warning')
        return redirect(url_for('main.home'))

    form = FusionSIGForm()
    return render_template('fusion_sig.html', form=form, loaded_files=loaded_files)
