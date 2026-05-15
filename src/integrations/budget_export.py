"""
Budget {Y} workbook builder.

Produces an .xlsx mirroring the layout of the manually-maintained
"Budget {Y} avec légende" Google Sheet (see ``Budget 2026.xlsx`` reference):

- Sheet 1 "Budget {Y} avec légende": single block (rows 11-25) with
  Légende, the ``Au DD/MM/YYYY`` date, headers per BU, then Devis/Contrats
  Signés/Potentiels/Envoyés numbers, Production sécurisée, Portefeuille
  sites au {today}, Total sécurisé maintenance, Objectifs and Production
  à aller chercher.
- Sheet 2 "Maintenance": per-row breakdown of new MAINTENANCE contracts
  signed in {Y} (Nom, Montant HT Prod {Y}, Montant HT, Mois signature,
  Mois démarrage), with the portefeuille at 1er Janvier {Y} and totals.

All numeric inputs (BU sums, maintenance entries, portefeuille values)
are computed from the processed proposals DataFrame the dashboard already
loads — no new business logic, just aggregation + layout.
"""

from __future__ import annotations

from datetime import date
from io import BytesIO
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd

try:
    from config.settings import STATUS_WAITING, STATUS_WON
except ImportError:
    STATUS_WON = ['gagné', 'gagne', 'signé', 'signe', 'gagnés et finis', 'gagnés en cours']
    STATUS_WAITING = ['brief', 'en cours', 'envoyée(s) attente réponse', 'envoyée(s) en attente de réponse']

try:
    from src.processing.objectives import objective_for_year
except ImportError:
    def objective_for_year(year: int, metric: str, dimension: str, key: str) -> float:  # type: ignore
        return 0.0


BU_ORDER: List[str] = ['CONCEPTION', 'TRAVAUX', 'MAINTENANCE']

MONTH_NAMES_FR: List[str] = [
    "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
    "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre",
]

LEGEND_TEXT_TEMPLATE = (
    "Devis Signés = Tous les devis déjà signés à produire en {year}\n"
    "Devis Potentiels = Tous les devis envoyés multipliés par leur probabilité "
    "de gain à produire en {year}\n"
    "Devis Envoyés = Tous les devis envoyés sans probabilité à produire en {year}"
)

EUR_FORMAT = '#,##0 €'


# =============================================================================
# Aggregation helpers
# =============================================================================

def _normalize_status(df: pd.DataFrame) -> pd.Series:
    """Return a normalized lowercase status Series (statut_clean fallback to statut)."""
    if 'statut_clean' in df.columns:
        return df['statut_clean'].astype(str).str.strip().str.lower()
    if 'statut' in df.columns:
        return df['statut'].astype(str).str.strip().str.lower()
    return pd.Series([''] * len(df), index=df.index)


def _bu_column(df: pd.DataFrame) -> pd.Series:
    """Return BU Series (final_bu fallback to cf_bu)."""
    if 'final_bu' in df.columns:
        return df['final_bu'].astype(str).str.strip().str.upper()
    if 'cf_bu' in df.columns:
        return df['cf_bu'].astype(str).str.strip().str.upper()
    return pd.Series([''] * len(df), index=df.index)


def _to_year_set(values: Sequence[str]) -> set:
    return {v.strip().lower() for v in values}


def _safe_sum(series: pd.Series) -> float:
    if series is None or len(series) == 0:
        return 0.0
    return float(pd.to_numeric(series, errors='coerce').fillna(0.0).sum())


def _column(df: pd.DataFrame, name: str) -> pd.Series:
    """Return df[name] coerced to numeric, or zeros if column missing."""
    if name in df.columns:
        return pd.to_numeric(df[name], errors='coerce').fillna(0.0)
    return pd.Series([0.0] * len(df), index=df.index)


def _compute_bu_amounts(df: pd.DataFrame, year: int, bu: str) -> Dict[str, float]:
    """
    Compute Signés / Potentiels / Envoyés totals for one BU and one year.

    Definitions (from the legend in Budget {Y}.xlsx):
    - Signés     = sum(Montant Total {Y}) where statut_clean ∈ STATUS_WON     and BU == bu
    - Potentiels = sum(Montant Pondéré {Y}) where statut_clean ∈ STATUS_WAITING and BU == bu
    - Envoyés    = sum(Montant Total {Y}) where statut_clean ∈ (WON ∪ WAITING) and BU == bu
    """
    if df is None or df.empty:
        return {"signes": 0.0, "potentiels": 0.0, "envoyes": 0.0}

    won = _to_year_set(STATUS_WON)
    waiting = _to_year_set(STATUS_WAITING)
    statuses = _normalize_status(df)
    bus = _bu_column(df)

    bu_mask = bus == bu.upper()
    won_mask = bu_mask & statuses.isin(won)
    waiting_mask = bu_mask & statuses.isin(waiting)
    sent_mask = bu_mask & statuses.isin(won | waiting)

    total_col = f"Montant Total {year}"
    pondere_col = f"Montant Pondéré {year}"

    signes = _safe_sum(_column(df, total_col)[won_mask])
    potentiels = _safe_sum(_column(df, pondere_col)[waiting_mask])
    envoyes = _safe_sum(_column(df, total_col)[sent_mask])

    return {"signes": signes, "potentiels": potentiels, "envoyes": envoyes}


def _parse_date(value: Any) -> Optional[pd.Timestamp]:
    if value is None or value == "":
        return None
    try:
        ts = pd.to_datetime(value, errors='coerce')
        if pd.isna(ts):
            return None
        return ts
    except Exception:
        return None


def _month_name_fr(value: Any) -> str:
    ts = _parse_date(value)
    if ts is None:
        return ""
    m = int(ts.month)
    if 1 <= m <= 12:
        return MONTH_NAMES_FR[m - 1]
    return ""


def _row_signature_year(row: pd.Series) -> Optional[int]:
    """
    Return the year a row was 'signed' for the purpose of the Budget Maintenance sheet.

    Matches the Signé monthly view OR-logic: signature_date OR date_effective_won OR date.
    """
    for col in ('signature_date', 'date_effective_won', 'date'):
        ts = _parse_date(row.get(col)) if col in row else None
        if ts is not None:
            return int(ts.year)
    return None


def _compute_maintenance_entries(df: pd.DataFrame, year: int) -> List[Dict[str, Any]]:
    """
    One row per new MAINTENANCE contract signed in {year}.

    Filter: statut_clean ∈ STATUS_WON AND BU == MAINTENANCE AND
            (signature_date.year == Y OR date_effective_won.year == Y OR date.year == Y)
    """
    if df is None or df.empty:
        return []

    won = _to_year_set(STATUS_WON)
    statuses = _normalize_status(df)
    bus = _bu_column(df)

    base_mask = (bus == 'MAINTENANCE') & statuses.isin(won)
    candidates = df[base_mask]

    entries: List[Dict[str, Any]] = []
    total_col = f"Montant Total {year}"
    for _, row in candidates.iterrows():
        if _row_signature_year(row) != year:
            continue

        title = str(row.get('title') or row.get('name') or row.get('Name') or '').strip()
        nom = f"(E) - {title}" if title else "(E) -"

        amount = float(pd.to_numeric(row.get('amount', 0), errors='coerce') or 0.0)
        prod = float(pd.to_numeric(row.get(total_col, 0), errors='coerce') or 0.0) if total_col in row else 0.0

        sig_date = _parse_date(row.get('signature_date')) or _parse_date(row.get('date_effective_won'))
        mois_signature = _month_name_fr(sig_date) if sig_date is not None else ""
        mois_demarrage = _month_name_fr(row.get('projet_start'))

        entries.append({
            "nom": nom,
            "montant_ht_prod": prod,
            "montant_ht": amount,
            "mois_signature": mois_signature,
            "mois_demarrage": mois_demarrage,
        })

    return entries


# =============================================================================
# Workbook layout
# =============================================================================

def _set_eur(cell, value: Optional[float]) -> None:
    if value is None:
        cell.value = ""
    else:
        cell.value = float(value)
        cell.number_format = EUR_FORMAT


def _build_sheet1(ws, year: int, today: date, bu_totals: Dict[str, Dict[str, float]],
                  portefeuille_debut: Optional[float], portefeuille_running: Optional[float]) -> None:
    """
    Layout (1-indexed rows) — mirrors `Copie de Budget 26 avec légende`:

      D11           : "Légende"
      D12:L14       : legend text (merged)
      D16           : "Au DD/MM/YYYY"
      D17:D19       : "Projection {Y}" (merged)
      E17:G17       : "CONCEPTION"  (merged)
      H17:J17       : "TRAVAUX"     (merged)
      K17:M17       : "MAINTENANCE" (merged)
      N17:P17       : "TOTAL"       (merged)
      Row 18        : column subheaders
      Row 19        : computed numeric values
      D20           : "Production sécurisée"
      Row 20        : E20=E19+F19, H20=H19+I19, K20=L19+K19, N20=E20+H20+L22
      K21:M21       : "Portefeuille sites au DD/MM/YYYY"
      L21           : portefeuille_running
      K22:M22       : "Total sécurisé maintenance {YY}"
      L22           : =L19+L21
      D23           : "Objectifs {Y}"
      Row 23        : E23/H23/K23 from objective_for_year, N23 = sum
      D25           : "Production à aller chercher"
      Row 25        : =Obj-Production sécurisée per BU
    """
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    ws.title = f"Budget {year} avec légende"

    # Column widths matching the reference workbook
    width_map = {
        'D': 28.1, 'E': 13.9, 'F': 16.0, 'G': 17.0, 'H': 14.0, 'I': 16.0, 'J': 17.0,
        'K': 15.4, 'L': 16.4, 'M': 14.9, 'N': 21.5, 'O': 23.1, 'P': 22.2,
    }
    for col, w in width_map.items():
        ws.column_dimensions[col].width = w

    bold = Font(bold=True)
    bold_white = Font(bold=True, color="FFFFFF")
    center = Alignment(horizontal="center", vertical="center", wrap_text=True)
    legend_fill = PatternFill("solid", fgColor="FFF2CC")
    bu_fills = {
        'CONCEPTION': PatternFill("solid", fgColor="A9D18E"),
        'TRAVAUX':    PatternFill("solid", fgColor="FFD966"),
        'MAINTENANCE': PatternFill("solid", fgColor="B4A7D6"),
        'TOTAL':      PatternFill("solid", fgColor="9DC3E6"),
    }

    # --- Légende block --------------------------------------------------
    ws['D11'] = 'Légende'
    ws['D11'].font = bold
    ws.merge_cells('D12:L14')
    ws['D12'] = LEGEND_TEXT_TEMPLATE.format(year=year)
    ws['D12'].alignment = Alignment(horizontal="left", vertical="top", wrap_text=True)
    ws['D12'].fill = legend_fill

    # --- Date row -------------------------------------------------------
    ws['D16'] = f"Au {today.strftime('%d/%m/%Y')}"
    ws['D16'].font = bold

    # --- Header band ----------------------------------------------------
    ws.merge_cells('D17:D19')
    ws['D17'] = f"Projection {year}"
    ws['D17'].font = bold
    ws['D17'].alignment = center

    bu_header_ranges = [
        ('CONCEPTION', 'E17:G17'),
        ('TRAVAUX', 'H17:J17'),
        ('MAINTENANCE', 'K17:M17'),
        ('TOTAL', 'N17:P17'),
    ]
    for label, rng in bu_header_ranges:
        ws.merge_cells(rng)
        first = rng.split(':')[0]
        ws[first] = label
        ws[first].font = bold_white
        ws[first].alignment = center
        ws[first].fill = bu_fills[label]

    # --- Subheaders (row 18) -------------------------------------------
    sub_headers = {
        'E18': 'Devis Signés',     'F18': 'Devis Potentiels', 'G18': 'Devis Envoyés',
        'H18': 'Devis Signés',     'I18': 'Devis Potentiels', 'J18': 'Devis Envoyés',
        'K18': f'Nouveaux contrats {year}', 'L18': 'Contrats Potentiels', 'M18': 'Contrats Envoyés',
        'N18': 'Devis + Contrats Signés', 'O18': 'Devis + Contrats Potentiels', 'P18': 'Devis + Contrats Envoyés',
    }
    for coord, label in sub_headers.items():
        ws[coord] = label
        ws[coord].font = bold
        ws[coord].alignment = center

    # --- Row 19 numeric values -----------------------------------------
    cells_19 = [
        ('E19', bu_totals['CONCEPTION']['signes']),
        ('F19', bu_totals['CONCEPTION']['potentiels']),
        ('G19', bu_totals['CONCEPTION']['envoyes']),
        ('H19', bu_totals['TRAVAUX']['signes']),
        ('I19', bu_totals['TRAVAUX']['potentiels']),
        ('J19', bu_totals['TRAVAUX']['envoyes']),
        ('K19', bu_totals['MAINTENANCE']['signes']),
        ('L19', bu_totals['MAINTENANCE']['potentiels']),
        ('M19', bu_totals['MAINTENANCE']['envoyes']),
    ]
    for coord, value in cells_19:
        _set_eur(ws[coord], value)
    ws['N19'] = '=E19+H19+K19'
    ws['O19'] = '=F19+I19+L19'
    ws['P19'] = '=G19+J19+M19'
    for coord in ('N19', 'O19', 'P19'):
        ws[coord].number_format = EUR_FORMAT

    # --- Row 20 Production sécurisée -----------------------------------
    ws['D20'] = 'Production sécurisée'
    ws['D20'].font = bold
    ws['E20'] = '=E19+F19'
    ws['H20'] = '=H19+I19'
    ws['K20'] = '=L19+K19'
    ws['N20'] = '=E20+H20+L22'
    for coord in ('E20', 'H20', 'K20', 'N20'):
        ws[coord].number_format = EUR_FORMAT
        ws[coord].font = bold
    for rng in ('E20:G20', 'H20:J20', 'K20:M20', 'N20:P20'):
        ws.merge_cells(rng)

    # --- Row 21 Portefeuille sites au {today} --------------------------
    ws['K21'] = f"Portefeuille sites au {today.strftime('%d/%m/%Y')}: "
    ws['K21'].alignment = Alignment(horizontal="right")
    if portefeuille_running is not None:
        _set_eur(ws['L21'], portefeuille_running)
    ws.merge_cells('L21:M21')

    # --- Row 22 Total sécurisé maintenance {YY} ------------------------
    ws['K22'] = f"Total sécurisé maintenance {str(year)[-2:]}"
    ws['K22'].alignment = Alignment(horizontal="right")
    ws['K22'].font = bold
    ws['L22'] = '=L19+L21'
    ws['L22'].number_format = EUR_FORMAT
    ws['L22'].font = bold
    ws.merge_cells('L22:M22')

    # --- Row 23 Objectifs {Y} ------------------------------------------
    ws['D23'] = f"Objectifs {year}"
    ws['D23'].font = bold

    obj_conception = objective_for_year(year, 'signe', 'bu', 'CONCEPTION')
    obj_travaux = objective_for_year(year, 'signe', 'bu', 'TRAVAUX')
    obj_maintenance = objective_for_year(year, 'signe', 'bu', 'MAINTENANCE')

    _set_eur(ws['E23'], obj_conception)
    _set_eur(ws['H23'], obj_travaux)
    _set_eur(ws['K23'], obj_maintenance)
    ws['N23'] = '=E23+H23+K23'
    ws['N23'].number_format = EUR_FORMAT
    for rng in ('E23:G23', 'H23:J23', 'K23:M23', 'N23:P23'):
        ws.merge_cells(rng)

    # --- Row 25 Production à aller chercher ----------------------------
    ws['D25'] = 'Production à aller chercher'
    ws['D25'].font = bold
    ws['E25'] = '=E23-E20'
    ws['H25'] = '=H23-H20'
    ws['K25'] = '=K23-L22'
    ws['N25'] = '=E25+H25+K25'
    for coord in ('E25', 'H25', 'K25', 'N25'):
        ws[coord].number_format = EUR_FORMAT
        ws[coord].font = bold
    for rng in ('E25:G25', 'H25:J25', 'K25:M25', 'N25:P25'):
        ws.merge_cells(rng)


def _build_sheet2(ws, year: int, today: date,
                  portefeuille_debut: Optional[float],
                  entries: List[Dict[str, Any]]) -> None:
    """
    Layout (1-indexed rows) — mirrors the "Maintenance" tab:

      B2:F2           : "Entrées/Sortie Portefeuille sites" (merged title)
      Row 3 headers   : A3 "Au DD/MM/YY" (merged A3:A{last_entry_row}),
                        B3 Nom | C3 Montant HT Prod {Y} | D3 Montant HT |
                        E3 Mois signature | F3 Mois démarrage
      Row 4           : "Total contrat de maintenance au 1er Janvier {Y}",
                        C4=D4=portefeuille_debut
      Rows 5..        : one per entry
      Last 2 rows     : totals (SUM of entries; matching reference's two totals)
    """
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    ws.title = "Maintenance"

    width_map = {'A': 14.0, 'B': 40.6, 'C': 32.9, 'D': 18.0, 'E': 16.0, 'F': 16.0}
    for col, w in width_map.items():
        ws.column_dimensions[col].width = w

    bold = Font(bold=True)
    center = Alignment(horizontal="center", vertical="center", wrap_text=True)
    title_fill = PatternFill("solid", fgColor="B4A7D6")
    header_fill = PatternFill("solid", fgColor="D9E1F2")

    ws.merge_cells('B2:F2')
    ws['B2'] = 'Entrées/Sortie Portefeuille sites'
    ws['B2'].font = Font(bold=True, color="FFFFFF")
    ws['B2'].alignment = center
    ws['B2'].fill = title_fill

    headers = {
        'A3': f"Au {today.strftime('%d/%m/%y')}",
        'B3': 'Nom',
        'C3': f"Montant HT Prod {year}",
        'D3': 'Montant HT',
        'E3': 'Mois signature',
        'F3': 'Mois démarrage',
    }
    for coord, label in headers.items():
        ws[coord] = label
        ws[coord].font = bold
        ws[coord].alignment = center
        ws[coord].fill = header_fill

    ws['B4'] = f"Total contrat de maintenance au 1er Janvier {year} "
    ws['B4'].font = bold
    if portefeuille_debut is not None:
        _set_eur(ws['C4'], portefeuille_debut)
        _set_eur(ws['D4'], portefeuille_debut)

    start_row = 5
    for i, entry in enumerate(entries):
        r = start_row + i
        ws.cell(row=r, column=2, value=entry.get("nom", ""))
        _set_eur(ws.cell(row=r, column=3), entry.get("montant_ht_prod"))
        _set_eur(ws.cell(row=r, column=4), entry.get("montant_ht"))
        ws.cell(row=r, column=5, value=entry.get("mois_signature", ""))
        ws.cell(row=r, column=6, value=entry.get("mois_demarrage", ""))

    last_entry_row = start_row + len(entries) - 1 if entries else start_row - 1

    a_merge_to = max(last_entry_row, start_row)
    if a_merge_to > 3:
        ws.merge_cells(f'A3:A{a_merge_to}')

    if entries:
        sum_row_a = last_entry_row + 1
        sum_row_b = last_entry_row + 2
        ws.cell(row=sum_row_a, column=3, value=f"=SUM(C{start_row}:C{last_entry_row})")
        ws.cell(row=sum_row_a, column=3).number_format = EUR_FORMAT
        ws.cell(row=sum_row_a, column=3).font = bold
        ws.cell(row=sum_row_a, column=4, value=f"=SUM(D{start_row}:D{last_entry_row})")
        ws.cell(row=sum_row_a, column=4).number_format = EUR_FORMAT
        ws.cell(row=sum_row_a, column=4).font = bold

        ws.cell(row=sum_row_b, column=3, value=f"=C4+SUM(C{start_row}:C{last_entry_row})")
        ws.cell(row=sum_row_b, column=3).number_format = EUR_FORMAT
        ws.cell(row=sum_row_b, column=3).font = bold
        ws.cell(row=sum_row_b, column=4, value=f"=D4+SUM(D{start_row}:D{last_entry_row})")
        ws.cell(row=sum_row_b, column=4).number_format = EUR_FORMAT
        ws.cell(row=sum_row_b, column=4).font = bold


# =============================================================================
# Public entry point
# =============================================================================

def build_budget_workbook(
    year: int,
    df_processed: pd.DataFrame,
    portefeuille_debut_annee: Optional[float],
    portefeuille_running: Optional[float],
    today: Optional[date] = None,
) -> bytes:
    """
    Build the Budget {year} xlsx and return its bytes.

    Args:
        year: Target budget year (e.g. 2026).
        df_processed: DataFrame combining WON + WAITING proposals contributing
            to year {year}. Must expose columns: ``statut_clean``/``statut``,
            ``final_bu``/``cf_bu``, ``amount``, ``Montant Total {year}``,
            ``Montant Pondéré {year}``, ``signature_date``/``date_effective_won``/
            ``date``, ``projet_start``, ``title``.
        portefeuille_debut_annee: Maintenance Entretien début {year}
            (sum of "Total HT Cette année" in Notion).
        portefeuille_running: Running maintenance portefeuille at click time.
        today: Date stamped in the workbook headers (defaults to ``date.today()``).
    """
    from openpyxl import Workbook

    today = today or date.today()

    bu_totals: Dict[str, Dict[str, float]] = {
        bu: _compute_bu_amounts(df_processed, year, bu) for bu in BU_ORDER
    }
    entries = _compute_maintenance_entries(df_processed, year)

    wb = Workbook()
    ws1 = wb.active
    _build_sheet1(ws1, year, today, bu_totals, portefeuille_debut_annee, portefeuille_running)

    ws2 = wb.create_sheet("Maintenance")
    _build_sheet2(ws2, year, today, portefeuille_debut_annee, entries)

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
