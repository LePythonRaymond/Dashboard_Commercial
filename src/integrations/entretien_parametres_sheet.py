"""Layout helpers for Maintenance Entretien parameter row on Google Sheets (Paramètres tab)."""

from typing import Any, List, Optional, Tuple

ENTRETIEN_PARAMETRES_WORKSHEET = "Paramètres"
ENTRETIEN_PARAM_KEY = "maintenance_entretien_debut_annee"


def _cell_to_float(val: Any) -> Optional[float]:
    if val is None or val == "":
        return None
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        return float(val)
    s = str(val).strip().replace(" ", "").replace(",", ".")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_entretien_parametres_rows(rows: Optional[List[List[Any]]]) -> Optional[Tuple[float, str]]:
    """
    Parse the fixed Paramètres layout (rows A1:C2) for maintenance entretien début d'année.

    Returns:
        (value, updated_at) if shape and key match, else None.
    """
    if not rows or len(rows) < 2:
        return None
    r0, r1 = rows[0], rows[1]
    if len(r0) < 3 or len(r1) < 3:
        return None
    h0 = str(r0[0]).strip().lower()
    h1 = str(r0[1]).strip().lower()
    h2 = str(r0[2]).strip().lower()
    if h0 != "clé" or "valeur" not in h1 or "mis à jour" not in h2:
        return None
    if str(r1[0]).strip() != ENTRETIEN_PARAM_KEY:
        return None
    value = _cell_to_float(r1[1])
    if value is None:
        return None
    updated_at = str(r1[2]).strip() if r1[2] is not None else ""
    if not updated_at:
        return None
    return (value, updated_at)
