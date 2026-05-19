from __future__ import annotations

import datetime as dt
import io
import json
from io import BytesIO
from pathlib import Path

import geopandas as gpd
import pandas as pd
from flask import jsonify, request, send_file, session

from . import main_bp
from .utils import _non_tech_columns
from app.modules.map_generator import MapDataGenerator
from app.modules.nlp.card_exports import (
    export_from_results,
    export_geojson_bytes,
    export_gpkg_bytes,
    export_shapefile_zip,
)
from app.modules.nlp.api import _get_engine
from app.modules.nlp import nlp_engine


@main_bp.route('/api/fields')
@main_bp.route('/api/fields-analysis')
def api_fields_analysis():
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
            fields.append({
                'name': col,
                'label': col.replace('_', ' ').title(),
                'type': dtype,
                'unique_count': int(unique_count),
                'sample_values': sample,
            })
        return jsonify({'success': True, 'fields': fields})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/api/field-analysis/<field_name>', methods=['GET'])
def api_field_analysis(field_name):
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
            'sample_values': list(non_null.unique()[:10]),
        }

        if is_numeric and not non_null.empty:
            analysis.update({
                'min_value': float(non_null.min()),
                'max_value': float(non_null.max()),
                'mean_value': float(non_null.mean()),
            })

        return jsonify({'success': True, 'analysis': analysis})
    except Exception:
        return jsonify({'success': False, 'error': 'Erreur analyse champ.'}), 500


@main_bp.route('/api/thematic-map/<field_name>', methods=['GET'])
def api_thematic_map(field_name):
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
        legend_items = []
        if 'legend' in res and res['legend'].get('items'):
            legend_items = res['legend']['items']
        else:
            legend = res.get('legend') or {}
            legend_items = legend.get('items', [])

        return jsonify({
            'success': True,
            'geojson': res['geojson'],
            'legend': {'type': res['legend'].get('type', 'discrete'), 'items': legend_items},
            'analysis': res.get('analysis', {}),
            'palette': res.get('palette_name', palette),
            'bounds': bounds,
        })
    except Exception:
        return jsonify({'success': False, 'error': 'Erreur génération carte thématique.'}), 500


@main_bp.route('/api/export-thematic-map/<field_name>', methods=['GET'])
def api_export_thematic_map(field_name):
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

        buf = BytesIO()
        buf.write(json.dumps(res['geojson']).encode('utf-8'))
        buf.seek(0)

        fname = f"thematic_{field_name}_{palette}_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.geojson"
        return send_file(buf, mimetype='application/geo+json', as_attachment=True, download_name=fname)
    except Exception:
        return jsonify({'success': False, 'error': 'Erreur export.'}), 500


@main_bp.route('/api/nlp/export', methods=['POST'])
def api_nlp_export():
    meta = session.get('fusion_result_metadata')
    if not meta or not meta.get('export_path'):
        return jsonify({'success': False, 'error': 'Aucun résultat de fusion en session.'}), 400

    payload = request.get_json(force=True) or {}
    fmt = (payload.get('fmt') or '').lower()
    top_k = int(payload.get('top_k', 100))
    mode = (payload.get('mode') or 'semantic').strip().lower()
    if mode not in {'semantic', 'keyword'}:
        mode = 'semantic'

    try:
        eng = _get_engine(meta['export_path'])
    except Exception as e:
        return jsonify({'success': False, 'error': f'Moteur NLP indisponible: {e}'}), 500

    if 'rows' in payload and payload['rows']:
        df = pd.DataFrame(payload['rows'])
        if df.empty or 'row_idx' not in df.columns:
            return jsonify({'success': False, 'error': "rows doit contenir au moins 'row_idx'."}), 400
        if 'mode' not in df.columns:
            df['mode'] = mode
        else:
            m0 = str(df.iloc[0]['mode']).lower()
            df['mode'] = m0 if m0 in {'semantic', 'keyword'} else mode
        if df.iloc[0]['mode'] == 'semantic' and 'similarite' not in df.columns and 'score' in df.columns:
            df['similarite'] = df['score']
    else:
        q = (payload.get('query') or '').strip()
        if not q:
            return jsonify({'success': False, 'error': "query vide (ou fournissez 'rows')."}), 400
        df = eng.search(q, top_k=top_k, mode=mode)
        if df.empty:
            return jsonify({'success': False, 'error': 'Aucun résultat pour la requête.'}), 400

    try:
        data = export_from_results(fmt, eng.corpus_gdf, df, layer='zada_nlp')
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': f'Erreur export: {e}'}), 500

    stamp = dt.datetime.now().strftime('%Y%m%d_%H%M%S')
    suffix = f'{mode}_{stamp}'
    filenames = {
        'shp': f'zada_nlp_{suffix}.shp.zip',
        'gpkg': f'zada_nlp_{suffix}.gpkg',
        'geojson': f'zada_nlp_{suffix}.geojson',
    }
    mimes = {
        'shp': 'application/zip',
        'gpkg': 'application/geopackage+sqlite3',
        'geojson': 'application/geo+json',
    }
    return send_file(
        io.BytesIO(data),
        mimetype=mimes.get(fmt, 'application/octet-stream'),
        as_attachment=True,
        download_name=filenames.get(fmt, f'zada_nlp_{suffix}.bin'),
    )


@main_bp.route('/api/map/export', methods=['POST'])
def api_map_export():
    meta = session.get('fusion_result_metadata')
    if not meta or not meta.get('export_path'):
        return jsonify({'success': False, 'error': 'Aucun résultat de fusion en session.'}), 400

    payload = request.get_json(force=True) or {}
    fmt = (payload.get('fmt') or '').lower()
    field_name = (payload.get('field_name') or '').strip()
    palette = (payload.get('palette') or 'default').strip()
    layer = (payload.get('layer') or 'zada_thematic').strip()

    if not field_name:
        return jsonify({'success': False, 'error': "Champ 'field_name' requis."}), 400

    try:
        gdf_source = gpd.read_file(meta['export_path'])
        if gdf_source is None or gdf_source.empty:
            return jsonify({'success': False, 'error': 'Carte source vide.'}), 400
        if field_name not in gdf_source.columns:
            return jsonify({'success': False, 'error': f"Champ '{field_name}' introuvable"}), 404

        gen = MapDataGenerator()
        gdf_export, legend, _ = gen.build_thematic_gdf(gdf_source, field_name=field_name, palette_name=palette)
        if fmt == 'geojson':
            data = export_geojson_bytes(gdf_export)
        elif fmt == 'gpkg':
            data = export_gpkg_bytes(gdf_export, layer=layer)
        elif fmt == 'shp':
            data = export_shapefile_zip(gdf_export)
        else:
            return jsonify({'success': False, 'error': 'Format non supporté (shp|gpkg|geojson).'}), 400
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': f'Erreur export: {e}'}), 500

    stamp = dt.datetime.now().strftime('%Y%m%d_%H%M%S')
    filenames = {
        'shp': f'zada_thematic_{field_name}_{palette}_{stamp}.shp.zip',
        'gpkg': f'zada_thematic_{field_name}_{palette}_{stamp}.gpkg',
        'geojson': f'zada_thematic_{field_name}_{palette}_{stamp}.geojson',
    }
    mimes = {
        'shp': 'application/zip',
        'gpkg': 'application/geopackage+sqlite3',
        'geojson': 'application/geo+json',
    }
    return send_file(
        io.BytesIO(data),
        mimetype=mimes.get(fmt, 'application/octet-stream'),
        as_attachment=True,
        download_name=filenames.get(fmt, f'zada_thematic_{stamp}.bin'),
    )
