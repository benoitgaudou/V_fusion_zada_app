// =============================
// static/js/leaflet_script.js 
// =============================

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
            'OpenTopoMap' : L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png', {
                maxZoom: 17,
                attribution: 'Map data: &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors, <a href="http://viewfinderpanoramas.org">SRTM</a> | Map style: &copy; <a href="https://opentopomap.org">OpenTopoMap</a> (<a href="https://creativecommons.org/licenses/by-sa/3.0/">CC-BY-SA</a>)'
            }),
            'Stadia_AlidadeSmoothDark': L.tileLayer('https://tiles.stadiamaps.com/tiles/alidade_smooth_dark/{z}/{x}/{y}{r}.{ext}', {
                minZoom: 0,
                maxZoom: 20,
                attribution: '&copy; <a href="https://www.stadiamaps.com/" target="_blank">Stadia Maps</a> &copy; <a href="https://openmaptiles.org/" target="_blank">OpenMapTiles</a> &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
                ext: 'png'
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
        const exportBtn    = document.getElementById('export-thematic-map');

        if (!fieldSelect || !paletteSelect || !generateBtn) return;

        fieldSelect.addEventListener('change', () => {
            generateBtn.disabled = !fieldSelect.value;
            if (exportBtn) exportBtn.disabled = true;
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

            if (data.legend) this.displayThematicLegend(data.legend);

            const map = this.maps.get('map');
            if (map && data.bounds) map.fitBounds(data.bounds, { padding: [10, 10] });

            this.currentThematicField = field;
            this.showStatus('success', 'Carte thématique générée !');

            // Active le bouton Export et branche l’action
            const exportBtn = document.getElementById('export-thematic-map');
            if (exportBtn) {
                exportBtn.disabled = false;
                exportBtn.onclick = () => this.exportThematicMap(field, palette);
            }
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
    const fmtSel = document.getElementById('export-format-select');
    const fmt = (fmtSel?.value || 'geojson').toLowerCase();

    const msgEl = document.getElementById('mapExportMsg');
    if (msgEl) { msgEl.textContent = 'Export en cours…'; }

    fetch('/api/map/export', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
        fmt, field_name: fieldName, palette: paletteName, layer: 'zada_thematic'
        })
    })
    .then(async res => {
        if (!res.ok) {
        let err = 'HTTP ' + res.status;
        try { const j = await res.json(); if (j.error) err = j.error; } catch {}
        throw new Error(err);
        }
        const blob = await res.blob();
        const cd = res.headers.get('Content-Disposition');
        const fallback = `zada_thematic_${fieldName}_${paletteName}.${fmt === 'shp' ? 'shp.zip' : fmt}`;
        const filename = filenameFromCD(cd) || fallback;
        downloadBlob(blob, filename);
        if (msgEl) { msgEl.textContent = `Export terminé: ${filename}`; }
        this.showStatus('success', `Fichier exporté: ${filename}`);
    })
    .catch(err => {
        if (msgEl) { msgEl.textContent = `Erreur export: ${err.message}`; }
        this.showStatus('danger', `Erreur export: ${err.message}`);
    });
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



// Méthode pour afficher les résultats NLP
displayNLPResults(results, mapId = 'nlpMap') {
    const map = this.maps.get(mapId);
    if (!map || !results.features) return;
    
    // Supprimer la couche précédente
    if (this.layers.has('nlp-results')) {
        const oldLayer = this.layers.get('nlp-results');
        map.removeLayer(oldLayer);
    }
    
    // Créer la nouvelle couche
    const nlpLayer = L.geoJSON(results.features, {
        style: this.getNLPStyle.bind(this),
        onEachFeature: this.getNLPPopup.bind(this)
    });
    
    map.addLayer(nlpLayer);
    this.layers.set('nlp-results', nlpLayer);
    
    // Ajuster la vue
    if (nlpLayer.getBounds().isValid()) {
        map.fitBounds(nlpLayer.getBounds(), { padding: [20, 20] });
    }
}

// Style pour les résultats NLP
getNLPStyle(feature) {
    const similarity = feature.properties?.nlp_similarity || 0.5;
    let color;
    
    if (similarity > 0.8) {
        color = '#e74c3c';  // Rouge fort
    } else if (similarity > 0.6) {
        color = '#f39c12';  // Orange
    } else if (similarity > 0.4) {
        color = '#f1c40f';  // Jaune
    } else {
        color = '#3498db';  // Bleu
    }
    
    return {
        color: color,
        fillColor: color,
        fillOpacity: 0.6,
        weight: 2,
        opacity: 0.9
    };
}

// Popup pour les résultats NLP
getNLPPopup(feature, layer) {
    if (!feature?.properties) return;
    
    const props = feature.properties;
    const similarity = (props.nlp_similarity * 100).toFixed(1);
    
    const html = `
        <div class="nlp-popup">
            <h6><i class="fas fa-brain me-1"></i><strong>Résultat NLP</strong></h6>
            <table class="table table-sm table-borderless mb-0">
                <tr><td><strong>Rang:</strong></td><td>#${props.nlp_rank}</td></tr>
                <tr><td><strong>Similarité:</strong></td><td>${similarity}%</td></tr>
                <tr><td><strong>Contenu:</strong></td><td>${props.nlp_content_preview}</td></tr>
            </table>
        </div>
    `;
    
    layer.bindPopup(html, { maxWidth: 300, className: 'nlp-popup' });
}
}

//Fonction d'exportation de cartes NLP 

async function onExportNlp(e) {
  e.preventDefault();
  if (!nlpReady) { showAlert('warning', 'Système NLP non initialisé'); return; }

  const q = document.querySelector('[name="query"]').value.trim();
  const topK = Number(document.getElementById('max_results').value || 10);
  const fmt = document.getElementById('fmtNlpSelect').value;
  const msgEl = document.getElementById('nlpExportMsg');

  if (!q) { showAlert('warning', 'Saisissez une requête'); return; }

  const btn = document.getElementById('btnExportNlp');
  btn.disabled = true; if (msgEl) msgEl.textContent = 'Export en cours…';

  try {
    const res = await fetch('/api/nlp/export', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ fmt, top_k: topK, query: q })
    });
    if (!res.ok) {
      let err = 'HTTP ' + res.status;
      try { const j = await res.json(); if (j.error) err = j.error; } catch {}
      throw new Error(err);
    }
    const blob = await res.blob();
    const cd = res.headers.get('Content-Disposition');
    const fallback = `zada_nlp_${Date.now()}.${fmt === 'shp' ? 'shp.zip' : fmt}`;
    const filename = filenameFromCD(cd) || fallback;
    downloadBlob(blob, filename);
    if (msgEl) msgEl.textContent = `Export terminé: ${filename}`;
  } catch (err) {
    if (msgEl) msgEl.textContent = `Erreur export: ${err.message}`;
    showAlert('danger', `Erreur export: ${err.message}`);
  } finally {
    btn.disabled = false;
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

    //Ecouteur NLP
    const btnExp = document.getElementById('btnExportNlp');
    if (btnExp) btnExp.addEventListener('click', onExportNlp);

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

function filenameFromCD(cd) {
  if (!cd) return '';
  const m = /filename\*=UTF-8''([^;]+)|filename="?([^"]+)"?/i.exec(cd);
  return decodeURIComponent(m ? (m[1] || m[2]) : '') || '';
}
function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = filename; document.body.appendChild(a); a.click();
  a.remove(); URL.revokeObjectURL(url);
}

// Expose minimal utils si besoin
window.zadaMapManager = () => zadaMapManager;



