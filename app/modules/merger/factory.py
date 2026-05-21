import logging

from app.modules.merger.config import MergeConfig
from app.modules.merger.zada_merger import ZadaMerger
from app.modules.merger.zada_pairwise_merger import ZadaPairwiseMerger

logger = logging.getLogger(__name__)


def create_merger(algorithm, config: MergeConfig):
    algorithm = algorithm.lower()

    if algorithm in ("default", "modern"):
        logger.debug("Creation de ZadaMerger")
        return ZadaMerger(config)

    if algorithm in ("pairwise", "titouan"):
        logger.debug("Creation de ZadaPairwiseMerger")
        return ZadaPairwiseMerger(config)

    raise ValueError(f"Unknown merger algorithm: {algorithm}")