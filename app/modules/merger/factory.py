from app.modules.merger.config import MergeConfig
from app.modules.merger.zada_merger import ZadaMerger
from app.modules.merger.zada_pairwise_merger import ZadaPairwiseMerger

def create_merger(algorithm, config: MergeConfig):
    algorithm = algorithm.lower()

    if algorithm in ("default", "modern"):
        return ZadaMerger(config)

    if algorithm in ("pairwise", "titouan"):
        return ZadaPairwiseMerger(config)

    raise ValueError(f"Unknown merger algorithm: {algorithm}")