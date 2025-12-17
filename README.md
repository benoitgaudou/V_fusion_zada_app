# V_fusion_zada_app


Application web de fusion et d'analyse de données géospatiales, avec export, cartographie thématique et recherche sémantique (NLP).

## Prérequis

- Python 3.10 ou supérieur
- pip (gestionnaire de paquets Python)
- GDAL et libspatialindex installés sur le système (pour GeoPandas et Shapely)
- (Optionnel) Un environnement virtuel Python (venv ou conda)

## Installation

### 1. Cloner le dépôt

```bash
git clone https://github.com/DidioDieudonne/V_fusion_zada_app.git
cd fusion_zada_app
```

### 2. Créer un environnement virtuel

```bash
python -m venv .venv
source .venv/bin/activate  # Sur Windows : .venv\Scripts\activate
```

### 3. Installer les dépendances système

**Sur Ubuntu/Debian :**
```bash
sudo apt-get update
sudo apt-get install -y gdal-bin libgdal-dev libspatialindex-dev
```

**Sur MacOS :**
```bash
brew install gdal
brew install spatialindex
```

**Sur Windows :**  
Téléchargez et installez GDAL et libspatialindex via les wheel (.whl) si besoin.

### 4. Installer les dépendances Python

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

## Lancement de l'application

```bash
python run.py
```

L'application sera accessible sur http://localhost:5000.

## Structure des dossiers

- `app` : code principal (routes, modules, templates, static)
- `Donnees`, `uploads`, `results`, `out` : dossiers de données
- `requirements.txt` : dépendances Python

## Fonctionnalités

- Upload de fichiers géospatiaux (Shapefile, GeoJSON, GPKG)
- Fusion intelligente des couches (algorithme ZADA)
- Export des résultats (GeoJSON, GPKG, Shapefile)
- Cartographie thématique interactive
- Recherche sémantique (NLP) sur les attributs

## Configuration

Les chemins de dossiers et paramètres sont configurables dans `config.py` ou via variables d'environnement.

## Dépannage

- Si GeoPandas/Shapely ne s'installent pas, vérifiez la présence de GDAL et libspatialindex.
- Pour les erreurs d'import, assurez-vous d'être dans le bon dossier et d'avoir activé l'environnement virtuel.


