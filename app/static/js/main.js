// ===== static/js/main.js =====
/**
 * Utilitaires JavaScript principaux pour l'application Fusion SIG
 */

// Configuration globale
window.FusionSIG = {
    apiUrl: '',
    sessionId: null,
    currentMap: null,
    currentLayer: null
};

// Utilitaires généraux
const Utils = {
    
    /**
     * Affiche un message toast
     */
    showToast: function(message, type = 'info', duration = 5000) {
        // Créer le conteneur toast s'il n'existe pas
        let toastContainer = document.getElementById('toast-container');
        if (!toastContainer) {
            toastContainer = document.createElement('div');
            toastContainer.id = 'toast-container';
            toastContainer.className = 'toast-container position-fixed top-0 end-0 p-3';
            toastContainer.style.zIndex = '9999';
            document.body.appendChild(toastContainer);
        }

        // Créer le toast
        const toastId = 'toast-' + Date.now();
        const toastHtml = `
            <div id="${toastId}" class="toast align-items-center text-white bg-${type}" role="alert">
                <div class="d-flex">
                    <div class="toast-body">
                        ${message}
                    </div>
                    <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>
                </div>
            </div>
        `;
        
        toastContainer.insertAdjacentHTML('beforeend', toastHtml);
        
        // Initialiser et afficher le toast
        const toastElement = document.getElementById(toastId);
        const toast = new bootstrap.Toast(toastElement, {
            autohide: true,
            delay: duration
        });
        toast.show();
        
        // Nettoyer après fermeture
        toastElement.addEventListener('hidden.bs.toast', function() {
            toastElement.remove();
        });
    },
    
    /**
     * Affiche un modal avec un message
     */
    showModal: function(title, body, type = 'info') {
        const modal = document.getElementById('messageModal');
        if (!modal) return;
        
        const titleEl = document.getElementById('messageModalTitle');
        const bodyEl = document.getElementById('messageModalBody');
        
        if (titleEl) titleEl.textContent = title;
        if (bodyEl) bodyEl.innerHTML = body;
        
        // Changer la couleur de l'en-tête selon le type
        const header = modal.querySelector('.modal-header');
        header.className = `modal-header bg-${type} text-white`;
        
        const bootstrapModal = new bootstrap.Modal(modal);
        bootstrapModal.show();
    },
    
    /**
     * Formate la taille des fichiers
     */
    formatFileSize: function(bytes) {
        if (bytes === 0) return '0 B';
        const k = 1024;
        const sizes = ['B', 'KB', 'MB', 'GB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
    },
    
    /**
     * Formate les nombres avec des séparateurs
     */
    formatNumber: function(num) {
        return new Intl.NumberFormat('fr-FR').format(num);
    },
    
    /**
     * Débounce function
     */
    debounce: function(func, wait) {
        let timeout;
        return function executedFunction(...args) {
            const later = () => {
                clearTimeout(timeout);
                func(...args);
            };
            clearTimeout(timeout);
            timeout = setTimeout(later, wait);
        };
    },
    
    /**
     * Requête AJAX simplifiée
     */
    ajax: async function(url, options = {}) {
        const config = {
            method: 'GET',
            headers: {
                'Content-Type': 'application/json',
                'X-Requested-With': 'XMLHttpRequest'
            },
            ...options
        };
        
        try {
            const response = await fetch(url, config);
            const data = await response.json();
            
            if (!response.ok) {
                throw new Error(data.error || `HTTP ${response.status}`);
            }
            
            return data;
        } catch (error) {
            console.error('Erreur AJAX:', error);
            throw error;
        }
    }
};

// Gestionnaire de session
const SessionManager = {
    
    /**
     * Charge les informations de session
     */
    loadSessionInfo: async function() {
        try {
            const data = await Utils.ajax('/api/session-info');
            window.FusionSIG.sessionId = data.session_id;
            
            // Mettre à jour l'affichage
            const sessionInfo = document.getElementById('session-info');
            if (sessionInfo) {
                sessionInfo.textContent = data.session_id ? data.session_id.substring(0, 8) : 'N/A';
            }
            
            return data;
        } catch (error) {
            console.error('Erreur chargement session:', error);
            return null;
        }
    },
    
    /**
     * Nettoie la session
     */
    clearSession: async function() {
        try {
            await Utils.ajax('/fusion/clear-session', { method: 'POST' });
            Utils.showToast('Session nettoyée avec succès', 'success');
            
            // Recharger la page après un court délai
            setTimeout(() => {
                window.location.reload();
            }, 1000);
            
        } catch (error) {
            Utils.showToast('Erreur lors du nettoyage: ' + error.message, 'danger');
        }
    }
};

// Gestionnaire de cartes
const MapManager = {
    
    /**
     * Initialise une carte Leaflet
     */
    initMap: function(containerId, options = {}) {
        const defaultOptions = {
            center: [46.2276, 2.2137], // Centre de la France
            zoom: 6,
            zoomControl: true,
            attributionControl: true
        };
        
        const config = { ...defaultOptions, ...options };
        
        // Supprimer la carte existante si elle existe
        if (window.FusionSIG.currentMap) {
            window.FusionSIG.currentMap.remove();
        }
        
        // Créer la nouvelle carte
        const map = L.map(containerId, config);
        
        // Ajouter les couches de base
        const osm = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            attribution: '© OpenStreetMap contributors',
            maxZoom: 19
        });
        
        const satellite = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
            attribution: '© Esri',
            maxZoom: 19
        });
        
        // Ajouter OSM par défaut
        osm.addTo(map);
        
        // Contrôle des couches
        const baseMaps = {
            "OpenStreetMap": osm,
            "Satellite": satellite
        };
        
        L.control.layers(baseMaps).addTo(map);
        
        // Contrôle d'échelle
        L.control.scale({
            position: 'bottomleft',
            imperial: false
        }).addTo(map);
        
        // Stocker la référence
        window.FusionSIG.currentMap = map;
        
        return map;
    },
    
    /**
     * Ajoute des données GeoJSON à la carte
     */
    addGeoJsonLayer: function(geojsonData, options = {}) {
        const map = window.FusionSIG.currentMap;
        if (!map || !geojsonData) return null;
        
        // Supprimer la couche existante
        if (window.FusionSIG.currentLayer) {
            map.removeLayer(window.FusionSIG.currentLayer);
        }
        
        const defaultStyle = {
            color: '#3388ff',
            weight: 2,
            opacity: 1,
            fillColor: '#3388ff',
            fillOpacity: 0.2
        };
        
        const layer = L.geoJSON(geojsonData, {
            style: options.style || defaultStyle,
            onEachFeature: function(feature, layer) {
                if (feature.properties && options.createPopup !== false) {
                    const popupContent = MapManager.createPopupContent(feature.properties);
                    layer.bindPopup(popupContent);
                }
            }
        });
        
        layer.addTo(map);
        
        // Ajuster la vue sur les données
        if (geojsonData.features && geojsonData.features.length > 0) {
            map.fitBounds(layer.getBounds(), { padding: [20, 20] });
        }
        
        window.FusionSIG.currentLayer = layer;
        return layer;
    },
    
    /**
     * Crée le contenu HTML pour les popups
     */
    createPopupContent: function(properties) {
        let html = '<div class="popup-content">';
        
        for (const [key, value] of Object.entries(properties)) {
            if (key !== 'geometry' && value !== null && value !== undefined) {
                let displayValue = value;
                
                // Formater les nombres
                if (typeof value === 'number') {
                    displayValue = Utils.formatNumber(value);
                }
                
                // Limiter la longueur des chaînes
                if (typeof value === 'string' && value.length > 100) {
                    displayValue = value.substring(0, 100) + '...';
                }
                
                html += `<div class="mb-1"><strong>${key}:</strong> ${displayValue}</div>`;
            }
        }
        
        html += '</div>';
        return html;
    },
    
    /**
     * Applique un style basé sur un attribut
     */
    styleByAttribute: function(geojsonData, attribute, styleInfo) {
        if (!styleInfo || !attribute) return null;
        
        return function(feature) {
            const value = feature.properties[attribute];
            
            if (styleInfo.type === 'categorical') {
                const color = styleInfo.color_map[String(value)] || '#gray';
                return {
                    color: color,
                    weight: 2,
                    opacity: 1,
                    fillColor: color,
                    fillOpacity: 0.7
                };
            } else if (styleInfo.type === 'numeric') {
                // Gradient de couleur pour les valeurs numériques
                const normalizedValue = (value - styleInfo.min_value) / 
                                      (styleInfo.max_value - styleInfo.min_value);
                const color = MapManager.getColorFromGradient(normalizedValue, 'viridis');
                
                return {
                    color: color,
                    weight: 2,
                    opacity: 1,
                    fillColor: color,
                    fillOpacity: 0.7
                };
            }
            
            // Style par défaut
            return {
                color: '#3388ff',
                weight: 2,
                opacity: 1,
                fillColor: '#3388ff',
                fillOpacity: 0.2
            };
        };
    },
    
    /**
     * Génère une couleur basée sur un gradient
     */
    getColorFromGradient: function(value, scheme = 'viridis') {
        // Palette viridis simplifiée
        const viridis = [
            '#440154', '#482777', '#3f4a8a', '#31678e', '#26838f',
            '#1f9d8a', '#6cce5a', '#b6de2b', '#fee825'
        ];
        
        const index = Math.floor(value * (viridis.length - 1));
        const clampedIndex = Math.max(0, Math.min(viridis.length - 1, index));
        
        return viridis[clampedIndex];
    }
};

// Initialisation au chargement de la page
document.addEventListener('DOMContentLoaded', function() {
    // Charger les informations de session
    SessionManager.loadSessionInfo();
    
    // Initialiser les tooltips Bootstrap
    const tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
    tooltipTriggerList.map(function(tooltipTriggerEl) {
        return new bootstrap.Tooltip(tooltipTriggerEl);
    });
});

// Fonction globale pour charger les infos de session (appelée depuis base.html)
function loadSessionInfo() {
    SessionManager.loadSessionInfo();
}

// Exposer les utilitaires globalement
window.Utils = Utils;
window.SessionManager = SessionManager;
window.MapManager = MapManager;