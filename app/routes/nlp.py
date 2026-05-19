from __future__ import annotations

import pandas as pd
from flask import current_app, jsonify, render_template, request, session

from . import main_bp
from app.forms import NLPQueryForm
from app.modules.nlp import nlp_engine
from app.modules.nlp.api import _get_engine, init_from_fusion_export


@main_bp.route('/nlp_query', methods=['GET'])
def nlp_query():
    form = NLPQueryForm()
    stats = nlp_engine.stats()
    return render_template('nlp_query.html', form=form, nlp_ready=stats['ready'], stats=stats)


@main_bp.route('/api/nlp/init', methods=['POST'])
def api_nlp_init():
    meta = session.get('fusion_result_metadata')
    if not meta or not meta.get('export_path'):
        return jsonify({'success': False, 'error': 'Aucun résultat de fusion en session.'}), 400

    data = request.get_json(silent=True) or request.form
    backend = (data.get('backend') or '').strip().lower()
    try:
        return jsonify(init_from_fusion_export(meta['export_path'], backend=backend if backend else None))
    except Exception as e:
        current_app.logger.exception('api_nlp_init')
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/api/nlp/search', methods=['POST'])
def api_nlp_search():
    meta = session.get('fusion_result_metadata')
    if not meta or not meta.get('export_path'):
        return jsonify({'success': False, 'error': 'Aucun résultat de fusion en session.'}), 400

    data = request.get_json(silent=True) or request.form
    q = (data.get('query') or '').strip()
    if not q:
        return jsonify({'success': False, 'error': 'Requête vide'}), 400

    raw_topk = data.get('top_k', data.get('max_results', 10))
    try:
        top_k = int(raw_topk)
    except (TypeError, ValueError):
        top_k = 10
    top_k = max(1, min(200, top_k))

    mode = (data.get('mode') or 'semantic').strip().lower()
    if mode not in {'semantic', 'keyword'}:
        mode = 'semantic'

    def _clamp01(x, default):
        try:
            v = float(x)
        except (TypeError, ValueError):
            v = default
        return max(0.0, min(1.0, v))

    similarity_threshold = _clamp01(data.get('similarity_threshold', 0.5), 0.5)
    coverage_threshold = _clamp01(data.get('coverage_threshold', 0.0), 0.0)

    try:
        eng = _get_engine(meta['export_path'])
        df = eng.search(query=q, top_k=top_k, mode=mode)
        if df is None:
            df = pd.DataFrame()

        if df.empty:
            return jsonify({
                'success': True,
                'mode': mode,
                'query': q,
                'top_k': top_k,
                'similarity_threshold': similarity_threshold,
                'coverage_threshold': coverage_threshold,
                'result_count': 0,
                'engine': eng.stats(),
                'legend': {'type': 'continuous', 'items': []},
                'bounds': None,
                'geojson': {'type': 'FeatureCollection', 'features': []},
            }), 200

        if mode == 'semantic':
            if 'score' not in df.columns:
                df['score'] = df.get('similarite', 0.0)
            df = df[df['score'] >= similarity_threshold]
        else:
            if 'couverture' not in df.columns:
                df['couverture'] = 0.0
            df = df[df['couverture'] >= coverage_threshold]

        df = df.reset_index(drop=True)
        geojson, legend, bounds = eng.to_geojson(df)

        return jsonify({
            'success': True,
            'mode': mode,
            'query': q,
            'top_k': top_k,
            'similarity_threshold': similarity_threshold,
            'coverage_threshold': coverage_threshold,
            'result_count': int(df.shape[0]),
            'engine': eng.stats(),
            'legend': legend,
            'bounds': bounds,
            'geojson': geojson,
        }), 200
    except Exception as e:
        current_app.logger.exception('api_nlp_search')
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/api/nlp/models', methods=['GET'])
def api_nlp_models():
    try:
        models = nlp_engine.available_models()
        return jsonify({'success': True, 'models': models})
    except Exception as e:
        current_app.logger.exception('api_nlp_models')
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/api/nlp/status', methods=['GET'])
def api_nlp_status():
    try:
        meta = session.get('fusion_result_metadata')
        if not meta or not meta.get('export_path'):
            return jsonify({'success': False, 'error': 'Aucun export_path en session.'}), 400

        eng = _get_engine(meta['export_path'])
        st = eng.stats()
        return jsonify({'success': True, 'ready': st.get('ready', False), 'stats': st})
    except Exception as e:
        current_app.logger.exception('api_nlp_status')
        return jsonify({'success': False, 'error': str(e)}), 500
