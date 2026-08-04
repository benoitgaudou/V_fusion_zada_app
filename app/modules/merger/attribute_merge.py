from __future__ import annotations

from typing import Optional

import pandas as pd

from app.config import Config

# Colonne qui liste les sources d'origine d'une entité fusionnée (ex: "z2+z4") :
# elle garde "+" comme séparateur, indépendamment du séparateur de fusion des
# autres attributs (`Config.ATTRIBUTE_MERGE_SEPARATOR`).
SOURCE_ATTRIBUTE_SEPARATOR = "+"
SOURCE_ATTRIBUTE_COLUMNS = {Config.SOURCE_NAMES_COLUMN.lower()}


def separator_for_column(col_name, default: Optional[str] = None) -> str:
    """Retourne le séparateur à utiliser pour fusionner les valeurs de `col_name`."""
    if default is None:
        default = Config.ATTRIBUTE_MERGE_SEPARATOR
    if str(col_name).lower() in SOURCE_ATTRIBUTE_COLUMNS:
        return SOURCE_ATTRIBUTE_SEPARATOR
    return default


def normalize_attribute_value(val, separator: Optional[str] = None) -> str:
    """
    Normalise une valeur d'attribut texte unique (fusionnée ou non) : les
    éléments déjà séparés par `separator` et dupliqués sont supprimés.

    Les séparateurs "," ";" "/" éventuellement présents dans la valeur ne
    sont volontairement pas convertis ici : ce sera une option proposée à
    l'utilisateur plus tard.
    """
    if separator is None:
        separator = Config.ATTRIBUTE_MERGE_SEPARATOR

    raw = str(val)

    parts = []
    for part in raw.split(separator):
        part = part.strip()
        if part and part not in parts:
            parts.append(part)
    return separator.join(parts) if parts else raw


def merge_attribute_values(val_a, val_b, separator: Optional[str] = None) -> str:
    """
    Combine deux valeurs d'un même attribut texte provenant de la fusion de 2 ZADA
    qui se chevauchent. Fonction commune aux deux algorithmes de fusion (ZadaMerger
    et l'algorithme pairwise/Titouan).

    Seule une valeur NA (NaN) est considérée comme manquante.
    - Si une des deux valeurs est manquante, l'autre est renvoyée (normalisée).
    - Sinon, les deux valeurs sont concaténées puis normalisées ensemble via
      `normalize_attribute_value` (doublons supprimés).
    """
    if separator is None:
        separator = Config.ATTRIBUTE_MERGE_SEPARATOR

    if pd.isna(val_a):
        return normalize_attribute_value(val_b, separator)
    if pd.isna(val_b):
        return normalize_attribute_value(val_a, separator)

    return normalize_attribute_value(f"{val_a}{separator}{val_b}", separator)
