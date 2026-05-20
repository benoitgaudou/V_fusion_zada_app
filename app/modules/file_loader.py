# ============================================================================
# app/modules/file_loader.py
# Un chargeur de fichiers SIG robuste (ZIP Shapefile & GeoJSON) + conversion GeoJSON
# ============================================================================
from __future__ import annotations

import io
import json
import logging
import os
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Tuple, Union

import geopandas as gpd
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

try:
    from .exceptions import FileLoadingError
except Exception:  
    class FileLoadingError(RuntimeError):
        pass

logger = logging.getLogger(__name__)


# ------------------------------
# Config
# ------------------------------
@dataclass(frozen=True)
class FileLoaderConfig:
    upload_folder: Path
    force_output_crs: str = "EPSG:4326"      # CRS de sortie attendu par l'algo
    assume_input_crs: str = "EPSG:4326"      # CRS par défaut si absent
    max_features_debug: Optional[int] = None # Limite de lecture (None = pas de limite)
    allow_network_proj: bool = False         # PROJ_NETWORK (par défaut OFF)
    keep_extracted: bool = False             # Conserver dossiers d'extraction ZIP ?

    def __post_init__(self):
        Path(self.upload_folder).mkdir(parents=True, exist_ok=True)


# ------------------------------
# Outil principal
# ------------------------------
class FileLoader:
    """Charge des shapefiles (dans ZIP) ou des GeoJSON et renvoie des GeoDataFrames prêts
    à être convertis en GeoJSON WGS84 pour l'algorithme."""

    def __init__(self, config: FileLoaderConfig):
        self.cfg = config
        self._configure_proj()

    # ---------- Public API ----------
    def process_uploaded_files(
        self, uploaded_files: Iterable[Union[FileStorage, Path, str]]
    ) -> List[Tuple[gpd.GeoDataFrame, str]]:
        """Traite une liste de fichiers uploadés ou de chemins.
        Retourne: [(gdf, stem), ...]  (stem = nom de base sans extension)"""
        results: List[Tuple[gpd.GeoDataFrame, str]] = []
        errors: List[str] = []

        for idx, f in enumerate(uploaded_files, start=1):
            try:
                layer_results = self.load_geofile(f)
                # Harmonise CRS → sortie
                for gdf, stem in layer_results:
                    gdf = self._ensure_output_crs(gdf)
                    results.append((gdf, stem))
                    logger.info(" [%d] %s : %d entités | CRS=%s", idx, stem, len(gdf), gdf.crs)
            except Exception as exc:
                msg = f"[{idx}] {getattr(f, 'filename', str(f))}: {exc}"
                logger.exception(msg)
                errors.append(msg)

        if not results:
            detail = "\n".join(errors) if errors else "Aucune erreur détaillée"
            raise FileLoadingError(f"Aucun fichier valide chargé.\n{detail}")
        return results

    def to_geojson_str(self, gdf: gpd.GeoDataFrame) -> str:
        """Convertit un GeoDataFrame (reprojeté) en chaîne GeoJSON validée."""
        gdf = self._ensure_output_crs(gdf)
        data = gdf.to_json()
        json.loads(data)  # validation légère
        return data

    # ---------- Lecture unitaire ----------
    def load_geofile(
        self, file_or_path: Union[FileStorage, Path, str]
    ) -> List[Tuple[gpd.GeoDataFrame, str]]:
        """Charge un fichier individuel (ZIP Shapefile ou GeoJSON)."""
        # Cas Flask: FileStorage
        if isinstance(file_or_path, FileStorage):
            filename = secure_filename(file_or_path.filename or "")
            if not filename:
                raise FileLoadingError("Fichier sans nom.")
            if filename.lower().endswith(".zip"):
                return self._read_zip_filestorage(file_or_path, filename)
            if filename.lower().endswith(".geojson") or filename.lower().endswith(".json"):
                return self._read_geojson_filestorage(file_or_path, filename)
            raise FileLoadingError(f"Extension non supportée: {filename}")

        # Cas chemin (tests/scripts)
        path = Path(file_or_path)
        if not path.exists():
            raise FileLoadingError(f"Chemin introuvable: {path}")
        if path.suffix.lower() == ".zip":
            return self._read_zip_path(path)
        if path.suffix.lower() in {".geojson", ".json"}:
            return self._read_geojson_path(path)
        raise FileLoadingError(f"Extension non supportée: {path.name}")

    # ---------- ZIP (Shapefile) ----------
    def _read_zip_filestorage(self, fs: FileStorage, filename: str) -> List[Tuple[gpd.GeoDataFrame, str]]:
        zip_path = Path(self.cfg.upload_folder) / filename
        fs.save(zip_path)
        try:
            return self._read_zip_path(zip_path)
        finally:
            if not self.cfg.keep_extracted:
                zip_path.unlink(missing_ok=True)

    def _read_zip_path(self, zip_path: Path) -> List[Tuple[gpd.GeoDataFrame, str]]:
        extract_dir = Path(self.cfg.upload_folder) / zip_path.stem
        extract_dir.mkdir(exist_ok=True)

        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                self._safe_extract(zf, extract_dir)

            vector_files = self._find_vector_files(extract_dir)
            if not vector_files:
                raise FileLoadingError("Aucun fichier vectoriel trouvé dans le ZIP.")

            results = []
            for vector_path in vector_files:
                try:
                    gdf = self._read_vector_file(vector_path)
                    # nom de couche = nom du ZIP + nom du fichier 
                    layer_name = (f"{zip_path.stem}__{vector_path.stem}")
                    results.append((gdf, layer_name))

                    logger.info( "Layer loaded: %s (%d features)", layer_name, len(gdf) )
                except Exception as exc:
                    logger.exception("Erreur lecture couche %s : %s", vector_path, exc)

            if not results:
                raise FileLoadingError("Aucune couche vectorielle valide dans le ZIP.")

            return results

        finally:
            if not self.cfg.keep_extracted:
                self._rmtree(extract_dir)

    # ---------- GeoJSON ----------
    def _read_geojson_filestorage(self, fs: FileStorage, filename: str) -> List[Tuple[gpd.GeoDataFrame, str]]:
        # lecture via buffer mémoire pour compat Flask
        buf = io.BytesIO(fs.read())
        gdf = self._read_geojson_vector(buf)
        return [(gdf, Path(filename).stem)]

    def _read_geojson_path(self, path: Path) -> List[Tuple[gpd.GeoDataFrame, str]]:
        gdf = self._read_geojson_vector(path)
        return [(gdf, path.stem)]

    # ---------- Impl. bas niveau ----------
    def _robust_read_vector(self, path: Path) -> gpd.GeoDataFrame:
        """Lecture robuste (Shapefile). Tente gpd, sinon fiona→from_features."""
        # 1) Essai direct GeoPandas
        try:
            gdf = gpd.read_file(path)
            return self._post_read_fix(gdf, src_path=path)
        except Exception as exc:
            logger.warning("Lecture GeoPandas échouée (%s). Tentative fiona…", exc)

        # 2) Fiona brut
        try:
            import fiona
            from shapely.geometry import shape

            features = []
            with fiona.open(path) as src:
                src_crs = src.crs
                for i, feat in enumerate(src):
                    if self.cfg.max_features_debug and i >= self.cfg.max_features_debug:
                        break
                    geom = feat.get("geometry")
                    props = feat.get("properties") or {}
                    if geom:
                        features.append({"geometry": shape(geom), **props})
            if not features:
                raise FileLoadingError("Aucune entité lisible avec fiona.")
            gdf = gpd.GeoDataFrame(features, crs=None)
            # priorité au .prj si possible
            crs = self._pick_source_crs(path) or src_crs or self.cfg.assume_input_crs
            gdf = gdf.set_crs(crs, allow_override=True)
            return self._post_read_fix(gdf, src_path=path)
        except Exception as exc:
            raise FileLoadingError(f"Echec lecture vectorielle: {exc}")

    def _read_geojson_vector(self,path: Path ) -> gpd.GeoDataFrame:
        try:
            gdf = gpd.read_file(path)
            return self._post_read_fix(gdf,src_path=path if isinstance(path, Path) else None)
        except Exception as exc:
            raise FileLoadingError(f"Echec lecture GeoJSON {path.name}: {exc}")

    def _read_vector_file(self,path: Path) -> gpd.GeoDataFrame:
        suffix = path.suffix.lower()

        if suffix == ".shp":
            return self._robust_read_vector(path)
        elif suffix in {".geojson", ".json"}:
            return self._read_geojson_vector(path)
        else:
            raise FileLoadingError(f"Format non supporté: {path.name}")

    # ---------- Helpers ----------
    def _post_read_fix(self, gdf: gpd.GeoDataFrame, src_path: Optional[Path] = None) -> gpd.GeoDataFrame:
        """Nettoyage post-lecture: CRS, limite features, geometry non‑nulles."""
        if self.cfg.max_features_debug is not None and len(gdf) > self.cfg.max_features_debug:
            gdf = gdf.iloc[: self.cfg.max_features_debug].copy()
        gdf = gdf[gdf.geometry.notna()].copy()

        # Reconstituer CRS s'il est absent
        if gdf.crs is None and src_path:
            guessed = self._pick_source_crs(src_path)
            if guessed:
                gdf = gdf.set_crs(guessed, allow_override=True)

        if gdf.crs is None:
            gdf = gdf.set_crs(self.cfg.assume_input_crs, allow_override=True)
        return gdf

    def _ensure_output_crs(self, gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        """Reprojette vers le CRS de sortie si nécessaire."""
        if gdf.crs is None:
            gdf = gdf.set_crs(self.cfg.assume_input_crs,allow_override=True)

        if gdf.crs.to_string() != self.cfg.force_output_crs:
            return gdf.to_crs(self.cfg.force_output_crs)

        return gdf

    def _pick_source_crs(self, vector_path: Path) -> Optional[str]:
        """Essaie de lire un .prj à côté d'un .shp pour récupérer le CRS réel."""
        prj = vector_path.with_suffix(".prj")
        if prj.exists():
            try:
                from pyproj import CRS
                return CRS.from_wkt(prj.read_text(encoding="utf-8", errors="ignore")).to_string()
            except Exception:
                # ignore -> on tombera sur assume_input_crs
                return None
        return None

    @staticmethod
    def _find_vector_files(extract_dir: Path) -> List[Path]:

        supported = {".shp", ".geojson", ".json"}
        vector_files = []

        for root, _, files in os.walk(extract_dir):
            # ignore dossiers parasites MacOS
            if "__MACOSX" in root:
                continue

            for f in files:
                # ignore les fichiers cachés (ex: .DS_Store) et les fichiers temporaires
                if f.startswith("."):
                    continue

                path = Path(root) / f

                if path.suffix.lower() in supported:
                    vector_files.append(path)

        return sorted(vector_files)


#    @staticmethod
#    def _find_all_shp(extract_dir: Path) -> List[Path]:
#        shp_files = []
#
#        for root, _, files in os.walk(extract_dir):
#            for f in files:
#                if f.lower().endswith(".shp") and not f.startswith("."):
#                    shp_files.append(Path(root) / f)
#
#        return sorted(shp_files)

#    @staticmethod
#    def _find_first_shp(extract_dir: Path) -> Optional[Path]:
#        for root, _, files in os.walk(extract_dir):
#            for f in files:
#                if f.lower().endswith(".shp") and not f.startswith("."):
#                    return Path(root) / f
#        return None

    @staticmethod
    def _safe_extract(zf: zipfile.ZipFile, target: Path) -> None:
        """Extraction ZIP protégée contre zip‑slip."""
        target_resolved = target.resolve()
        for member in zf.infolist():
            dest = (target / member.filename).resolve()
            if not str(dest).startswith(str(target_resolved)):
                raise FileLoadingError("Archive ZIP non sûre (zip‑slip détecté).")
        zf.extractall(target)

    @staticmethod
    def _rmtree(path: Path) -> None:
        try:
            import shutil
            shutil.rmtree(path, ignore_errors=True)
        except Exception:
            pass

    def _configure_proj(self) -> None:
        """Configuration PROJ minimaliste (OFF par défaut)."""
        os.environ["PROJ_NETWORK"] = "ON" if self.cfg.allow_network_proj else "OFF"