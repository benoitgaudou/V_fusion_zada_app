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
from app.modules.map_generator import MapDataGenerator
from app.modules.exceptions import ZADAException, FileLoadingError

from app.modules.nlp import nlp_engine
from app.forms import NLPInitForm


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
def _non_tech_columns(gdf: gpd.GeoDataFrame):
    """
    Retourne les colonnes 'métier' affichable en UI,
    en excluant les champs techniques (casse insensible),
    en tenant compte du VRAI nom de la géométrie,
    et en excluant les colonnes commençant par certains préfixes, ex: 'nom', 'id', 'source'
    """
    excluded_base = {"geometry", "intersection_type", "type", "source", "source_names"}
    # les préfixes des éléments à exclure
    prefixes_to_exclude = ("original", "source", "id")

    # géométrie réelle (peut ne pas s'appeler 'geometry')
    try:
        geom_col = gdf.geometry.name
    except Exception:
        geom_col = "geometry"
        
    excluded_lc = {x.lower() for x in (excluded_base | {geom_col})}
    

    def is_excluded(col: str) -> bool:
        c = col.lower()
        if c in excluded_lc:
            return True
        
        # Exclure les colonnes commençant par les préfixes donnés
        return any(c.startswith(prefix) for prefix in prefixes_to_exclude)
        
    # Filtrage avec la prise en compte des préfixes
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
            cols = _non_tech_columns(gdf)
            
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
        session['fusion_result_metadata'] = {
            'export_path': str(out_geojson),
            'available_fields': _non_tech_columns(result_gdf),
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
            unique_count = s.nunique(dropna=True)
            
            # Appel correct de la méthode statique
            #sample = ZadaMerger._convert_numpy_types(sample)
            #unique_count = ZadaMerger._convert_numpy_types(unique_count)
            # Utilisation de l'ancien algorithme juste l'intersection par pair et la différence en commentant les deux lignes d'avant
            
            
            fields.append({
                'name': col,
                'label': col.replace('_', ' ').title(),
                'type': dtype,
                'unique_count': int(unique_count),
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
# NLP Pour la Recherche Sémantique
# -------------------------------------------------------------------


from app.modules.nlp import nlp_engine
from app.modules.nlp.api import init_from_fusion_export, semantic_search
from app.modules.nlp.api import _get_engine


@main_bp.route('/nlp_query', methods=['GET'])
def nlp_query():
    from app.forms import NLPQueryForm
    form = NLPQueryForm()
    stats = nlp_engine.stats()
    return render_template('nlp_query.html', form=form, nlp_ready=stats["ready"], stats=stats)


@main_bp.route('/api/nlp/init', methods=['POST'])
def api_nlp_init():
    meta = session.get('fusion_result_metadata')
    if not meta or not meta.get('export_path'):
        return jsonify({'success': False, 'error': "Aucun résultat de fusion en session."}), 400

    data = request.get_json(silent=True) or request.form
    backend = (data.get('backend') or '').strip().lower()
    try:
        return jsonify(init_from_fusion_export(meta['export_path'], backend=backend if backend else None))
    except Exception as e:
        current_app.logger.exception("api_nlp_init")
        return jsonify({'success': False, 'error': str(e)}), 500



@main_bp.route('/api/nlp/search', methods=['POST'])
def api_nlp_search():
    meta = session.get('fusion_result_metadata')
    if not meta or not meta.get('export_path'):
        return jsonify({'success': False, 'error': "Aucun résultat de fusion en session."}), 400

    data = request.get_json(silent=True) or request.form
    q = (data.get('query') or '').strip()
    top_k = int(data.get('max_results', 10))
    if not q:
        return jsonify({'success': False, 'error': 'Requête vide'}), 400

    try:
        return jsonify(semantic_search(meta['export_path'], q, top_k=top_k))
    except Exception as e:
        current_app.logger.exception("api_nlp_search")
        return jsonify({'success': False, 'error': str(e)}), 500
    
@main_bp.route('/api/nlp/models', methods=['GET'])
def api_nlp_models():
    try:
        models = nlp_engine.available_models()  # renvoie [] si rien
        return jsonify({"success": True, "models": models})
    except Exception as e:
        current_app.logger.exception("api_nlp_models")
        return jsonify({"success": False, "error": str(e)}), 500

@main_bp.route('/api/nlp/status', methods=['GET'])
def api_nlp_status():
    try:
        meta = session.get('fusion_result_metadata')
        if not meta or not meta.get('export_path'):
            return jsonify({'success': False, 'error': "Aucun export_path en session."}), 400

        eng = _get_engine(meta['export_path'])  # Récupérer l’instance correcte
        st = eng.stats()  # Statut du moteur correct
        return jsonify({"success": True, "ready": st.get("ready", False), "stats": st})
    except Exception as e:
        current_app.logger.exception("api_nlp_status")
        return jsonify({"success": False, "error": str(e)}), 500

# Exports génériques (fonctionnemet pour n'importe quel GeoDataFrame)
from app.modules.nlp.card_exports import (
    export_from_results,         # pour NLP (df de search -> fichier)
    export_geojson_bytes,        # pour thématique (GDF -> bytes)
    export_gpkg_bytes,           # idem
    export_shapefile_zip,        # idem
)

# Export NLP 
@main_bp.route("/api/nlp/export", methods=["POST"])
def api_nlp_export():
    """
    Body JSON:
    {
      "fmt": "shp" | "gpkg" | "geojson",
      "top_k": 100,                 # optionnel
      "query": "texte",             # optionnel si export direct depuis une requête
      "rows": [{"row_idx":0,"similarite":0.91}, ...]   # optionnel si déjà calculé côté client
    }
    """
    meta = session.get('fusion_result_metadata')
    if not meta or not meta.get('export_path'):
        return jsonify({'success': False, 'error': "Aucun résultat de fusion en session."}), 400

    payload = request.get_json(force=True) or {}
    fmt = (payload.get("fmt") or "").lower()
    top_k = int(payload.get("top_k", 100))

    # Récupère le bon moteur NLP lié au fichier en session
    try:
        eng = _get_engine(meta['export_path'])
    except Exception as e:
        current_app.logger.exception("api_nlp_export/_get_engine")
        return jsonify({'success': False, 'error': f"Moteur NLP indisponible: {e}"}), 500

    # Construit le DataFrame des résultats
    if "rows" in payload and payload["rows"]:
        df = pd.DataFrame(payload["rows"])
        if df.empty or not {"row_idx", "similarite"}.issubset(df.columns):
            return jsonify({'success': False, 'error': "rows doit contenir row_idx et similarite."}), 400
    else:
        q = (payload.get("query") or "").strip()
        if not q:
            return jsonify({'success': False, 'error': "query vide (ou fournissez 'rows')."}), 400
        df = eng.search(q, top_k=top_k)
        if df.empty:
            return jsonify({'success': False, 'error': "Aucun résultat pour la requête."}), 400

    try:
        data = export_from_results(fmt, eng.corpus_gdf, df, layer="zada_nlp")
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        current_app.logger.exception("api_nlp_export/export")
        return jsonify({'success': False, 'error': f"Erreur export: {e}"}), 500

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    filenames = {
        "shp":    f"zada_nlp_{stamp}.shp.zip",
        "gpkg":   f"zada_nlp_{stamp}.gpkg",
        "geojson":f"zada_nlp_{stamp}.geojson",
    }
    mimes = {
        "shp":    "application/zip",
        "gpkg":   "application/geopackage+sqlite3",
        "geojson":"application/geo+json",
    }
    return send_file(
        io.BytesIO(data),
        mimetype=mimes.get(fmt, "application/octet-stream"),
        as_attachment=True,
        download_name=filenames.get(fmt, f"zada_nlp_{stamp}.bin"),
    )


# Export carte thématique par critère
@main_bp.route("/api/map/export", methods=["POST"])
def api_map_export():
    """
    Body JSON:
    {
      "fmt": "geojson" | "gpkg" | "shp",
      "field_name": "nom_du_champ",
      "palette": "default" | "pastel" | "vibrant" | "earth",
      "layer": "nom_couche_gpkg"   # optionnel (défaut: 'zada_thematic')
    }
    """
    meta = session.get('fusion_result_metadata')
    if not meta or not meta.get('export_path'):
        return jsonify({'success': False, 'error': "Aucun résultat de fusion en session."}), 400

    payload = request.get_json(force=True) or {}
    fmt = (payload.get("fmt") or "").lower()
    field_name = (payload.get("field_name") or "").strip()
    palette = (payload.get("palette") or "default").strip()
    layer = (payload.get("layer") or "zada_thematic").strip()

    if not field_name:
        return jsonify({'success': False, 'error': "Champ 'field_name' requis."}), 400

    try:
        gdf_source = gpd.read_file(meta['export_path'])
        if gdf_source is None or gdf_source.empty:
            return jsonify({'success': False, 'error': "Carte source vide."}), 400
        if field_name not in gdf_source.columns:
            return jsonify({'success': False, 'error': f"Champ '{field_name}' introuvable"}), 404

        gen = MapDataGenerator()
        # Construit un GDF prêt à l’export (EPSG:4326)
        gdf_export, legend, _ = gen.build_thematic_gdf(gdf_source, field_name=field_name, palette_name=palette)

        # Exporte selon fmt
        if fmt == "geojson":
            data = export_geojson_bytes(gdf_export)
        elif fmt == "gpkg":
            data = export_gpkg_bytes(gdf_export, layer=layer)
        elif fmt == "shp":
            data = export_shapefile_zip(gdf_export)
        else:
            return jsonify({'success': False, 'error': "Format non supporté (shp|gpkg|geojson)."}), 400

    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        current_app.logger.exception("api_map_export")
        return jsonify({'success': False, 'error': f"Erreur export: {e}"}), 500

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    filenames = {
        "shp":    f"zada_thematic_{field_name}_{palette}_{stamp}.shp.zip",
        "gpkg":   f"zada_thematic_{field_name}_{palette}_{stamp}.gpkg",
        "geojson":f"zada_thematic_{field_name}_{palette}_{stamp}.geojson",
    }
    mimes = {
        "shp":    "application/zip",
        "gpkg":   "application/geopackage+sqlite3",
        "geojson":"application/geo+json",
    }
    return send_file(
        io.BytesIO(data),
        mimetype=mimes.get(fmt, "application/octet-stream"),
        as_attachment=True,
        download_name=filenames.get(fmt, f"zada_thematic_{stamp}.bin"),
    )
