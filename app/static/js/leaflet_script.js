// ============================================================================
// static/js/leaflet_script.js - Script principal pour les fonctionnalités carte
// ============================================================================

/**
 * Gestionnaire principal des cartes Leaflet pour l'application ZADA
 */
class ZADAMapManager {
    constructor() {
        this.maps = new Map();
        this.layers = new Map();
        this.controls = new Map();
        this.colorPalette = {
            intersection: '#FF6B6B',
            difference: '#4ECDC4', 
            original: '#45B7D1',
            filtered: '#96CEB4'
        };
        this.defaultCenter = [14.8667, -16.8667]; // 
        this.defaultZoom = 6;
    }

    /**
     * Initialise une carte Leaflet
     * @param {string} containerId - ID du conteneur de la carte
     * @param {Object} options - Options de configuration
     */
    initializeMap(containerId, options = {}) {
        const defaultOptions = {
            center: this.defaultCenter,
            zoom: this.defaultZoom,
            zoomControl: true,
            attributionControl: true
        };

        const mapOptions = { ...defaultOptions, ...options };
        
        // Créer la carte
        const map = L.map(containerId, {
            center: mapOptions.center,
            zoom: mapOptions.zoom,
            zoomControl: mapOptions.zoomControl,
            attributionControl: mapOptions.attributionControl
        });

        // Ajouter la couche de base
        this.addBaseLayers(map);

        // Stocker la référence
        this.maps.set(containerId, map);
        this.layers.set(containerId, new Map());

        return map;
    }

    /**
     * Ajoute les couches de base à la carte
     * @param {L.Map} map - Instance de carte Leaflet
     */
    addBaseLayers(map) {
        const baseLayers = {
            'OpenStreetMap': L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
                attribution: '© OpenStreetMap contributors',
                maxZoom: 19
            }),
            'CartoDB Positron': L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', {
                attribution: '© CartoDB © OpenStreetMap contributors',
                maxZoom: 19
            }),
            'Satellite': L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
                attribution: 'Esri, DigitalGlobe, GeoEye, Earthstar Geographics',
                maxZoom: 18
            })
        };

        // Ajouter la couche par défaut
        baseLayers['OpenStreetMap'].addTo(map);

        // Ajouter le contrôle des couches si plusieurs options
        if (Object.keys(baseLayers).length > 1) {
            L.control.layers(baseLayers).addTo(map);
        }
    }

    /**
     * Ajoute des données GeoJSON à la carte
     * @param {string} mapId - ID de la carte
     * @param {Object} geoJsonData - Données GeoJSON
     * @param {string} layerName - Nom de la couche
     * @param {Object} options - Options de style et comportement
     */
    addGeoJsonLayer(mapId, geoJsonData, layerName, options = {}) {
        const map = this.maps.get(mapId);
        if (!map) {
            console.error(`Carte ${mapId} non trouvée`);
            return null;
        }

        const defaultOptions = {
            style: this.getDefaultStyle.bind(this),
            onEachFeature: this.getDefaultPopup.bind(this),
            pointToLayer: this.getDefaultMarker.bind(this),
            filter: null
        };

        const layerOptions = { ...defaultOptions, ...options };

        // Créer la couche GeoJSON
        const geoJsonLayer = L.geoJSON(geoJsonData, {
            style: layerOptions.style,
            onEachFeature: layerOptions.onEachFeature,
            pointToLayer: layerOptions.pointToLayer,
            filter: layerOptions.filter
        });

        // Ajouter à la carte
        geoJsonLayer.addTo(map);

        // Stocker la référence
        const mapLayers = this.layers.get(mapId);
        mapLayers.set(layerName, geoJsonLayer);

        return geoJsonLayer;
    }

    /**
     * Style par défaut pour les features
     * @param {Object} feature - Feature GeoJSON
     */
    getDefaultStyle(feature) {
        const intersectionType = feature.properties.intersection_type || 'original';
        const baseColor = this.colorPalette[intersectionType] || '#3388ff';

        return {
            color: baseColor,
            fillColor: baseColor,
            fillOpacity: 0.6,
            weight: 2,
            opacity: 0.8
        };
    }

    /**
     * Popup par défaut pour les features
     * @param {Object} feature - Feature GeoJSON
     * @param {L.Layer} layer - Couche Leaflet
     */
    getDefaultPopup(feature, layer) {
        if (!feature.properties) return;

        let popupContent = '<div class="popup-content">';
        
        // Titre basé sur le type
        const type = feature.properties.intersection_type || 'Zone';
        popupContent += `<h6 class="popup-title">${this.getTypeLabel(type)}</h6>`;

        // Propriétés importantes
        const importantProps = ['source_names', 'similarity', 'area'];
        const otherProps = [];

        for (const [key, value] of Object.entries(feature.properties)) {
            if (key === 'style' || key === 'intersection_type') continue;
            
            if (value !== null && value !== undefined && value !== '') {
                if (importantProps.includes(key)) {
                    popupContent += this.formatPropertyRow(key, value, true);
                } else {
                    otherProps.push([key, value]);
                }
            }
        }

        // Ajouter les autres propriétés
        if (otherProps.length > 0) {
            popupContent += '<hr class="my-2">';
            otherProps.slice(0, 5).forEach(([key, value]) => {
                popupContent += this.formatPropertyRow(key, value, false);
            });

            if (otherProps.length > 5) {
                popupContent += `<small class="text-muted">... et ${otherProps.length - 5} autres propriétés</small>`;
            }
        }

        popupContent += '</div>';

        layer.bindPopup(popupContent, {
            maxWidth: 300,
            className: 'custom-popup'
        });
    }

    /**
     * Marqueur par défaut pour les points
     * @param {Object} feature - Feature GeoJSON
     * @param {L.LatLng} latlng - Coordonnées
     */
    getDefaultMarker(feature, latlng) {
        const intersectionType = feature.properties.intersection_type || 'original';
        const color = this.colorPalette[intersectionType] || '#3388ff';

        return L.circleMarker(latlng, {
            radius: 8,
            fillColor: color,
            color: '#fff',
            weight: 2,
            opacity: 1,
            fillOpacity: 0.8
        });
    }

    /**
     * Formate une ligne de propriété pour le popup
     * @param {string} key - Clé de la propriété
     * @param {*} value - Valeur de la propriété
     * @param {boolean} important - Si la propriété est importante
     */
    formatPropertyRow(key, value, important = false) {
        const label = this.formatPropertyLabel(key);
        const formattedValue = this.formatPropertyValue(key, value);
        const weight = important ? 'fw-bold' : '';

        return `
            <div class="d-flex justify-content-between align-items-center mb-1">
                <span class="text-muted ${weight}">${label}:</span>
                <span class="${weight}">${formattedValue}</span>
            </div>
        `;
    }

    /**
     * Formate le label d'une propriété
     * @param {string} key - Clé de la propriété
     */
    formatPropertyLabel(key) {
        const labelMap = {
            'source_names': 'Sources',
            'intersection_type': 'Type',
            'similarity': 'Similarité',
            'area': 'Superficie',
            'original_source_name': 'Source origine'
        };

        return labelMap[key] || key.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
    }

    /**
     * Formate la valeur d'une propriété
     * @param {string} key - Clé de la propriété
     * @param {*} value - Valeur à formater
     */
    formatPropertyValue(key, value) {
        if (key === 'similarity' && typeof value === 'number') {
            return `${(value * 100).toFixed(1)}%`;
        }
        
        if (key === 'area' && typeof value === 'number') {
            if (value > 1000000) {
                return `${(value / 1000000).toFixed(2)} km²`;
            } else {
                return `${value.toFixed(0)} m²`;
            }
        }

        if (typeof value === 'string' && value.length > 50) {
            return value.substring(0, 47) + '...';
        }

        return String(value);
    }

    /**
     * Obtient le label français pour un type d'intersection
     * @param {string} type - Type d'intersection
     */
    getTypeLabel(type) {
        const typeLabels = {
            'intersection': 'Intersection',
            'difference': 'Zone unique',
            'original': 'Zone originale',
            'filtered': 'Zone filtrée'
        };

        return typeLabels[type] || type;
    }

    /**
     * Ajuste la vue de la carte sur une couche
     * @param {string} mapId - ID de la carte
     * @param {string} layerName - Nom de la couche
     */
    fitToLayer(mapId, layerName) {
        const map = this.maps.get(mapId);
        const mapLayers = this.layers.get(mapId);
        
        if (!map || !mapLayers) return;

        const layer = mapLayers.get(layerName);
        if (layer) {
            map.fitBounds(layer.getBounds(), { padding: [20, 20] });
        }
    }

    /**
     * Ajuste la vue de la carte sur toutes les couches
     * @param {string} mapId - ID de la carte
     */
    fitToAllLayers(mapId) {
        const map = this.maps.get(mapId);
        const mapLayers = this.layers.get(mapId);
        
        if (!map || !mapLayers) return;

        const group = new L.featureGroup();
        mapLayers.forEach(layer => {
            group.addLayer(layer);
        });

        if (group.getLayers().length > 0) {
            map.fitBounds(group.getBounds(), { padding: [20, 20] });
        }
    }

    /**
     * Supprime une couche de la carte
     * @param {string} mapId - ID de la carte
     * @param {string} layerName - Nom de la couche
     */
    removeLayer(mapId, layerName) {
        const map = this.maps.get(mapId);
        const mapLayers = this.layers.get(mapId);
        
        if (!map || !mapLayers) return;

        const layer = mapLayers.get(layerName);
        if (layer) {
            map.removeLayer(layer);
            mapLayers.delete(layerName);
        }
    }

    /**
     * Efface toutes les couches d'une carte
     * @param {string} mapId - ID de la carte
     */
    clearLayers(mapId) {
        const map = this.maps.get(mapId);
        const mapLayers = this.layers.get(mapId);
        
        if (!map || !mapLayers) return;

        mapLayers.forEach((layer, name) => {
            map.removeLayer(layer);
        });
        mapLayers.clear();
    }

    /**
     * Ajoute une légende à la carte
     * @param {string} mapId - ID de la carte
     * @param {Object} legendData - Données de la légende
     * @param {string} position - Position de la légende
     */
    addLegend(mapId, legendData, position = 'bottomleft') {
        const map = this.maps.get(mapId);
        if (!map) return;

        // Supprimer l'ancienne légende si elle existe
        const existingLegend = this.controls.get(`${mapId}_legend`);
        if (existingLegend) {
            map.removeControl(existingLegend);
        }

        // Créer la nouvelle légende
        const legend = L.control({ position });
        
        legend.onAdd = function() {
            const div = L.DomUtil.create('div', 'map-legend');
            div.innerHTML = '<h6 class="mb-2">Légende</h6>';

            for (const [type, data] of Object.entries(legendData)) {
                div.innerHTML += `
                    <div class="legend-item">
                        <div class="legend-color" style="background-color: ${data.color}"></div>
                        <span>${data.label} (${data.count})</span>
                    </div>
                `;
            }

            return div;
        };

        legend.addTo(map);
        this.controls.set(`${mapId}_legend`, legend);

        return legend;
    }

    /**
     * Ajoute des contrôles personnalisés à la carte
     * @param {string} mapId - ID de la carte
     * @param {Array} controls - Liste des contrôles à ajouter
     */
    addCustomControls(mapId, controls = []) {
        const map = this.maps.get(mapId);
        if (!map) return;

        controls.forEach(controlConfig => {
            const control = L.control({ position: controlConfig.position || 'topright' });
            
            control.onAdd = function() {
                const div = L.DomUtil.create('div', 'leaflet-control-custom');
                div.innerHTML = controlConfig.html;
                
                if (controlConfig.onClick) {
                    div.addEventListener('click', controlConfig.onClick);
                }

                return div;
            };

            control.addTo(map);
            
            if (controlConfig.name) {
                this.controls.set(`${mapId}_${controlConfig.name}`, control);
            }
        });
    }

    /**
     * Met à jour le style d'une couche
     * @param {string} mapId - ID de la carte
     * @param {string} layerName - Nom de la couche
     * @param {Function|Object} newStyle - Nouveau style
     */
    updateLayerStyle(mapId, layerName, newStyle) {
        const mapLayers = this.layers.get(mapId);
        if (!mapLayers) return;

        const layer = mapLayers.get(layerName);
        if (layer) {
            layer.setStyle(newStyle);
        }
    }

    /**
     * Filtre une couche selon des critères
     * @param {string} mapId - ID de la carte
     * @param {string} layerName - Nom de la couche
     * @param {Function} filterFunction - Fonction de filtrage
     */
    filterLayer(mapId, layerName, filterFunction) {
        const map = this.maps.get(mapId);
        const mapLayers = this.layers.get(mapId);
        
        if (!map || !mapLayers) return;

        const layer = mapLayers.get(layerName);
        if (layer) {
            layer.eachLayer(function(featureLayer) {
                const feature = featureLayer.feature;
                if (filterFunction(feature)) {
                    featureLayer.setStyle({ opacity: 1, fillOpacity: 0.6 });
                } else {
                    featureLayer.setStyle({ opacity: 0.3, fillOpacity: 0.1 });
                }
            });
        }
    }

    /**
     * Exporte la carte en image
     * @param {string} mapId - ID de la carte
     * @param {Object} options - Options d'export
     */
    exportMapAsImage(mapId, options = {}) {
        const map = this.maps.get(mapId);
        if (!map) return;

        // Cette fonctionnalité nécessite une librairie supplémentaire comme leaflet-image
        console.log('Export d\'image non implémenté - nécessite leaflet-image');
    }

    /**
     * Obtient les limites géographiques de toutes les couches
     * @param {string} mapId - ID de la carte
     */
    getAllLayersBounds(mapId) {
        const mapLayers = this.layers.get(mapId);
        if (!mapLayers) return null;

        const group = new L.featureGroup();
        mapLayers.forEach(layer => {
            group.addLayer(layer);
        });

        return group.getLayers().length > 0 ? group.getBounds() : null;
    }
}

// ============================================================================
// Gestionnaire d'événements et utilitaires
// ============================================================================

/**
 * Gestionnaire d'événements pour l'application ZADA
 */
class ZADAEventManager {
    constructor(mapManager) {
        this.mapManager = mapManager;
        this.setupGlobalEventListeners();
    }

    /**
     * Configure les écouteurs d'événements globaux
     */
    setupGlobalEventListeners() {
        // Gestion du redimensionnement
        window.addEventListener('resize', this.handleWindowResize.bind(this));
        
        // Gestion des raccourcis clavier
        document.addEventListener('keydown', this.handleKeyboardShortcuts.bind(this));
        
        // Gestion du drag & drop pour les fichiers
        this.setupDragAndDrop();
    }

    /**
     * Gère le redimensionnement de la fenêtre
     */
    handleWindowResize() {
        // Invalider la taille de toutes les cartes
        this.mapManager.maps.forEach((map, mapId) => {
            setTimeout(() => {
                map.invalidateSize();
            }, 100);
        });
    }

    /**
     * Gère les raccourcis clavier
     * @param {KeyboardEvent} event - Événement clavier
     */
    handleKeyboardShortcuts(event) {
        // Échapper pour fermer les popups
        if (event.key === 'Escape') {
            this.mapManager.maps.forEach(map => {
                map.closePopup();
            });
        }

        // Ctrl+R pour réinitialiser la vue
        if (event.ctrlKey && event.key === 'r') {
            event.preventDefault();
            const activeMapId = this.getActiveMapId();
            if (activeMapId) {
                this.mapManager.fitToAllLayers(activeMapId);
            }
        }
    }

    /**
     * Configure le drag & drop pour les fichiers
     */
    setupDragAndDrop() {
        const uploadAreas = document.querySelectorAll('.upload-area, .card-body');
        
        uploadAreas.forEach(area => {
            area.addEventListener('dragover', this.handleDragOver.bind(this));
            area.addEventListener('dragleave', this.handleDragLeave.bind(this));
            area.addEventListener('drop', this.handleFileDrop.bind(this));
        });
    }

    /**
     * Gère l'événement dragover
     * @param {DragEvent} event - Événement de drag
     */
    handleDragOver(event) {
        event.preventDefault();
        event.currentTarget.classList.add('dragover');
    }

    /**
     * Gère l'événement dragleave
     * @param {DragEvent} event - Événement de drag
     */
    handleDragLeave(event) {
        event.preventDefault();
        event.currentTarget.classList.remove('dragover');
    }

    /**
     * Gère le drop de fichiers
     * @param {DragEvent} event - Événement de drop
     */
    handleFileDrop(event) {
        event.preventDefault();
        event.currentTarget.classList.remove('dragover');
        
        const files = Array.from(event.dataTransfer.files);
        const validFiles = files.filter(file => 
            file.name.toLowerCase().endsWith('.shp') || 
            file.name.toLowerCase().endsWith('.geojson') ||
            file.name.toLowerCase().endsWith('.zip')
        );

        if (validFiles.length > 0) {
            console.log('Fichiers déposés:', validFiles.map(f => f.name));
            // Ici on pourrait déclencher l'upload automatique
            this.showNotification('Fichiers détectés', `${validFiles.length} fichier(s) prêt(s) à être traité(s)`, 'info');
        } else {
            this.showNotification('Fichiers non supportés', 'Seuls les fichiers .shp, .geojson et .zip sont acceptés', 'warning');
        }
    }

    /**
     * Obtient l'ID de la carte active
     */
    getActiveMapId() {
        // Simple heuristique basée sur la page courante
        if (document.getElementById('map')) return 'map';
        if (document.getElementById('nlpMap')) return 'nlpMap';
        return null;
    }

    /**
     * Affiche une notification
     * @param {string} title - Titre de la notification
     * @param {string} message - Message de la notification
     * @param {string} type - Type de notification (success, warning, error, info)
     */
    showNotification(title, message, type = 'info') {
        // Créer une notification toast Bootstrap
        const toastContainer = this.getOrCreateToastContainer();
        
        const toast = document.createElement('div');
        toast.className = `toast align-items-center text-white bg-${type === 'error' ? 'danger' : type} border-0`;
        toast.setAttribute('role', 'alert');
        toast.setAttribute('aria-live', 'assertive');
        toast.setAttribute('aria-atomic', 'true');

        toast.innerHTML = `
            <div class="d-flex">
                <div class="toast-body">
                    <strong>${title}</strong><br>
                    ${message}
                </div>
                <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>
            </div>
        `;

        toastContainer.appendChild(toast);

        // Initialiser et afficher le toast
        const bsToast = new bootstrap.Toast(toast, {
            autohide: true,
            delay: 5000
        });
        
        bsToast.show();

        // Nettoyer après fermeture
        toast.addEventListener('hidden.bs.toast', () => {
            toast.remove();
        });
    }

    /**
     * Obtient ou crée le conteneur de toasts
     */
    getOrCreateToastContainer() {
        let container = document.getElementById('toast-container');
        
        if (!container) {
            container = document.createElement('div');
            container.id = 'toast-container';
            container.className = 'toast-container position-fixed bottom-0 end-0 p-3';
            container.style.zIndex = '9999';
            document.body.appendChild(container);
        }

        return container;
    }
}

// ============================================================================
// Utilitaires pour les données géographiques
// ============================================================================

/**
 * Utilitaires pour manipuler les données géographiques
 */
class ZADAGeoUtils {
    /**
     * Calcule la superficie d'une feature en mètres carrés
     * @param {Object} feature - Feature GeoJSON
     */
    static calculateArea(feature) {
        if (!feature.geometry) return 0;
        
        // Utiliser la librairie turf.js si disponible, sinon approximation simple
        if (typeof turf !== 'undefined' && turf.area) {
            return turf.area(feature);
        }
        
        // Approximation simple pour les polygones
        if (feature.geometry.type === 'Polygon') {
            const coords = feature.geometry.coordinates[0];
            return this.polygonArea(coords);
        }
        
        return 0;
    }

    /**
     * Calcule approximativement l'aire d'un polygone
     * @param {Array} coords - Coordonnées du polygone
     */
    static polygonArea(coords) {
        let area = 0;
        const n = coords.length;
        
        for (let i = 0; i < n - 1; i++) {
            area += coords[i][0] * coords[i + 1][1];
            area -= coords[i + 1][0] * coords[i][1];
        }
        
        return Math.abs(area) / 2;
    }

    /**
     * Calcule le centroïde d'une feature
     * @param {Object} feature - Feature GeoJSON
     */
    static getCentroid(feature) {
        if (!feature.geometry) return null;
        
        if (typeof turf !== 'undefined' && turf.centroid) {
            const centroid = turf.centroid(feature);
            return centroid.geometry.coordinates;
        }
        
        // Approximation simple
        if (feature.geometry.type === 'Polygon') {
            const coords = feature.geometry.coordinates[0];
            const centroid = this.polygonCentroid(coords);
            return [centroid.lng, centroid.lat];
        }
        
        return null;
    }

    /**
     * Calcule le centroïde d'un polygone
     * @param {Array} coords - Coordonnées du polygone
     */
    static polygonCentroid(coords) {
        let x = 0, y = 0;
        const n = coords.length - 1; // Exclure le point de fermeture
        
        for (let i = 0; i < n; i++) {
            x += coords[i][0];
            y += coords[i][1];
        }
        
        return { lng: x / n, lat: y / n };
    }

    /**
     * Convertit des coordonnées en format lisible
     * @param {Array} coordinates - [longitude, latitude]
     */
    static formatCoordinates(coordinates) {
        if (!coordinates || coordinates.length < 2) return 'N/A';
        
        const lng = coordinates[0].toFixed(6);
        const lat = coordinates[1].toFixed(6);
        
        return `${lat}°N, ${lng}°E`;
    }

    /**
     * Vérifie si deux features se chevauchent
     * @param {Object} feature1 - Première feature
     * @param {Object} feature2 - Deuxième feature
     */
    static featuresOverlap(feature1, feature2) {
        if (typeof turf !== 'undefined' && turf.intersect) {
            const intersection = turf.intersect(feature1, feature2);
            return intersection !== null;
        }
        
        // Fallback simple: vérifier si les bounding boxes se chevauchent
        const bbox1 = this.getBoundingBox(feature1);
        const bbox2 = this.getBoundingBox(feature2);
        
        return this.boundingBoxesOverlap(bbox1, bbox2);
    }

    /**
     * Obtient la bounding box d'une feature
     * @param {Object} feature - Feature GeoJSON
     */
    static getBoundingBox(feature) {
        if (typeof turf !== 'undefined' && turf.bbox) {
            return turf.bbox(feature);
        }
        
        // Implementation simple
        const coords = this.getAllCoordinates(feature.geometry);
        if (coords.length === 0) return null;
        
        let minLng = coords[0][0], maxLng = coords[0][0];
        let minLat = coords[0][1], maxLat = coords[0][1];
        
        coords.forEach(coord => {
            minLng = Math.min(minLng, coord[0]);
            maxLng = Math.max(maxLng, coord[0]);
            minLat = Math.min(minLat, coord[1]);
            maxLat = Math.max(maxLat, coord[1]);
        });
        
        return [minLng, minLat, maxLng, maxLat];
    }

    /**
     * Extrait toutes les coordonnées d'une géométrie
     * @param {Object} geometry - Géométrie GeoJSON
     */
    static getAllCoordinates(geometry) {
        const coords = [];
        
        function extractCoords(geom) {
            switch (geom.type) {
                case 'Point':
                    coords.push(geom.coordinates);
                    break;
                case 'LineString':
                case 'MultiPoint':
                    geom.coordinates.forEach(coord => coords.push(coord));
                    break;
                case 'Polygon':
                case 'MultiLineString':
                    geom.coordinates.forEach(ring => {
                        ring.forEach(coord => coords.push(coord));
                    });
                    break;
                case 'MultiPolygon':
                    geom.coordinates.forEach(polygon => {
                        polygon.forEach(ring => {
                            ring.forEach(coord => coords.push(coord));
                        });
                    });
                    break;
                case 'GeometryCollection':
                    geom.geometries.forEach(extractCoords);
                    break;
            }
        }
        
        extractCoords(geometry);
        return coords;
    }

    /**
     * Vérifie si deux bounding boxes se chevauchent
     * @param {Array} bbox1 - Première bounding box [minLng, minLat, maxLng, maxLat]
     * @param {Array} bbox2 - Deuxième bounding box
     */
    static boundingBoxesOverlap(bbox1, bbox2) {
        if (!bbox1 || !bbox2) return false;
        
        return !(bbox1[2] < bbox2[0] || // bbox1 à gauche de bbox2
                bbox1[0] > bbox2[2] || // bbox1 à droite de bbox2
                bbox1[3] < bbox2[1] || // bbox1 au-dessus de bbox2
                bbox1[1] > bbox2[3]);  // bbox1 en-dessous de bbox2
    }
}

// ============================================================================
// Initialisation globale
// ============================================================================

// Variables globales
let zadaMapManager;
let zadaEventManager;

// Initialisation au chargement du DOM
document.addEventListener('DOMContentLoaded', function() {
    // Créer les gestionnaires globaux
    zadaMapManager = new ZADAMapManager();
    zadaEventManager = new ZADAEventManager(zadaMapManager);
    
    // Initialiser les cartes présentes sur la page
    initializePageMaps();
    
    // Configuration globale de Leaflet
    configureLeafletGlobals();
});

/**
 * Initialise les cartes présentes sur la page courante
 */
function initializePageMaps() {
    // Carte de fusion SIG
    const mapElement = document.getElementById('map');
    if (mapElement) {
        const fusionMap = zadaMapManager.initializeMap('map');
        
        // Ajouter les contrôles personnalisés
        zadaMapManager.addCustomControls('map', [
            {
                name: 'reset',
                position: 'topright',
                html: '<button class="map-control-btn" title="Réinitialiser la vue"><i class="fas fa-home"></i></button>',
                onClick: () => zadaMapManager.fitToAllLayers('map')
            },
            {
                name: 'fullscreen',
                position: 'topright', 
                html: '<button class="map-control-btn" title="Plein écran"><i class="fas fa-expand"></i></button>',
                onClick: () => toggleMapFullscreen('map')
            }
        ]);
    }
    
    // Carte NLP
    const nlpMapElement = document.getElementById('nlpMap');
    if (nlpMapElement) {
        const nlpMap = zadaMapManager.initializeMap('nlpMap');
        
        // Ajouter les contrôles pour la carte NLP
        zadaMapManager.addCustomControls('nlpMap', [
            {
                name: 'reset',
                position: 'topright',
                html: '<button class="map-control-btn" title="Réinitialiser la vue"><i class="fas fa-home"></i></button>',
                onClick: () => zadaMapManager.fitToAllLayers('nlpMap')
            }
        ]);
    }
}

/**
 * Configure les paramètres globaux de Leaflet
 */
function configureLeafletGlobals() {
    // Configuration des icônes par défaut
    delete L.Icon.Default.prototype._getIconUrl;
    L.Icon.Default.mergeOptions({
        iconRetinaUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/images/marker-icon-2x.png',
        iconUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/images/marker-icon.png',
        shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/images/marker-shadow.png',
    });
}

/**
 * Bascule le mode plein écran pour une carte
 * @param {string} mapId - ID de la carte
 */
function toggleMapFullscreen(mapId) {
    const mapContainer = document.getElementById(mapId).parentElement;
    
    if (!document.fullscreenElement) {
        mapContainer.requestFullscreen().then(() => {
            // Redimensionner la carte après l'entrée en plein écran
            setTimeout(() => {
                const map = zadaMapManager.maps.get(mapId);
                if (map) {
                    map.invalidateSize();
                }
            }, 100);
        });
    } else {
        document.exitFullscreen();
    }
}

// Exporter les classes et fonctions principales pour utilisation globale
window.ZADAMapManager = ZADAMapManager;
window.ZADAEventManager = ZADAEventManager;
window.ZADAGeoUtils = ZADAGeoUtils;
window.zadaMapManager = zadaMapManager;
window.zadaEventManager = zadaEventManager;