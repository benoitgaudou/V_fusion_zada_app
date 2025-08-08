from flask import Blueprint, render_template, request, flash, redirect, url_for, jsonify, session
from werkzeug.utils import secure_filename
import json
from pathlib import Path
import logging
import traceback

from app.forms import FileUploadForm, FusionSIGForm, NLPQueryForm
from app.modules.file_loader import FileLoader
from app.modules.zada_fusion import ZADAFusionEngine
from app.modules.map_generator import MapDataGenerator
from app.modules.exceptions import ZADAException

main_bp = Blueprint('main', __name__)
logger = logging.getLogger(__name__)


# Ajoutez ces routes AU DÉBUT de votre fichier app/routes.py
# (avant @main_bp.route('/process_fusion', methods=['POST']))

@main_bp.route('/')
def home():
    """Page d'accueil avec formulaire de chargement"""
    form = FileUploadForm()
    
    # Récupérer la liste des fichiers chargés depuis la session
    loaded_files = session.get('loaded_files', [])
    
    return render_template('home.html', form=form, loaded_files=loaded_files)

@main_bp.route('/upload', methods=['POST'])
def upload_files():
    """Traitement du chargement de fichiers"""
    form = FileUploadForm()
    
    if form.validate_on_submit():
        try:
            # Initialiser le loader
            from flask import current_app
            loader = FileLoader(current_app.config['UPLOAD_FOLDER'])
            
            # Extraire et sauvegarder les fichiers
            uploaded_files = request.files.getlist('files')
            if not uploaded_files:
                flash("Aucun fichier sélectionné", "error")
                return redirect(url_for('main.home'))
            
            geodataframes = loader.process_uploaded_files(uploaded_files)
            

            if not geodataframes:
                flash("Aucun fichier géospatial valide détecté", "error")
                return redirect(url_for('main.home'))
            
            # Sauvegarder les informations en session
            session['loaded_files'] = [
                {
                    'name': name,
                    'count': len(gdf),
                    'columns': list(gdf.columns),
                    'bounds': gdf.total_bounds.tolist() if not gdf.empty else []
                }
                for gdf, name in geodataframes
            ]
            
            session['area_threshold'] = form.area_threshold.data
            
            flash(f"Succès ! {len(geodataframes)} fichiers chargés", "success")
            
            return redirect(url_for('main.fusion_sig'))
            
        except Exception as e:
            logger.error(f"Erreur chargement: {e}")
            flash(f"Erreur lors du chargement: {str(e)}", "error")
            return redirect(url_for('main.home'))
    
    # Si validation échoue
    for field, errors in form.errors.items():
        for error in errors:
            flash(f"{field}: {error}", "error")
    
    return redirect(url_for('main.home'))

@main_bp.route('/fusion_sig')
def fusion_sig():
    """Interface de fusion par critères SIG"""
    loaded_files = session.get('loaded_files', [])
    
    if not loaded_files:
        flash("Veuillez d'abord charger des fichiers", "warning")
        return redirect(url_for('main.home'))
    
    form = FusionSIGForm()
    
    # Extraire les colonnes communes pour les critères
    all_columns = set()
    for file_info in loaded_files:
        all_columns.update(file_info['columns'])
    
    # Exclure les colonnes techniques
    excluded = {'geometry', 'original_source_id', 'original_source_name'}
    criteria_columns = [(col, col.replace('_', ' ').title()) 
                       for col in sorted(all_columns - excluded)]
    
    form.criterion.choices = [('', 'Sélectionner un critère...')] + criteria_columns
    form.area_threshold.data = session.get('area_threshold', 100)
    
    return render_template('fusion_sig.html', form=form, loaded_files=loaded_files)

@main_bp.route('/nlp_query')
def nlp_query():
    """Interface de recherche sémantique NLP"""
    form = NLPQueryForm()
    return render_template('nlp_query.html', form=form)

@main_bp.route('/api/map_data/<criterion>')
def get_map_data(criterion):
    """API pour récupérer les données cartographiques"""
    # TODO: Implémenter récupération données selon critère
    return jsonify({
        'type': 'FeatureCollection',
        'features': []
    })

@main_bp.route('/process_fusion', methods=['POST'])
def process_fusion():
    """Traitement complet de la fusion ZADA"""
    form = FusionSIGForm()
    
    if not form.validate_on_submit():
        return jsonify({
            'success': False,
            'error': 'Données de formulaire invalides'
        }), 400
    
    try:
        # Récupérer les paramètres
        criterion = form.criterion.data
        area_threshold = form.area_threshold.data
        
        # Vérifier qu'on a des fichiers chargés en session
        loaded_files_info = session.get('loaded_files', [])
        if not loaded_files_info:
            return jsonify({
                'success': False,
                'error': 'Aucun fichier chargé. Veuillez retourner à l\'accueil.'
            }), 400
        
        # Recharger les fichiers depuis le disque
        from flask import current_app
        loader = FileLoader(current_app.config['UPLOAD_FOLDER'])
        
        # Reconstituer les chemins des fichiers
        upload_folder = Path(current_app.config['UPLOAD_FOLDER'])
        file_paths = []
        
        for file_info in loaded_files_info:
            # Chercher les fichiers correspondants
            potential_paths = list(upload_folder.rglob(f"{file_info['name']}.*"))
            shp_files = [p for p in potential_paths if p.suffix.lower() == '.shp']
            geojson_files = [p for p in potential_paths if p.suffix.lower() == '.geojson']
            
            file_paths.extend(shp_files + geojson_files)
        
        if not file_paths:
            return jsonify({
                'success': False,
                'error': 'Fichiers source introuvables. Veuillez les recharger.'
            }), 400
        
        # Charger les GeoDataFrames
        logger.info(f"Rechargement de {len(file_paths)} fichiers pour fusion")
        geodataframes = loader.load_geodataframes(file_paths)
        
        if len(geodataframes) < 2:
            return jsonify({
                'success': False,
                'error': 'Au moins 2 fichiers valides requis pour la fusion'
            }), 400
        
        # Initialiser le moteur de fusion ZADA
        fusion_engine = ZADAFusionEngine(area_threshold=area_threshold)
        
        # Exécuter la fusion
        logger.info("Démarrage de la fusion ZADA...")
        result_gdf = fusion_engine.execute_fusion(geodataframes)
        
        if result_gdf is None:
            return jsonify({
                'success': False,
                'error': 'Échec de la fusion ZADA'
            }), 500
        
        # Appliquer le filtrage par critère si spécifié
        if criterion and criterion in result_gdf.columns:
            logger.info(f"Application du critère de filtrage: {criterion}")
            filtered_gdf = fusion_engine.filter_by_criterion(result_gdf, criterion)
        else:
            filtered_gdf = result_gdf
        
        # Générer les données cartographiques
        map_generator = MapDataGenerator()
        
        # Convertir en GeoJSON pour Leaflet
        map_data = map_generator.gdf_to_leaflet_geojson(
            filtered_gdf,
            properties_to_include=[
                'intersection_type', 'source_names', 'original_source_name',
                criterion if criterion else None
            ]
        )
        
        # Générer la légende
        legend_data = map_generator.generate_legend_data(filtered_gdf)
        
        # Calculer les limites géographiques
        map_bounds = map_generator.get_map_bounds(filtered_gdf)
        
        # Obtenir les statistiques
        stats = fusion_engine.get_fusion_statistics()
        
        # Sauvegarder le résultat
        results_folder = Path(current_app.config['RESULTS_FOLDER'])
        output_path = results_folder / f"fusion_result_{criterion or 'all'}.geojson"
        
        success_export = fusion_engine.export_to_geojson(filtered_gdf, output_path)
        
        # Préparer la réponse
        response_data = {
            'success': True,
            'message': f'Fusion réussie selon le critère: {criterion or "tous"}',
            'statistics': {
                'total_features': stats.get('total_features', 0),
                'intersections': stats.get('intersections', 0),
                'differences': stats.get('differences', 0),
                'originals': stats.get('originals', 0),
                'area_threshold': stats.get('area_threshold', area_threshold),
                'criterion_applied': criterion,
                'exported': success_export
            },
            'map_data': map_data,
            'legend_data': legend_data,
            'map_bounds': map_bounds,
            'export_path': str(output_path) if success_export else None
        }
        
        # Sauvegarder les résultats en session pour usage ultérieur
        session['last_fusion_result'] = {
            'criterion': criterion,
            'statistics': response_data['statistics'],
            'export_path': str(output_path) if success_export else None
        }
        
        logger.info(f"Fusion ZADA terminée avec succès: {stats.get('total_features', 0)} entités")
        
        return jsonify(response_data)
        
    except ZADAException as e:
        logger.error(f"Erreur ZADA: {e}")
        return jsonify({
            'success': False,
            'error': f'Erreur lors de la fusion: {str(e)}'
        }), 500
        
    except Exception as e:
        logger.error(f"Erreur inattendue lors de la fusion: {e}")
        logger.error(traceback.format_exc())
        return jsonify({
            'success': False,
            'error': 'Erreur interne du serveur'
        }), 500

@main_bp.route('/api/download_result/<path:filename>')
def download_result(filename):
    """Téléchargement des résultats de fusion"""
    try:
        from flask import current_app, send_file
        results_folder = Path(current_app.config['RESULTS_FOLDER'])
        file_path = results_folder / filename
        
        if not file_path.exists():
            return jsonify({'error': 'Fichier non trouvé'}), 404
        
        return send_file(file_path, as_attachment=True)
        
    except Exception as e:
        logger.error(f"Erreur téléchargement: {e}")
        return jsonify({'error': 'Erreur lors du téléchargement'}), 500

@main_bp.route('/api/fusion_status')
def fusion_status():
    """Status de la dernière fusion"""
    last_result = session.get('last_fusion_result')
    
    if not last_result:
        return jsonify({
            'has_result': False,
            'message': 'Aucune fusion récente'
        })
    
    return jsonify({
        'has_result': True,
        'criterion': last_result.get('criterion'),
        'statistics': last_result.get('statistics'),
        'export_path': last_result.get('export_path')
    })
