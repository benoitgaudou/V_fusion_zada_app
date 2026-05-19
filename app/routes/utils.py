from __future__ import annotations

from pathlib import Path
from typing import Optional

import geopandas as gpd
from flask import current_app, session

from app.modules.file_loader import FileLoader, FileLoaderConfig
from app.modules.fusion import MergeConfig, ZadaMerger


def _get_paths() -> tuple[Path, Path, Path]:
    uploads = Path(current_app.config['UPLOAD_FOLDER'])
    stage = Path(current_app.config['STAGE_FOLDER'])
    results = Path(current_app.config['RESULTS_FOLDER'])
    for folder in (uploads, stage, results):
        folder.mkdir(parents=True, exist_ok=True)
    return uploads, stage, results


def _get_loader() -> FileLoader:
    uploads, _, _ = _get_paths()
    cfg = FileLoaderConfig(
        upload_folder=uploads,
        force_output_crs=current_app.config['DEFAULT_CRS'],
        assume_input_crs=current_app.config['DEFAULT_CRS'],
        max_features_debug=None,
        allow_network_proj=bool(current_app.config.get('PROJ_NETWORK', False)),
        keep_extracted=False,
    )
    return FileLoader(cfg)


def _get_merger(area_threshold: float | None = None) -> ZadaMerger:
    at = float(
        area_threshold
        if area_threshold is not None
        else session.get('area_threshold', current_app.config['DEFAULT_AREA_THRESHOLD'])
    )
    mcfg = MergeConfig(
        area_threshold_m2=at,
        input_crs_fallback=current_app.config['DEFAULT_CRS'],
        output_crs=current_app.config['DEFAULT_CRS'],
        metric_crs=current_app.config['METRIC_CRS'],
        sample_unique_values=10,
        similarity_threshold=0.30,
    )
    return ZadaMerger(mcfg)


def _non_tech_columns(gdf: gpd.GeoDataFrame) -> list[str]:
    excluded_base = {'geometry', 'intersection_type', 'type', 'source', 'source_names'}
    prefixes_to_exclude = ('original', 'source', 'id')

    try:
        geom_col = gdf.geometry.name
    except Exception:
        geom_col = 'geometry'

    excluded_lc = {x.lower() for x in (excluded_base | {geom_col})}

    def is_excluded(col: str) -> bool:
        c = col.lower()
        if c in excluded_lc:
            return True
        return any(c.startswith(prefix) for prefix in prefixes_to_exclude)

    return [c for c in gdf.columns if not is_excluded(c)]
