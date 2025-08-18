# app/modules/nlp/utils.py
from __future__ import annotations
import unicodedata
import re
import numpy as np
from typing import List, Dict

_WORD_RE = re.compile(r"\b\w+\b", flags=re.UNICODE)

def clean_value(v) -> str | None:
    if v is None: return None
    s = str(v).strip().lower()
    if s in {"", " ", "nan", "none", "null", "nsp", "0", "0.0"}:
        return None
    s = re.sub(r"[^\w\s/\-,.]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) > 1 else None

def clean_colname(c: str) -> str:
    c = re.sub(r"_\d+$", "", str(c))
    c = re.sub(r"_+", "_", c).strip("_")
    return c

def strip_prefixes(text: str) -> str:
    # supprime les "nom_colonne: "
    return re.sub(r"[^:\n]+:\s*", "", text, flags=re.IGNORECASE)

def remove_eccent(text: str)-> str:
    # supprime les accents
    nfkd_form = unicodedata.normalize('NFKD', text)
    return ''.join([c for c in nfkd_form if not unicodedata.combining(c)])
    
def tokens_from_corpus(text: str, min_len: int = 3) -> List[str]:
    if not isinstance(text, str): return []
    t = strip_prefixes(text.replace(";", " ").replace(",", " ").replace("/", " "))
    t = remove_eccent(t)
    toks = _WORD_RE.findall(t.lower())
    return [w for w in toks if len(w) >= min_len]

def palette_rdylbu(n: int) -> List[str]:
    base = ["#A50026","#D73027","#F46D43","#FDAE61","#FEE090",
            "#E0F3F8","#ABD9E9","#74ADD1","#4575B4","#313695"][::-1]
    if n <= len(base): return base[:n]
    from itertools import cycle
    return [c for _, c in zip(range(n), cycle(base))]

def legend_from_scores(scores: np.ndarray, classes: int = 6) -> Dict:
    qs = np.quantile(scores, np.linspace(0, 1, classes+1))
    items, palette = [], palette_rdylbu(classes)
    for i in range(classes):
        lo, hi = qs[i], qs[i+1]
        items.append({"label": f"{lo:.2f} - {hi:.2f}", "color": palette[i]})
    return {"type":"continuous","items":items, "min_value": float(qs[0]), "max_value": float(qs[-1])}
