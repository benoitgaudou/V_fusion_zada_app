// ============================================================================
// static/js/leaflet_script.js - Version simplifiée pour cartographie thématique
// ============================================================================

class ZADAMapManager {
    constructor() {
        this.maps = new Map();
        this.layers = new Map();
        this.controls = new Map();

        // plus de palette par type d'intersection – on laisse un style neutre
        this.defaultCenter = [14.0583, 108.2772];
        this.defaultZoom = 6;

        // Thématique
        this.thematicLayer = null;
        this.currentThematicField = null;
        this.availableFields = [];
    }

    initializeMap(containerId, options = {}) {
        const map = L.map(containerId, {
            center: options.center || this.defaultCenter,
            zoom: options.zoom || this.defaultZoom,
            zoomControl: true,
            attributionControl: true
        });

        this.addBaseLayers(map);

        this.maps.set(containerId, map);
        this.layers.set(containerId, new Map());

        return map;
    }

    addBaseLayers(map) {
        const baseLayers = {
            'OpenStreetMap': L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
                attribution: '© OpenStreetMap contributors',
                maxZoom: 19
            }),
            'CartoDB Positron': L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', {
                attribution: '© CartoDB © OpenStreetMap contributors',
                maxZoom: 19
            })
        };
        baseLayers['OpenStreetMap'].addTo(map);
        L.control.layers(baseLayers, {}, {
            collapsed: false,
            position: 'bottomleft'
        }).addTo(map);
    }

    // Style neutre (utilisé si la feature n’a pas de style)
    getDefaultStyle(/* feature */) {
        return {
            color: '#3388ff',
            fillColor: '#3388ff',
            fillOpacity: 0.5,
            weight: 2,
            opacity: 0.9
        };
    }

    // Style thématique : on respecte feature.properties.style si présent
    getThematicStyle(feature) {
        if (feature?.properties?.style) return feature.properties.style;
        return this.getDefaultStyle(feature);
    }

    getThematicPopup(feature, layer) {
        if (!feature?.properties) return;
        const p = feature.properties;
        const rows = [
            p.thematic_field ? ['Champ', p.thematic_field] : null,
            p.thematic_value ? ['Valeur', p.thematic_value] : null,
            p.thematic_label ? ['Classe', p.thematic_label] : null,
            p.source_names ? ['Sources', p.source_names] : null,
        ].filter(Boolean);

        let html = `<div class="thematic-popup"><h6><i class="fas fa-palette me-1"></i><strong>Cartographie thématique</strong></h6>`;
        if (rows.length) {
            html += `<table class="table table-sm table-borderless mb-0">`;
            rows.forEach(([k, v]) => {
                html += `<tr><td><strong>${k}:</strong></td><td>${String(v)}</td></tr>`;
            });
            html += `</table>`;
        }
        html += `</div>`;

        layer.bindPopup(html, { maxWidth: 300, className: 'thematic-popup' });
    }

    // ------------------------ Thématique ------------------------

    initializeThematicMapping() {
        const fieldSelect = document.getElementById('thematic-field-select');
        const paletteSelect = document.getElementById('color-palette-select');
        const generateBtn  = document.getElementById('generate-thematic-map');

        if (!fieldSelect || !paletteSelect || !generateBtn) return;

        fieldSelect.addEventListener('change', () => {
            generateBtn.disabled = !fieldSelect.value;
            this.hideThematicLegend();
            this.hideStatus();
            this.showFieldPreview(fieldSelect.value);
        });

        generateBtn.addEventListener('click', () => this.generateThematicMap());

        // Charger la liste des champs (provenant du résultat de fusion côté serveur)
        this.loadAvailableFields();
    }

    loadAvailableFields() {
        // Nouvelle route simplifiée
        fetch('/api/fields')
            .then(r => r.json())
            .then(data => {
                if (!data.success) throw new Error(data.error || 'Erreur /api/fields');
                this.availableFields = data.fields || [];
                this.populateFieldSelect(this.availableFields);

                // Afficher la section si au moins un champ
                if (this.availableFields.length) {
                    const sec = document.getElementById('thematic-mapping-section');
                    if (sec) sec.style.display = 'block';
                }
            })
            .catch(err => {
                console.error(err);
                this.showStatus('danger', 'Impossible de charger la liste des champs');
            });
    }

    populateFieldSelect(fields) {
        const select = document.getElementById('thematic-field-select');
        if (!select) return;
        select.innerHTML = '<option value="">Sélectionner un champ...</option>';

        fields.forEach(f => {
            const opt = document.createElement('option');
            opt.value = f.name;
            opt.textContent = f.label || f.name;
            opt.title = `${f.type || 'type inconnu'} • ${f.unique_count ?? '?'} valeurs`;
            select.appendChild(opt);
        });
    }

    showFieldPreview(fieldName) {
        if (!fieldName) return;
        fetch(`/api/field-analysis/${encodeURIComponent(fieldName)}`)
            .then(r => r.json())
            .then(data => {
                if (!data.success) return;
                this.renderFieldPreview(data.analysis);
            })
            .catch(() => {/* silencieux */});
    }

    renderFieldPreview(analysis) {
        const preview = document.getElementById('field-preview');
        const content = document.getElementById('field-preview-content');
        if (!preview || !content) return;

        let html = `
            <div class="field-preview-stats">
                <span class="field-preview-stat"><strong>Type:</strong> ${analysis.data_type}</span>
                <span class="field-preview-stat"><strong>Uniques:</strong> ${analysis.unique_count}</span>
                <span class="field-preview-stat"><strong>Valides:</strong> ${analysis.valid_values ?? (analysis.total_values - (analysis.null_values||0))}/${analysis.total_values}</span>
        `;
        if ((analysis.null_values || 0) > 0) {
            html += `<span class="field-preview-stat" style="background:#fff3cd;border-color:#ffeaa7;"><strong>Nulles:</strong> ${analysis.null_values}</span>`;
        }
        html += `</div>`;

        if (analysis.data_type?.includes('numeric')) {
            html += `
                <div class="field-preview-values mt-2">
                    <strong>Stats:</strong> 
                    Min: ${toNum(analysis.min_value)}, 
                    Max: ${toNum(analysis.max_value)}, 
                    Moy: ${toNum(analysis.mean_value)}
                </div>
            `;
        }

        if (analysis.sample_values?.length) {
            const vals = analysis.sample_values.slice(0, 5).join(', ');
            html += `<div class="field-preview-values mt-2"><strong>Échantillon:</strong> ${vals}${analysis.sample_values.length>5?'...':''}</div>`;
        }

        content.innerHTML = html;
        preview.style.display = 'block';

        function toNum(v){ return (typeof v==='number') ? v.toFixed(2) : (v ?? 'N/A'); }
    }

    generateThematicMap() {
        const field = document.getElementById('thematic-field-select')?.value;
        const palette = document.getElementById('color-palette-select')?.value || 'default';
        if (!field) {
            this.showStatus('warning', 'Veuillez sélectionner un champ'); 
            return;
        }

        this.showStatus('info', `Génération de la carte thématique pour « ${field} »...`, true);

        fetch(`/api/thematic-map/${encodeURIComponent(field)}?palette=${encodeURIComponent(palette)}`)
            .then(r => r.json())
            .then(data => {
                if (!data.success) throw new Error(data.error || 'Génération échouée');

                this.updateMapWithThematicData(data);

                // Légende
                if (data.legend) this.displayThematicLegend(data.legend);

                // Fit bounds
                const map = this.maps.get('map');
                if (map && data.map_bounds) map.fitBounds(data.map_bounds, { padding: [10, 10] });

                this.currentThematicField = field;
                this.showStatus('success', 'Carte thématique générée !');
                this.injectExportButton(field, palette);
            })
            .catch(err => {
                console.error(err);
                this.showStatus('danger', `Erreur: ${err.message}`);
            });
    }

    updateMapWithThematicData(thematicData) {
        const map = this.maps.get('map');
        if (!map) return;

        if (this.thematicLayer && map.hasLayer(this.thematicLayer)) {
            map.removeLayer(this.thematicLayer);
        }
        if (!thematicData.geojson || !thematicData.geojson.features?.length) {
            this.showStatus('warning', 'Aucune entité à afficher');
            return;
        }

        this.thematicLayer = L.geoJSON(thematicData.geojson, {
            style: this.getThematicStyle.bind(this),
            onEachFeature: this.getThematicPopup.bind(this),
        }).addTo(map);

        const mapLayers = this.layers.get('map');
        if (mapLayers) mapLayers.set('thematic', this.thematicLayer);
    }

    displayThematicLegend(legend) {
        const box = document.getElementById('thematic-legend');
        const content = document.getElementById('thematic-legend-content');
        if (!box || !content) return;

        if (!legend.items?.length) { this.hideThematicLegend(); return; }

        let html = '';
        if (legend.type === 'discrete') {
            html += '<h6 class="mb-2">Légende par valeurs :</h6>';
            legend.items.forEach(it => {
                html += `
                    <div class="thematic-legend-item">
                        <div class="thematic-color-box" style="background-color:${it.color};"></div>
                        <span class="thematic-label">${it.label}</span>
                        ${it.count ? `<span class="thematic-count">${it.count}</span>` : ''}
                    </div>`;
            });
        } else if (legend.type === 'continuous' || legend.type === 'gradient') {
            html += '<h6 class="mb-2">Légende par classes :</h6>';
            legend.items.forEach(it => {
                html += `
                    <div class="thematic-legend-item">
                        <div class="thematic-color-box" style="background-color:${it.color};"></div>
                        <span class="thematic-label">${it.label}</span>
                    </div>`;
            });
            if (legend.min_value != null && legend.max_value != null) {
                html += `<div class="mt-2"><small class="text-muted">Plage: ${Number(legend.min_value).toFixed(2)} - ${Number(legend.max_value).toFixed(2)}</small></div>`;
            }
        }

        content.innerHTML = html;
        box.style.display = 'block';
    }

    hideThematicLegend() {
        const box = document.getElementById('thematic-legend');
        if (box) box.style.display = 'none';
    }

    showStatus(kind, msg, spin=false) {
        const div = document.getElementById('thematic-generation-status');
        if (!div) return;
        const icon = spin ? 'fas fa-spinner fa-spin' :
            (kind==='success' ? 'fas fa-check-circle' :
            kind==='warning' ? 'fas fa-exclamation-triangle' :
            kind==='danger'  ? 'fas fa-exclamation-triangle' : 'fas fa-info-circle');

        div.innerHTML = `<div class="alert alert-${kind} mb-0"><i class="${icon} me-2"></i>${msg}</div>`;
        div.style.display = 'block';
        if (kind === 'success') setTimeout(()=>{ div.style.display='none'; }, 4000);
    }

    hideStatus(){ const d=document.getElementById('thematic-generation-status'); if(d) d.style.display='none'; }

    injectExportButton(fieldName, paletteName) {
        const statusDiv = document.getElementById('thematic-generation-status');
        const alert = statusDiv?.querySelector('.alert.alert-success');
        if (!alert) return;

        const btn = document.createElement('button');
        btn.className = 'btn btn-sm btn-outline-success ms-2';
        btn.innerHTML = '<i class="fas fa-download me-1"></i>Exporter';
        btn.onclick = () => this.exportThematicMap(fieldName, paletteName);
        alert.appendChild(btn);
    }

    exportThematicMap(fieldName, paletteName) {
        fetch(`/api/export-thematic-map/${encodeURIComponent(fieldName)}?palette=${encodeURIComponent(paletteName)}`)
            .then(r => r.json())
            .then(data => {
                if (!data.success) throw new Error(data.error || 'Export échoué');
                const a = document.createElement('a');
                a.href = data.download_url;
                a.download = data.filename;
                document.body.appendChild(a);
                a.click();
                a.remove();
                this.showStatus('success', `Fichier exporté: ${data.filename}`);
            })
            .catch(err => this.showStatus('danger', `Erreur export: ${err.message}`));
    }

    fitToAllLayers(mapId) {
        const map = this.maps.get(mapId);
        const mapLayers = this.layers.get(mapId);
        if (!map || !mapLayers) return;

        const group = new L.featureGroup();
        mapLayers.forEach(layer => layer && group.addLayer(layer));
        if (group.getLayers().length) map.fitBounds(group.getBounds(), { padding: [20, 20] });
    }

    clearLayers(mapId) {
        const map = this.maps.get(mapId);
        const mapLayers = this.layers.get(mapId);
        if (!map || !mapLayers) return;
        mapLayers.forEach(layer => map.removeLayer(layer));
        mapLayers.clear();
        this.thematicLayer = null;
        this.hideThematicLegend();
        this.hideStatus();
        this.currentThematicField = null;
    }
}

// ------------------------ Styles intégrés minimes ------------------------
function addThematicStyles() {
    if (document.getElementById('thematic-styles')) return;
    const style = document.createElement('style');
    style.id = 'thematic-styles';
    style.textContent = `
    .thematic-legend-item{display:flex;align-items:center;margin-bottom:8px;padding:4px 8px;border-radius:4px;background:#f8f9fa;cursor:pointer}
    .thematic-legend-item:hover{background:#e9ecef;transform:translateX(2px)}
    .thematic-color-box{width:20px;height:20px;border-radius:3px;margin-right:10px;border:1px solid #dee2e6;flex-shrink:0}
    .thematic-label{font-size:.9em;font-weight:500}
    .thematic-count{margin-left:auto;font-size:.8em;color:#6c757d;background:#e9ecef;padding:2px 6px;border-radius:10px}
    .field-preview-stat{display:inline-block;background:#e3f2fd;padding:4px 8px;margin:2px 4px;border-radius:12px;font-size:.85em;border:1px solid #bbdefb}
    .thematic-popup{max-width:300px}
    .thematic-popup .table td{padding:.25rem;border:none}
    .thematic-popup .table td:first-child{width:40%;color:#6c757d}
    `;
    document.head.appendChild(style);
}

// ------------------------ Initialisation page ------------------------
let zadaMapManager;

document.addEventListener('DOMContentLoaded', () => {
    addThematicStyles();

    // Config icônes Leaflet
    delete L.Icon.Default.prototype._getIconUrl;
    L.Icon.Default.mergeOptions({
        iconRetinaUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/images/marker-icon-2x.png',
        iconUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/images/marker-icon.png',
        shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/images/marker-shadow.png',
    });

    zadaMapManager = new ZADAMapManager();

    if (document.getElementById('map')) {
        zadaMapManager.initializeMap('map');

        // Contrôles simples
        const resetBtn = document.getElementById('resetViewBtn');
        if (resetBtn) resetBtn.addEventListener('click', () => zadaMapManager.fitToAllLayers('map'));

        const toggleLegendBtn = document.getElementById('toggleLegendBtn');
        if (toggleLegendBtn) toggleLegendBtn.addEventListener('click', () => {
            const box = document.getElementById('thematic-legend');
            if (box) box.style.display = (box.style.display === 'none' ? 'block' : 'none');
        });
    }

    // Activer la carto thématique si la section existe (page fusion_sig)
    if (document.getElementById('thematic-mapping-section')) {
        zadaMapManager.initializeThematicMapping();
    }

    // Raccourcis
    document.addEventListener('keydown', (e) => {
        if (e.ctrlKey && e.key === 'r') {
            e.preventDefault();
            zadaMapManager.fitToAllLayers('map');
        }
        if (e.key === 'Escape') {
            const map = zadaMapManager?.maps.get('map');
            if (map) map.closePopup();
        }
    });
});

// Expose minimal utils si besoin
window.zadaMapManager = () => zadaMapManager;
