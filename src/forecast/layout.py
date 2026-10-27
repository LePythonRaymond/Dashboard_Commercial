"""
Layout of the forecast spreadsheet: tabs, columns, fixed cells.

Every pole tab (Travaux, Conception, Maintenance contrats, Maintenance TS) has the same shape:

    row 1  title and last update
    row 2  empty, a thin spacer (the colour legend lives in the Accueil tab)
    row 3  field ids (hidden): the server and the Apps Script find columns by these ids
    row 4  "Signé" subtotal per month (formula)
    row 5  "Pipe pondéré" subtotal per month (formula: amount x probability)
    row 6  "Total prévu" per month (formula)
    row 7  "Coût prévu" per month (formula: amount / KV, pipe weighted), for the KV of the summary
    row 8  column titles
    row 9+ one row per project, devis, contract, TS or manual line

Formulas are written in the spreadsheet's locale (fr_FR): ";" between arguments, "," for decimals.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence

from . import months as M

POLES = ("Travaux", "Conception", "Maintenance")  # poles of the Synthèse, Réalisé and objectives
TAB_CONTRACTS = "Maintenance contrats"
TAB_TS = "Maintenance TS"
POLE_TABS = ("Travaux", "Conception", TAB_CONTRACTS, TAB_TS)  # one tab of rows per pole, Maintenance split in two
TAB_POLE = {"Travaux": "Travaux", "Conception": "Conception", TAB_CONTRACTS: "Maintenance", TAB_TS: "Maintenance"}
POLE_TAB_LIST = {pole: [t for t in POLE_TABS if TAB_POLE[t] == pole] for pole in POLES}
TAB_HOME = "Accueil"
TAB_SUMMARY = "Synthèse"
TAB_ACTUALS = "Réalisé"
TAB_PARAMS = "Paramètres"
TAB_CHECKS = "Contrôles"
TAB_INPUTS = "_saisies"  # Réf -> JSON of the values typed by hand
TAB_RENDER = "_rendu"  # Réf -> JSON of the editable cells as last written
TAB_CALC = "_calcul"  # Réf -> JSON the Apps Script needs to redo a row; row 1 holds the grid
HIDDEN_TABS = (TAB_INPUTS, TAB_RENDER, TAB_CALC)
TAB_ORDER = (TAB_HOME, TAB_SUMMARY, *POLE_TABS, TAB_ACTUALS, TAB_PARAMS, TAB_CHECKS, *HIDDEN_TABS)

ROW_TITLE, ROW_LEGEND, ROW_FIELDS = 1, 2, 3
ROW_SIGNED, ROW_PIPE, ROW_TOTAL, ROW_COST = 4, 5, 6, 7
ROW_HEADER = 8
ROW_FIRST = 9

# Colours (Google Sheets RGB 0..1)
def rgb(hex_code: str) -> dict:
    """"#fff2cc" -> {"red": 1.0, "green": 0.949..., "blue": 0.8}"""
    h = hex_code.lstrip("#")
    return {k: int(h[i:i + 2], 16) / 255 for k, i in (("red", 0), ("green", 2), ("blue", 4))}


# One colour per pole, chosen by the user in the Synthèse (2026-10-26): pale for the lines, strong for totals
# and headers; then three shades for the charts (réalisé, signé, pipe pondéré).
POLE_COLORS = {
    "Travaux": {"light": "#fff2cc", "strong": "#ffe599", "chart": ("#7f6000", "#bf9000", "#ffd966")},
    "Conception": {"light": "#d9ead3", "strong": "#b6d7a8", "chart": ("#274e13", "#6aa84f", "#b6d7a8")},
    "Maintenance": {"light": "#d9d2e9", "strong": "#b4a7d6", "chart": ("#20124d", "#674ea7", "#b4a7d6")},
    "Total": {"light": "#cfe2f3", "strong": "#9fc5e8", "chart": ("#073763", "#3d85c6", "#9fc5e8")},
}
# The two Maintenance tabs share the pole's purple; the TS tab is a shade darker so the tabs stand apart.
POLE_COLORS[TAB_CONTRACTS] = POLE_COLORS["Maintenance"]
POLE_COLORS[TAB_TS] = {"light": "#e4dff0", "strong": "#8e7cc3", "chart": POLE_COLORS["Maintenance"]["chart"]}
HEALTH = ("Safe", "À risque", "Mort")  # contract status, set by hand once confirmed
HEALTH_TEXT = {"Safe": "#1a6b3d", "À risque": "#b45309", "Mort": "#7f7f7f"}
OBJECTIVE_LINE = "#434343"
INPUT_HEX = "#e8f2ff"
TYPED_HEX = "#f9cb9c"  # orange: yellow is the Travaux colour
BLUE_INPUT = rgb(INPUT_HEX)
TYPED_BG = rgb(TYPED_HEX)
GREY_CALC = {"red": 0.96, "green": 0.96, "blue": 0.96}
WHITE = {"red": 1.0, "green": 1.0, "blue": 1.0}
HEADER_BG = {"red": 0.15, "green": 0.30, "blue": 0.25}
HEADER_FG = {"red": 1.0, "green": 1.0, "blue": 1.0}
SUBTOTAL_BG = {"red": 0.88, "green": 0.93, "blue": 0.90}
RED_TEXT = {"red": 0.75, "green": 0.1, "blue": 0.1}


@dataclass(frozen=True)
class Col:
    field: str
    title: str
    kind: str  # text, money, pct, kv, date, month
    editable: str = ""  # "" = computed, "all" = every row, "manual" = manual rows only
    width: int = 100
    detail: bool = False  # in the collapsible "détails" group
    only: Sequence[str] = POLE_TABS  # tabs where the column is visible


BEFORE: List[Col] = [
    Col("ref", "Réf.", "text", width=72),
    Col("cat", "Catégorie", "text", "manual", 78),
    Col("type", "Type", "text", width=132),
    Col("label", "Libellé", "text", "manual", 330),
    Col("owner", "Responsable", "text", width=120),
    # Contract status (asked by Hugo): automatic proposal, confirmed by typing it
    Col("health", "État du contrat", "text", "contract", 92, only=(TAB_CONTRACTS,)),
    Col("confirmed_until", "Confirmé jusqu'au", "day", "contract", 92, only=(TAB_CONTRACTS,)),
    Col("health_reason", "Pourquoi", "text", width=300, only=(TAB_CONTRACTS,)),
    # Maintenance portfolio (asked by Arthur): hidden in the other tabs
    Col("client", "Client", "text", width=150, only=(TAB_CONTRACTS,)),
    Col("furious_no", "N° Furious", "text", width=72, only=(TAB_CONTRACTS,)),
    Col("status", "Statut Notion", "text", width=78, only=(TAB_CONTRACTS,)),
    Col("renewal", "Reconduction (Notion)", "text", width=150, only=(TAB_CONTRACTS,)),
    Col("frequency", "Fréquence", "text", width=190, only=(TAB_CONTRACTS,)),
    Col("passages_year", "Passages prévus {year}", "int", width=82, only=(TAB_CONTRACTS,)),
    Col("passages_done", "Passages faits à date", "int", width=82, only=(TAB_CONTRACTS,)),
    Col("year_value", "CA {year} (€)", "money", width=96, only=(TAB_CONTRACTS,)),
    Col("per_passage", "CA par passage (€)", "money", width=92, only=(TAB_CONTRACTS,)),
    Col("ca", "CA (€)", "money", "manual", 100),
    Col("proba", "Proba", "pct", "all", 64),
    Col("remaining", "Reste à produire (€)", "money", width=120),
    Col("this_year", "dont {year} (€)", "money", width=110),
    Col("start", "Début", "date", "all", 84),
    Col("stop", "Fin", "date", "all", 84),  # contracts: Notion's end date (they renew unless "Perdu")
    Col("kv", "KV", "kv", "all", 56),
    # Collapsible group "détails" (contiguous on purpose: a column group needs one block)
    Col("typo", "Typologie", "text", width=130, detail=True),
    Col("produced", "Réalisé à la clôture (€)", "money", width=120, detail=True),
    Col("mai", "Part maintenance (€)", "money", "all", 110, detail=True, only=("Travaux",)),
    Col("cost", "Coût prévu (€)", "money", "all", 106, detail=True),
]
AFTER: List[Col] = [
    Col("beyond", "Après la fenêtre (€)", "money", width=110),
    Col("gap", "Écart (€)", "money", width=88),
    Col("cost_next", "Coût sur la fenêtre (€)", "money", width=120),
    Col("margin_next", "Marge sur la fenêtre (€)", "money", width=120),
    Col("alerts", "Alertes", "text", width=260),
    Col("comment", "Commentaire / prochaine étape", "text", "all", 320),
]


def quarters(grid: List[str]) -> List[tuple]:
    """Calendar quarters touched by the grid: (field id, title, months of the quarter inside the grid)."""
    out: List[tuple] = []
    for m in grid:
        year, month = M.parse(m)
        q = (month - 1) // 3 + 1
        fid = f"q:{year}-{q}"
        if out and out[-1][0] == fid:
            out[-1][2].append(m)
        else:
            out.append((fid, f"T{q} {year}", [m]))
    return out


def pole_columns(grid: List[str]) -> List[Col]:
    months = [Col(m, M.label(m), "month", "all", 92) for m in grid]
    quarter_cols = [Col(fid, title, "money", width=96) for fid, title, _ in quarters(grid)]
    return BEFORE + months + quarter_cols + AFTER


def col_letter(index0: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA."""
    n, out = index0 + 1, ""
    while n:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out


def a1(col0: int, row1: int) -> str:
    return f"{col_letter(col0)}{row1}"


def quote(tab: str) -> str:
    return "'" + tab.replace("'", "''") + "'"


# Paramètres: fixed cells
P_LAST_CLOSED = "B4"
P_FALLBACK = "B5"
P_NEW_PROJECTS_SINCE = "B6"
P_ZERO_PROBA = "B7"
P_KV_FIRST_ROW = 10  # A10:B19 typologie -> KV
P_KV_ROWS = 10
P_OBJ_HEADER_ROW = 22  # objectives table header; rows 23+ = pole x year
