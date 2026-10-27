"""
Google Sheets side of the forecast: read the spreadsheet into a ``SheetState``, write an ``Output``.

Writes go in a few batched calls (values RAW, formulas USER_ENTERED in the sheet's fr_FR syntax,
then one formatting batch), to stay far below the Sheets quota the live dashboard shares.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Dict, List, Optional, Sequence, Tuple

from . import months as M
from .build import FinanceProject, Output, Params, SheetState
from .layout import (
    BLUE_INPUT, GREY_CALC, HEADER_BG, HEADER_FG, HIDDEN_TABS, P_FALLBACK, P_KV_FIRST_ROW, P_KV_ROWS,
    P_LAST_CLOSED, P_NEW_PROJECTS_SINCE, P_OBJ_HEADER_ROW, POLES, RED_TEXT, ROW_COST, ROW_FIELDS, ROW_FIRST,
    ROW_HEADER, ROW_LEGEND, ROW_PIPE, ROW_SIGNED, ROW_TITLE, ROW_TOTAL, TAB_ACTUALS, TAB_CALC,
    TAB_CHECKS, TAB_HOME, TAB_INPUTS, TAB_ORDER, TAB_PARAMS, TAB_RENDER, TAB_SUMMARY, WHITE, Col,
    INPUT_HEX, OBJECTIVE_LINE, POLE_COLORS, TYPED_BG, TYPED_HEX, rgb,
    HEALTH, HEALTH_TEXT, POLE_TAB_LIST, POLE_TABS, TAB_CONTRACTS, TAB_POLE,
    a1, col_letter, pole_columns, quarters, quote,
)
from .engine import myrium_allocation
from .model import Row, Shown

EPOCH = date(1899, 12, 30)
LEGEND_BLUE = "Bleu : modifiable"
LEGEND_TYPED = "Orange : saisi à la main, gardé tel quel (videz la cellule pour revenir au calcul automatique)"
LEGEND_WHITE = "Blanc : calculé, ne pas modifier"
SIGNED_TEXT = {"red": 0.10, "green": 0.42, "blue": 0.24}
DARK_TEXT = {"red": 0.13, "green": 0.13, "blue": 0.13}
GREY_TEXT = {"red": 0.40, "green": 0.40, "blue": 0.40}
PIPE_TEXT = {"red": 0.72, "green": 0.40, "blue": 0.03}


def type_label(row: Row, category: str) -> str:
    """What the line is, in words: signed project, pending devis, contract, signed TS..."""
    words = set(str(row.typologie or "").upper().replace(",", " ").split())
    if row.kind == "Projet":
        return "Projet signé"
    if row.kind == "Devis":
        if "TS" in words:
            return "Devis TS en attente"
        if "ANIMATION" in words:
            return "Devis animation en attente"
        return "Devis en attente"
    if row.kind == "TS":
        return "TS signé"
    if row.kind == "Animation":
        return "Animation signée"
    if row.kind == "Contrat":
        return "Contrat d'entretien" if not row.inactive else "Contrat inactif"
    return "Ligne manuelle (signée)" if category == "Signé" else "Ligne manuelle (pipe)"
FORMATS = {
    "money": {"type": "NUMBER", "pattern": '#,##0" €";-#,##0" €";""'},
    "month": {"type": "NUMBER", "pattern": '#,##0" €";-#,##0" €";""'},
    "pct": {"type": "PERCENT", "pattern": "0%"},
    "kv": {"type": "NUMBER", "pattern": "0.00"},
    "date": {"type": "DATE", "pattern": "mmm yyyy"},
    "int": {"type": "NUMBER", "pattern": "0"},
    "day": {"type": "DATE", "pattern": "dd/mm/yyyy"},
}

# Réalisé tab: accounting table (rows 5-7 production, 11-13 costs), projects table from row 17
ACT_FIELDS_ROW, ACT_PROD_FIRST, ACT_COST_FIRST = 3, 5, 11
ACT_PROJ_HEADER, ACT_PROJ_FIRST = 16, 17
ACT_PROJ_COLS = ["Pôle", "N° projet", "Libellé", "Production cumulée à la clôture (€)", "Clôture (mois)", "État", "CA connu de la finance (€)", "Commentaire"]


def serial(d: date) -> int:
    return (d - EPOCH).days


def month_serial(month_key: Optional[str]):
    if not month_key:
        return ""
    y, m = M.parse(month_key)
    return serial(date(y, m, 1))


def cell_day(value) -> Optional[str]:
    """A day typed in a date cell (serial number, "2027-06-30" or "30/06/2027") as "2027-06-30"."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return date.fromordinal(EPOCH.toordinal() + int(value)).isoformat()
    text = str(value).strip()
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        pass
    parts = [p for p in text.replace("-", "/").split("/") if p]
    try:
        if len(parts) == 3 and len(parts[2]) == 4:
            return date(int(parts[2]), int(parts[1]), int(parts[0])).isoformat()
    except ValueError:
        return None
    return None


def cell_month(value) -> Optional[str]:
    """A month typed in a date cell: serial number, "2027-01", "01/2027" or "01/01/2027"."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        d = date.fromordinal(EPOCH.toordinal() + int(value))
        return M.key(d.year, d.month)
    text = str(value).strip()
    if M.is_key(text):
        return text
    parts = [p for p in text.replace("-", "/").split("/") if p]
    try:
        if len(parts) == 2:
            return M.key(int(parts[1]), int(parts[0]))
        if len(parts) == 3:
            return M.key(int(parts[2]) if len(parts[2]) == 4 else int(parts[0]), int(parts[1]))
    except ValueError:
        return None
    return None


def _num(value) -> Optional[float]:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(" ", "").replace(" ", "").replace("€", "").replace(",", "."))
    except ValueError:
        return None


def editable_fields(row: Row, pole: str, grid: Sequence[str]) -> List[str]:
    fields = ["kv", "cost", "comment"] + list(grid)
    if row.category == "Pipe" or row.kind == "Manuel":
        fields.append("proba")
    if row.kind not in ("Contrat", "TS", "Animation"):
        fields += ["start", "stop"]
    if row.kind == "Contrat":
        fields += ["health", "confirmed_until"]
    if pole == "Travaux":
        fields.append("mai")
    if row.kind == "Manuel":
        fields += ["cat", "label", "ca"]
    return fields


def manual_fields(pole: str, grid: Sequence[str]) -> List[str]:
    dummy = Row(ref="", pole=pole, category="Signé", kind="Manuel", label="")
    return editable_fields(dummy, pole, grid)


def normalize_typed(field: str, value):
    """Typed values as stored in _saisies: months for dates, numbers for amounts, text otherwise."""
    if value in (None, ""):
        return None
    if field in ("start", "stop"):
        return cell_month(value)
    if field == "confirmed_until":
        return cell_day(value)
    if field in ("cat", "label", "comment", "health"):
        return str(value).strip()
    x = _num(value)
    if field == "proba" and x is not None and x > 1:  # "30" typed for 30 %
        x = x / 100
    return x


@dataclass
class Rendered:
    values: List[List[object]]
    formulas: List[Tuple[str, str]]  # (A1 cell, formula)
    backgrounds: List[List[dict]]  # data rows x columns
    red_cells: List[Tuple[int, int]]  # (row0, col0) with red text
    type_colors: List[Tuple[int, int, dict]]  # (row0, col0, colour) of the Type cells
    rendered: Dict[str, Dict[str, object]]
    calc: Dict[str, Dict[str, object]]
    columns: List[Col]


def render_pole(pole: str, items: List[Tuple[Row, Shown]], grid: List[str], open_from: str, updated_at: datetime,
                fallback: int) -> Rendered:
    cols = pole_columns(grid)
    idx = {c.field: i for i, c in enumerate(cols)}
    n = len(items)
    last = ROW_FIRST + max(n, 1) - 1
    width = len(cols)
    values: List[List[object]] = [[""] * width for _ in range(last)]
    values[ROW_TITLE - 1][0] = f"{pole} · prévision de production"
    values[ROW_TITLE - 1][idx["label"]] = (
        f"Mis à jour le {updated_at:%d/%m/%Y à %H:%M} · mois ouverts de {M.label(open_from)} à {M.label(grid[-1])}"
    )
    values[ROW_LEGEND - 1][0] = LEGEND_BLUE
    values[ROW_LEGEND - 1][idx["label"]] = LEGEND_TYPED
    values[ROW_LEGEND - 1][idx["owner"]] = LEGEND_WHITE
    values[ROW_FIELDS - 1] = [c.field for c in cols]
    labels = {ROW_SIGNED: "Signé (€)", ROW_PIPE: "Pipe pondéré par la proba (€)", ROW_TOTAL: "Total prévu (€)",
              ROW_COST: "Coût prévu, pipe pondéré (€)"}
    for r, text in labels.items():
        values[r - 1][0] = text  # merged over the frozen columns A to D
    values[ROW_HEADER - 1] = [c.title.replace("{year}", str(M.year_of(open_from))) for c in cols]

    cat_l, proba_l, kv_l = col_letter(idx["cat"]), col_letter(idx["proba"]), col_letter(idx["kv"])
    rng = lambda letter: f"{letter}{ROW_FIRST}:{letter}{last}"
    formulas: List[Tuple[str, str]] = []
    year = M.year_of(open_from)
    this_year = [m for m in grid if M.year_of(m) == year]
    groups = {"this_year": this_year, **{fid: months for fid, _, months in quarters(grid)}}  # columns that add months up
    sum_fields = list(grid) + ["remaining", "beyond"] + list(groups)
    for f in sum_fields:
        L = col_letter(idx[f])
        formulas.append((f"{L}{ROW_SIGNED}", f'=SUMIFS({rng(L)};{rng(cat_l)};"Signé")'))
        formulas.append((f"{L}{ROW_PIPE}", f'=SUMPRODUCT(ARRAYFORMULA(({rng(cat_l)}="Pipe")*{rng(L)}*{rng(proba_l)}))'))
        formulas.append((f"{L}{ROW_TOTAL}", f"={L}{ROW_SIGNED}+{L}{ROW_PIPE}"))
        if M.is_key(f):
            formulas.append((
                f"{L}{ROW_COST}",
                f'=SUMPRODUCT(ARRAYFORMULA((({rng(cat_l)}="Signé")+({rng(cat_l)}="Pipe")*{rng(proba_l)})*{rng(L)}'
                f"/IF({rng(kv_l)}>0;{rng(kv_l)};1)))",
            ))
        elif f in groups and groups[f]:
            a, b = col_letter(idx[groups[f][0]]), col_letter(idx[groups[f][-1]])
            formulas.append((f"{L}{ROW_COST}", f"=SUM({a}{ROW_COST}:{b}{ROW_COST})"))

    first_m, last_m = col_letter(idx[grid[0]]), col_letter(idx[grid[-1]])
    backgrounds: List[List[dict]] = []
    red_cells: List[Tuple[int, int]] = []
    type_colors: List[Tuple[int, int, dict]] = []
    rendered: Dict[str, Dict[str, object]] = {}
    calc: Dict[str, Dict[str, object]] = {}
    for k, (row, s) in enumerate(items):
        r = ROW_FIRST + k
        v = values[r - 1]
        cells = {
            "ref": row.ref, "cat": s.category, "type": type_label(row, s.category), "label": s.label, "owner": row.owner,
            "typo": row.typologie, "ca": s.ca, "proba": s.proba if s.category == "Pipe" else "",
            "mai": s.mai if pole == "Travaux" else "", "produced": row.produced if row.kind == "Projet" else "",
            "remaining": s.remaining, "start": month_serial(s.start), "stop": month_serial(s.stop), "kv": s.kv,
            "cost": s.cost, "beyond": s.beyond or "", "gap": s.gap or "", "alerts": " · ".join(dict.fromkeys(s.alerts)),
            "comment": s.comment,
        }
        cells.update({m: s.months.get(m, 0.0) for m in grid})
        for f, value in row.extra.items():
            if f in idx:
                cells[f] = serial(value) if isinstance(value, date) else ("" if value is None else value)
        for f, value in cells.items():
            v[idx[f]] = value
        for f, months in groups.items():
            if months:
                a, b = col_letter(idx[months[0]]), col_letter(idx[months[-1]])
                formulas.append((f"{col_letter(idx[f])}{r}", f"=SUM({a}{r}:{b}{r})"))
        type_colors.append((r - 1, idx["type"], SIGNED_TEXT if s.category == "Signé" else PIPE_TEXT))
        if row.extra.get("health") in HEALTH_TEXT and "health" in idx:
            type_colors.append((r - 1, idx["health"], rgb(HEALTH_TEXT[row.extra["health"]])))
        formulas.append((f"{col_letter(idx['cost_next'])}{r}", f"=IFERROR(SUM({first_m}{r}:{last_m}{r})/{kv_l}{r};0)"))
        formulas.append((f"{col_letter(idx['margin_next'])}{r}",
                         f"=SUM({first_m}{r}:{last_m}{r})-{col_letter(idx['cost_next'])}{r}"))

        editable = editable_fields(row, pole, grid)
        typed = set(s.pinned) | set(s.overridden) | ({"comment"} if s.comment and s.comment != row.comment else set())
        bg_row = []
        for c in cols:
            if c.field in editable:
                bg_row.append(TYPED_BG if c.field in typed else BLUE_INPUT)
            else:
                bg_row.append(WHITE)
        backgrounds.append(bg_row)
        if s.gap:
            red_cells.append((r - 1, idx["gap"]))
        if s.alerts:
            red_cells.append((r - 1, idx["alerts"]))

        shown = {}
        for f in editable:
            shown[f] = (cell_month(cells[f]) if f in ("start", "stop") else
                        cell_day(cells[f]) if f == "confirmed_until" else cells[f])
        shown["label"] = s.label
        rendered[row.ref] = shown
        calc[row.ref] = {
            "kind": row.kind, "cat": row.category, "rule": row.rule, "produced": row.produced, "ca": row.ca,
            "mai": row.mai, "proba": row.proba, "kv_default": row.kv_default, "start": row.start, "stop": row.stop,
            "proposal_date": row.proposal_date, "closed": row.closed,
            "fixed": row.fixed,
            "alloc": None if row.fixed is not None else myrium_allocation(row.rule, row.ca if row.ca > 0 else 1.0, row.start,
                                                                          row.stop, row.proposal_date),
        }
    return Rendered(values, formulas, backgrounds, red_cells, type_colors, rendered, calc, cols)


class ForecastSheet:
    """Reads and writes the forecast spreadsheet."""

    def __init__(self, spreadsheet_id: str, client=None):
        if client is None:
            from src.integrations.google_sheets import GoogleSheetsClient

            client = GoogleSheetsClient().client
        self.ss = client.open_by_key(spreadsheet_id)

    # ------------------------------------------------------------------ metadata
    def sheet_ids(self) -> Dict[str, int]:
        meta = self.ss.fetch_sheet_metadata(params={"fields": "sheets(properties(sheetId,title))"})
        return {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta["sheets"]}

    def ensure_tabs(self) -> Dict[str, int]:
        """Create the missing tabs and give every tab room for 1 500 rows and 80 columns."""
        meta = self.ss.fetch_sheet_metadata(params={"fields": "sheets(properties(sheetId,title,gridProperties))"})
        props = {s["properties"]["title"]: s["properties"] for s in meta["sheets"]}
        requests = []
        for title in TAB_ORDER:
            if title not in props:
                requests.append({"addSheet": {"properties": {"title": title, "gridProperties": {"rowCount": 1500, "columnCount": 80},
                                                             "hidden": title in HIDDEN_TABS}}})
            else:
                grid = props[title].get("gridProperties", {})
                if grid.get("rowCount", 0) < 1500 or grid.get("columnCount", 0) < 80:
                    requests.append({"updateSheetProperties": {"properties": {"sheetId": props[title]["sheetId"], "gridProperties": {
                        "rowCount": max(1500, grid.get("rowCount", 0)), "columnCount": max(80, grid.get("columnCount", 0))}},
                        "fields": "gridProperties(rowCount,columnCount)"}})
        if requests:
            self.ss.batch_update({"requests": requests})
        return self.sheet_ids()

    def _own_tabs(self, ids: Dict[str, int]) -> List[str]:
        """Tabs this program built: they carry its marker in A1 (or are the hidden tabs)."""
        own = []
        titles = [t for t in TAB_ORDER if t in ids]
        if not titles:
            return own
        got = self.ss.values_batch_get([f"{quote(t)}!A1" for t in titles])["valueRanges"]
        markers = ("Prévision de production", "Synthèse de production", "Réalisé (saisi", "Paramètres de la prévision", "Pôle", "Réf.")
        for t, g in zip(titles, got):
            a1_value = str((g.get("values") or [[""]])[0][0] if g.get("values") else "")
            if t in HIDDEN_TABS or any(a1_value.startswith(m) for m in markers) or "· prévision de production" in a1_value:
                own.append(t)
        return own

    # ------------------------------------------------------------------ read
    def read_state(self) -> SheetState:
        ranges = [f"{quote(p)}!A1:BZ1500" for p in POLE_TABS] + [
            f"{quote(TAB_PARAMS)}!A1:P60", f"{quote(TAB_ACTUALS)}!A1:AO2000",
            f"{quote(TAB_INPUTS)}!A1:B3000", f"{quote(TAB_RENDER)}!A1:B3000",
        ]
        got = self.ss.values_batch_get(ranges, params={"valueRenderOption": "UNFORMATTED_VALUE",
                                                       "dateTimeRenderOption": "SERIAL_NUMBER"})["valueRanges"]
        grids = [g.get("values", []) for g in got]
        visible: Dict[str, List[Dict[str, object]]] = {}
        n_tabs = len(POLE_TABS)
        for pole, rows in zip(POLE_TABS, grids[:n_tabs]):
            if len(rows) < ROW_FIELDS:
                visible[pole] = []
                continue
            fields = [str(x) for x in rows[ROW_FIELDS - 1]]
            data = []
            for raw in rows[ROW_FIRST - 1:]:
                cells = {f: (raw[i] if i < len(raw) else "") for i, f in enumerate(fields) if f}
                for f in ("start", "stop"):
                    if f in cells:
                        cells[f] = cell_month(cells[f])
                if "confirmed_until" in cells:
                    cells["confirmed_until"] = cell_day(cells["confirmed_until"])
                if any(v not in (None, "") for v in cells.values()):
                    data.append(cells)
            visible[pole] = data
        params = parse_params(grids[n_tabs])
        pl, pl_costs, finance, pl_columns = parse_actuals(grids[n_tabs + 1])
        def json_rows(rows):
            out = {}
            for r in rows:
                if len(r) > 1 and r[0] and r[1] and r[0] != "Réf.":
                    try:
                        out[str(r[0])] = json.loads(r[1])
                    except ValueError:
                        continue
            return out

        inputs, rendered = json_rows(grids[n_tabs + 2]), json_rows(grids[n_tabs + 3])
        return SheetState(params, finance, pl, pl_costs, inputs, rendered, visible, pl_columns)

    # ------------------------------------------------------------------ write
    def write(self, output: Output, state: SheetState, updated_at: datetime) -> None:
        ids = self.ensure_tabs()
        data_raw, data_formulas, fmt = [], [], []
        all_rendered: Dict[str, Dict[str, object]] = {}
        all_calc: Dict[str, Dict[str, object]] = {"__meta": {
            "grid": output.grid, "open_from": output.open_from, "fallback": state.params.fallback_months,
            "pending_from": output.pending_from or output.open_from,
            "colors": {"typed": TYPED_HEX, "input": INPUT_HEX},
            "updated_at": updated_at.isoformat(timespec="minutes")}}
        clears = []
        for pole in POLE_TABS:
            rend = render_pole(pole, output.rows.get(pole, []), output.grid, output.open_from, updated_at,
                               state.params.fallback_months)
            all_rendered.update(rend.rendered)
            all_calc.update(rend.calc)
            clears.append(f"{quote(pole)}!A1:BZ1500")
            data_raw.append({"range": f"{quote(pole)}!A1", "values": rend.values})
            data_formulas += [{"range": f"{quote(pole)}!{cell}", "values": [[f]]} for cell, f in rend.formulas]
            fmt += pole_format_requests(ids[pole], rend, pole)

        typed = {ref: v for ref, v in output.inputs.items() if v}
        clears += [f"{quote(TAB_INPUTS)}!A1:B3000", f"{quote(TAB_RENDER)}!A1:B3000", f"{quote(TAB_CALC)}!A1:B3000",
                   f"{quote(TAB_CHECKS)}!A1:E2000", f"{quote(TAB_SUMMARY)}!A1:BZ300"]
        data_raw.append({"range": f"{quote(TAB_INPUTS)}!A1", "values": [["Réf.", "Saisies (JSON)"]] +
                         [[ref, json.dumps(v, ensure_ascii=False)] for ref, v in sorted(typed.items())]})
        data_raw.append({"range": f"{quote(TAB_RENDER)}!A1", "values": [["Réf.", "Rendu (JSON)"]] +
                         [[ref, json.dumps(v, ensure_ascii=False)] for ref, v in sorted(all_rendered.items())]})
        data_raw.append({"range": f"{quote(TAB_CALC)}!A1", "values":
                         [[ref, json.dumps(v, ensure_ascii=False, default=str)] for ref, v in all_calc.items()]})
        check_rows = [["Pôle", "Réf.", "Libellé", "Point à vérifier"]] + [list(c) for c in output.checks]
        if output.notes:
            check_rows += [[""], ["Journal de la dernière mise à jour"]] + [[n] for n in output.notes]
        data_raw.append({"range": f"{quote(TAB_CHECKS)}!A1", "values": check_rows})
        summary_values, summary_formulas, summary_styled = render_summary(output, state, updated_at)
        data_raw.append({"range": f"{quote(TAB_SUMMARY)}!A1", "values": summary_values})
        data_formulas += [{"range": f"{quote(TAB_SUMMARY)}!{cell}", "values": [[f]]} for cell, f in summary_formulas]
        new_fin = new_finance_rows(state.finance, output.finance)
        if new_fin:
            start = ACT_PROJ_FIRST + len(state.finance)
            data_raw.append({"range": f"{quote(TAB_ACTUALS)}!A{start}", "values": new_fin})
        home = render_home()
        home[2][1] = f"{updated_at:%d/%m/%Y à %H:%M}"
        clears.append(f"{quote(TAB_HOME)}!A1:B80")
        data_raw.append({"range": f"{quote(TAB_HOME)}!A1", "values": home})
        swatches = {"Bleu": BLUE_INPUT, "Orange": TYPED_BG, "Blanc": WHITE,
                    **{label: rgb(POLE_COLORS[pole]["strong"]) for label, pole in HOME_POLE_LINES.items()}}
        fmt.append(_repeat(ids[TAB_HOME], 0, 80, 0, 1, {"backgroundColor": WHITE, "textFormat": {"bold": False}},
                           "userEnteredFormat(backgroundColor,textFormat)"))
        for i, line in enumerate(home):
            if line and line[0] in swatches:
                fmt.append(_repeat(ids[TAB_HOME], i, i + 1, 0, 1, {"backgroundColor": swatches[line[0]], "textFormat": {"bold": True}},
                                   "userEnteredFormat(backgroundColor,textFormat)"))
            elif line and line[0] in ("À quoi sert ce fichier", "Qui fait quoi", "Les couleurs", "Règles de calcul",
                                      "Les références (colonne Réf.)", "Quelles lignes apparaissent"):
                fmt.append(_repeat(ids[TAB_HOME], i, i + 1, 0, 1, {"textFormat": {"bold": True, "fontSize": 12}},
                                   "userEnteredFormat.textFormat"))
            elif line and line[0] == "Texte vert / orange":
                fmt.append(_repeat(ids[TAB_HOME], i, i + 1, 0, 1, {"textFormat": {"bold": True, "foregroundColor": SIGNED_TEXT}},
                                   "userEnteredFormat.textFormat"))
            elif line and line[0] == "Texte rouge":
                fmt.append(_repeat(ids[TAB_HOME], i, i + 1, 0, 1, {"textFormat": {"bold": True, "foregroundColor": RED_TEXT}},
                                   "userEnteredFormat.textFormat"))

        self.ss.values_batch_clear(body={"ranges": clears})
        self.ss.values_batch_update({"valueInputOption": "RAW", "data": data_raw})
        if data_formulas:
            self.ss.values_batch_update({"valueInputOption": "USER_ENTERED", "data": data_formulas})
        fmt += summary_format_requests(ids[TAB_SUMMARY], output, summary_styled)
        fmt += self._chart_requests(ids[TAB_SUMMARY], summary_styled.get("chart_rows", [13])[0])
        fmt += self._pole_cell_requests(ids)
        if fmt:
            self.ss.batch_update({"requests": fmt})

    def _chart_requests(self, sheet_id: int, n_rows: int) -> List[dict]:
        """The "all poles" chart, then one chart per pole, each in its colours (data in hidden columns)."""
        def rng(col0, rows=n_rows):
            return {"sources": [{"sheetId": sheet_id, "startRowIndex": CHART_ROW - 1, "endRowIndex": CHART_ROW - 1 + rows,
                                 "startColumnIndex": col0, "endColumnIndex": col0 + 1}]}

        def spec(k: int, name: str, title: str) -> dict:
            c0 = CHART_COL + CHART_BLOCK * k
            shades = POLE_COLORS[name]["chart"]
            series = [{"series": {"sourceRange": rng(c0 + 1 + i)}, "targetAxis": "LEFT_AXIS", "type": "COLUMN",
                       "colorStyle": {"rgbColor": rgb(shades[i])}} for i in range(3)]
            series.append({"series": {"sourceRange": rng(c0 + 4)}, "targetAxis": "LEFT_AXIS", "type": "LINE",
                           "colorStyle": {"rgbColor": rgb(OBJECTIVE_LINE)}, "lineStyle": {"type": "MEDIUM_DASHED", "width": 2}})
            return {"title": title, "hiddenDimensionStrategy": "SHOW_ALL",
                    "basicChart": {"chartType": "COMBO", "legendPosition": "BOTTOM_LEGEND", "headerCount": 1, "stackedType": "STACKED",
                                   "axis": [{"position": "BOTTOM_AXIS"}, {"position": "LEFT_AXIS"}],
                                   "domains": [{"domain": {"sourceRange": rng(c0)}}], "series": series}}

        def position(row: int, offset_x: int, width: int, height: int) -> dict:
            return {"overlayPosition": {"anchorCell": {"sheetId": sheet_id, "rowIndex": row - 1, "columnIndex": 0},
                                        "offsetXPixels": offset_x, "widthPixels": width, "heightPixels": height}}

        charts = [(spec(0, "Total", "Tous pôles : production par mois (€), réalisé, signé, pipe pondéré et objectif"),
                   position(CHART_TOP_ROW, 0, 1250, 380))]
        for i, pole in enumerate(POLES):
            charts.append((spec(1 + i, pole, f"{pole} : production par mois (€)"), position(POLE_CHARTS_TOP_ROW, 630 * i, 620, 340)))
        meta = self.ss.fetch_sheet_metadata(params={"fields": "sheets(properties(sheetId),charts(chartId))"})
        old = [c["chartId"] for sh in meta["sheets"] if sh["properties"]["sheetId"] == sheet_id for c in sh.get("charts", [])]
        # Recreated on every run: an update cannot move an anchor back to column A (a 0 index is ignored).
        return [{"deleteEmbeddedObject": {"objectId": cid}} for cid in old] + \
            [{"addChart": {"chart": {"spec": sp, "position": pos}}} for sp, pos in charts]

    def _pole_cell_requests(self, ids: Dict[str, int]) -> List[dict]:
        """Colour the "Pôle" cells (column A) of Réalisé, Paramètres and Contrôles with their pole's colour.

        Conditional rules, so rows finance adds by hand get their colour too. The rules written by the previous
        run are deleted first (recognised by their shape: column A only, "text is exactly <pole>").
        """
        targets = {ids[TAB_ACTUALS]: ACT_PROD_FIRST - 1, ids[TAB_PARAMS]: P_OBJ_HEADER_ROW, ids[TAB_CHECKS]: 1}
        names = list(POLES) + [t for t in POLE_TABS if t not in POLES]  # Contrôles names the tab
        meta = self.ss.fetch_sheet_metadata(params={"fields": "sheets(properties(sheetId),conditionalFormats)"})
        req: List[dict] = []
        for sh in meta["sheets"]:
            sid = sh["properties"]["sheetId"]
            if sid not in targets:
                continue
            for i, rule in reversed(list(enumerate(sh.get("conditionalFormats", [])))):
                cond = rule.get("booleanRule", {}).get("condition", {})
                values = [v.get("userEnteredValue") for v in cond.get("values", [])]
                if cond.get("type") == "TEXT_EQ" and values and values[0] in names and \
                        all(r.get("startColumnIndex", 0) == 0 and r.get("endColumnIndex") == 1 for r in rule.get("ranges", [])):
                    req.append({"deleteConditionalFormatRule": {"sheetId": sid, "index": i}})
        for sid, first_row in targets.items():
            for pole in names:
                req.append({"addConditionalFormatRule": {"index": 0, "rule": {
                    "ranges": [{"sheetId": sid, "startRowIndex": first_row, "endRowIndex": 2000, "startColumnIndex": 0, "endColumnIndex": 1}],
                    "booleanRule": {"condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": pole}]},
                                    "format": {"backgroundColor": rgb(POLE_COLORS[pole]["light"])}}}}})
        return req

    # ------------------------------------------------------------------ start-up
    def initialize(self, params: Params, pl: Dict[str, Dict[str, float]], finance: List[FinanceProject],
                   inputs: Dict[str, Dict[str, object]], first_year: int, last_year: int, delete_others: bool,
                   pl_costs: Optional[Dict[str, Dict[str, float]]] = None) -> None:
        ids = self.sheet_ids()
        clash = [t for t in TAB_ORDER if t in ids and t not in self._own_tabs(ids)]
        if clash:  # an old tab with a new tab's name (the workbook's "Synthèse"): rename it first
            self.ss.batch_update({"requests": [{"updateSheetProperties": {"properties": {"sheetId": ids[t], "title": f"{t} (ancien)"},
                                                                          "fields": "title"}} for t in clash]})
        ids = self.ensure_tabs()
        data = [
            {"range": f"{quote(TAB_PARAMS)}!A1", "values": render_params(params, first_year, last_year)},
            {"range": f"{quote(TAB_ACTUALS)}!A1", "values": render_actuals(pl, finance, params.last_closed, first_year, last_year, pl_costs)},
            {"range": f"{quote(TAB_HOME)}!A1", "values": render_home()},
            {"range": f"{quote(TAB_INPUTS)}!A1", "values": [["Réf.", "Saisies (JSON)"]] +
             [[ref, json.dumps(v, ensure_ascii=False)] for ref, v in sorted(inputs.items())]},
        ]
        self.ss.values_batch_clear(body={"ranges": [f"{quote(t)}!A1:AZ2000" for t in (TAB_PARAMS, TAB_ACTUALS, TAB_HOME, TAB_INPUTS, TAB_RENDER)]})
        self.ss.values_batch_update({"valueInputOption": "RAW", "data": data})
        obj_formulas = []
        row = P_OBJ_HEADER_ROW + 1
        for year in range(first_year, last_year + 1):
            for pole in POLES:
                for m in range(12):
                    obj_formulas.append({"range": f"{quote(TAB_PARAMS)}!{col_letter(4 + m)}{row}", "values": [[f"=$C{row}/12"]]})
                row += 1
        self.ss.values_batch_update({"valueInputOption": "USER_ENTERED", "data": obj_formulas})
        requests = static_format_requests(ids, first_year, last_year)
        if delete_others:
            keep = set(TAB_ORDER)
            requests += [{"deleteSheet": {"sheetId": sid}} for title, sid in ids.items() if title not in keep]
        order = [ids[t] for t in TAB_ORDER]
        requests += [{"updateSheetProperties": {"properties": {"sheetId": sid, "index": i}, "fields": "index"}}
                     for i, sid in enumerate(order)]
        self.ss.batch_update({"requests": requests})


# ---------------------------------------------------------------------- params & actuals

def render_params(params: Params, first_year: int, last_year: int) -> List[List[object]]:
    rows: List[List[object]] = [[""] * 16 for _ in range(P_OBJ_HEADER_ROW + 3 * (last_year - first_year + 1))]
    rows[0][0] = "Paramètres de la prévision"
    rows[1][0] = "Modifiables par la finance. Les cellules en bleu pilotent tous les onglets."
    rows[2][0] = "Clôture et fenêtre"
    rows[3][:2] = ["Dernier mois clos par la finance (AAAA-MM)", params.last_closed]
    rows[4][:2] = ["Mois de rattrapage quand les dates Furious sont dépassées", params.fallback_months]
    rows[5][:2] = ["Projets Furious ajoutés seuls s'ils sont créés à partir du", serial(params.new_projects_since) if params.new_projects_since else ""]
    rows[6][:2] = ["Un devis à 0 % dans Furious compte pour", 0.5]
    rows[P_KV_FIRST_ROW - 2][0] = "KV par défaut (coût = production ÷ KV)"
    for i, (name, value) in enumerate(list(params.kv.items())[:P_KV_ROWS]):
        rows[P_KV_FIRST_ROW - 1 + i][:2] = [name, value]
    rows[P_OBJ_HEADER_ROW - 2][0] = "Objectifs de production (le mensuel vaut 1/12 par défaut, modifiable)"
    rows[P_OBJ_HEADER_ROW - 1] = ["Pôle", "Année", "Objectif annuel (€)", "KV objectif"] + [M.MONTH_LABELS_FR[m] for m in range(12)]
    r = P_OBJ_HEADER_ROW
    for year in range(first_year, last_year + 1):
        for pole in POLES:
            amount, kv, _ = params.objectives.get((pole, year), (0.0, 0.0, {}))
            rows[r][:4] = [pole, year, amount or "", kv or ""]
            r += 1
    return rows


def parse_params(rows: List[List[object]]) -> Params:
    def cell(a1_ref: str):
        col = ord(a1_ref[0]) - 65
        row = int(a1_ref[1:]) - 1
        return rows[row][col] if row < len(rows) and col < len(rows[row]) else ""

    last_closed = str(cell(P_LAST_CLOSED) or "").strip()
    if not M.is_key(last_closed):
        last_closed = cell_month(cell(P_LAST_CLOSED)) or last_closed
    since = cell(P_NEW_PROJECTS_SINCE)
    since_date = date.fromordinal(EPOCH.toordinal() + int(since)) if isinstance(since, (int, float)) and since else None
    kv = {}
    for i in range(P_KV_ROWS):
        r = P_KV_FIRST_ROW - 1 + i
        if r < len(rows) and len(rows[r]) > 1 and rows[r][0] and _num(rows[r][1]):
            kv[str(rows[r][0])] = float(_num(rows[r][1]))
    objectives, objective_rows = {}, {}
    for r in range(P_OBJ_HEADER_ROW, len(rows)):
        line = rows[r] + [""] * 16
        if line[0] in POLES and _num(line[1]):
            monthly = {M.key(int(_num(line[1])), m + 1): _num(line[4 + m]) or 0.0 for m in range(12)}
            objectives[(str(line[0]), int(_num(line[1])))] = (_num(line[2]) or 0.0, _num(line[3]) or 0.0, monthly)
            objective_rows[(str(line[0]), int(_num(line[1])))] = r + 1
    return Params(last_closed, int(_num(cell(P_FALLBACK)) or 2), since_date, kv, objectives, objective_rows)


def actual_month_columns(first_year: int, last_year: int) -> List[str]:
    return M.span(M.key(first_year, 1), M.key(last_year, 12))


ACT_NOTE = ("Production réalisée par pôle et par mois, d'après la comptabilité (compte de résultat). « Report » = mois non "
            "détaillés (janvier à août 2026, chiffres compta du fichier d'Arthur). À chaque clôture : tapez le mois, puis avancez "
            "« Dernier mois clos » dans Paramètres. Un mois après le dernier mois clos reste une prévision.")
ACT_COST_NOTE = ("Coûts (€) · le report vaut production ÷ KV réel du 1er semestre (compta fin juin, fichier d'Arthur). "
                 "Mois vides : coût = production ÷ KV objectif.")
ACT_PROJ_NOTE = ("Production cumulée par projet à la dernière clôture, depuis le début du projet. PROVISOIRE : reprise du fichier "
                 "Forecast 2 (cut-off compta du 1er semestre + estimations des chefs de projet pour juillet et août). À "
                 "remplacer par le suivi de production de la comptabilité dès qu'il est accessible.")


def render_actuals(pl: Dict[str, Dict[str, float]], finance: List[FinanceProject], last_closed: str,
                   first_year: int, last_year: int, pl_costs: Optional[Dict[str, Dict[str, float]]] = None) -> List[List[object]]:
    months = actual_month_columns(first_year, last_year)
    width = max(2 + len(months) + 1, len(ACT_PROJ_COLS))
    rows: List[List[object]] = [[""] * width for _ in range(ACT_PROJ_FIRST - 1 + len(finance))]
    rows[0][0] = "Réalisé (saisi par la finance)"
    rows[1][0] = ACT_NOTE
    rows[ACT_FIELDS_ROW - 1][:2 + len(months)] = ["pole", "report"] + months
    rows[ACT_PROD_FIRST - 2][:2 + len(months)] = ["Production (€)", "Report (mois non détaillés)"] + [M.label(m) for m in months]
    for i, pole in enumerate(POLES):
        line = rows[ACT_PROD_FIRST - 1 + i]
        line[0] = pole
        line[1] = pl.get(pole, {}).get("report", "")
        for j, m in enumerate(months):
            line[2 + j] = pl.get(pole, {}).get(m, "")
    rows[ACT_COST_FIRST - 2][:2 + len(months)] = ["Coûts (€)", "Report"] + [M.label(m) for m in months]
    rows[ACT_COST_FIRST - 3][0] = ACT_COST_NOTE
    for i, pole in enumerate(POLES):
        line = rows[ACT_COST_FIRST - 1 + i]
        line[0] = pole
        line[1] = (pl_costs or {}).get(pole, {}).get("report", "")
        for j, m in enumerate(months):
            line[2 + j] = (pl_costs or {}).get(pole, {}).get(m, "")
    rows[ACT_PROJ_HEADER - 2][0] = ACT_PROJ_NOTE
    rows[ACT_PROJ_HEADER - 1][:len(ACT_PROJ_COLS)] = ACT_PROJ_COLS
    for i, f in enumerate(finance):
        rows[ACT_PROJ_FIRST - 1 + i][:8] = [f.pole, f.number, f.label, f.produced, f.closed_month, f.state, f.ca or "", f.comment]
    return rows


def parse_actuals(rows: List[List[object]]):
    pl: Dict[str, Dict[str, float]] = {}
    costs: Dict[str, Dict[str, float]] = {}
    fields = [str(x) for x in rows[ACT_FIELDS_ROW - 1]] if len(rows) >= ACT_FIELDS_ROW else []
    for first, target in ((ACT_PROD_FIRST, pl), (ACT_COST_FIRST, costs)):
        for i in range(3):
            r = first - 1 + i
            if r >= len(rows) or not rows[r] or rows[r][0] not in POLES:
                continue
            values = {}
            for j, f in enumerate(fields[1:], start=1):
                if j < len(rows[r]) and _num(rows[r][j]) is not None:
                    values[f] = float(_num(rows[r][j]))
            target[rows[r][0]] = values
    finance = []
    for r in rows[ACT_PROJ_FIRST - 1:]:
        line = list(r) + [""] * 8
        if line[0] in POLES and str(line[1]).strip():
            number = str(line[1]).strip()
            number = number[:-2] if number.endswith(".0") else number
            finance.append(FinanceProject(line[0], number, str(line[2]), _num(line[3]) or 0.0,
                                          cell_month(line[4]) or "", "fermé" if str(line[5]).lower().startswith("ferm") else "ouvert",
                                          _num(line[6]) or 0.0, str(line[7])))
    columns = {f: col_letter(j) for j, f in enumerate(fields) if f and f != "pole"}
    return pl, costs, finance, columns


def new_finance_rows(before: List[FinanceProject], after: List[FinanceProject]) -> List[List[object]]:
    known = {(f.pole, f.number) for f in before}
    return [[f.pole, f.number, f.label, f.produced, f.closed_month, f.state, f.ca or "", f.comment]
            for f in after if (f.pole, f.number) not in known]


HOME_POLE_LINES = {"Pôle Travaux": "Travaux", "Pôle Conception": "Conception", "Pôle Maintenance": "Maintenance",
                   "Tous pôles": "Total"}


def render_home() -> List[List[object]]:
    return [
        ["Prévision de production · Merci Raymond"],
        [""],
        ["Dernière mise à jour automatique", ""],
        [""],
        ["À quoi sert ce fichier"],
        ["Montrer chaque mois, par pôle : ce qui est produit (réalisé), ce qui est signé et reste à produire, "
         "ce qui sera probablement signé (pipe pondéré) et ce qu'il manque pour l'objectif."],
        [""],
        ["Qui fait quoi"],
        ["Chefs de projet", "Onglets Travaux, Conception, Maintenance contrats, Maintenance TS : saisir un mois (le reste se répartit seul), "
                            "la date de fin, le KV ou le coût prévu, la proba d'un devis, un commentaire."],
        ["Finance", "Onglet Réalisé : production de la comptabilité par mois et production cumulée par projet à chaque clôture. "
                    "Onglet Paramètres : dernier mois clos, KV par défaut, objectifs."],
        ["Direction", "Onglet Synthèse : lecture seule."],
        ["Serveur (chaque nuit)", "Met à jour les projets et devis Furious, les contrats Notion et les passages du planning."],
        [""],
        ["Les couleurs"],
        ["Bleu", "Cellule modifiable : elle affiche la valeur automatique (Furious, Notion, règles)."],
        ["Orange", "Valeur saisie à la main (ou reprise du fichier d'Arthur), gardée telle quelle. Videz la cellule pour revenir "
                   "au calcul automatique."],
        ["Blanc", "Calculé. Ne pas modifier : toute modification est remplacée à la mise à jour suivante. Pour changer un CA, "
                  "une date ou une probabilité de source, corrigez-la dans Furious."],
        ["Texte vert / orange", "Colonne Type : vert = signé (compte à 100 %), orange = en attente (compte à sa probabilité)."],
        ["Pôle Travaux", "Jaune : l'onglet Travaux (titre, sous-totaux, en-têtes), ses lignes et son graphique dans la Synthèse, "
                         "ses cellules « Pôle » dans Réalisé, Paramètres et Contrôles."],
        ["Pôle Conception", "Vert, aux mêmes endroits."],
        ["Pôle Maintenance", "Violet, aux mêmes endroits."],
        ["Tous pôles", "Bleu clair : les lignes « Total » et le premier graphique de la Synthèse."],
        ["Texte rouge", "Alerte ou écart à regarder (détail dans l'onglet Contrôles)."],
        [""],
        ["Les références (colonne Réf.)"],
        ["P1081", "Projet signé : n° de projet Furious."],
        ["D252703", "Devis en attente : n° de devis Furious."],
        ["T1357", "TS ou animation signé : n° de projet Furious."],
        ["S1311", "Contrat d'entretien : n° Furious du site, lu dans son titre Notion (« Cogéva - 1116 (EXT) »), sinon dans la "
                  "colonne « ID furious ». Si plusieurs sites le partagent : suivi de EXT, INT, Maraichage… (S721-EXT). Un code "
                  "interne si le site n'a aucun n°."],
        ["M3", "Ligne manuelle, tapée sous un tableau."],
        [""],
        ["Quelles lignes apparaissent"],
        ["Projets signés", "Ceux de l'onglet Réalisé (liste de la finance) qui ne sont pas fermés, plus tout projet Furious créé "
                           "après la date de Paramètres. Ils restent affichés même si tout leur reste tombe après la fenêtre."],
        ["Devis en attente", "Tous les devis Furious au statut Brief, En cours ou Envoyé (comme le pipe du dashboard), quelles "
                             "que soient leurs dates."],
        ["Contrats", "Onglet Maintenance contrats : tous les sites de la base Notion Sites, rangés par état (Safe, puis "
                     "À risque, puis Mort), les sites sans production restante en bas."],
        ["TS et animations", "Onglet Maintenance TS : projets Furious des 24 derniers mois qui ont encore de la production à "
                             "partir du mois en cours, et les devis TS en attente."],
        ["Lignes manuelles", "Celles que vous tapez."],
        [""],
        ["Règles de calcul"],
        ["Reste à produire", "Projet : CA − part maintenance − production cumulée à la dernière clôture (finance). "
                             "Devis : son montant, compté dans la synthèse à sa probabilité."],
        ["Répartition par défaut", "Règles du dashboard commercial : Travaux à parts égales entre début et fin Furious ; "
                                   "Conception par paliers selon le montant ; devis maintenance sur 12 mois. Un devis pas encore "
                                   "signé ne produit qu'à partir du mois prochain : s'il démarre plus tôt, tout son calendrier est décalé."],
        ["Devis gagné", "Quand un devis est gagné dans Furious, il quitte le pipe et son projet apparaît à la mise à jour suivante "
                        "(ligne P… ou T…). Les mois, KV, coût, dates et commentaire saisis sur le devis passent sur le projet."],
        ["Mois saisi", "Un mois saisi reste tel quel. Le reste à produire moins les mois saisis se répartit sur les autres "
                       "mois ouverts, selon la forme par défaut ; si les dates sont passées, sur les 2 prochains mois."],
        ["Maintenance", "Valeur annuelle d'un contrat d'après ses dates (règle de la colonne Notion « Total HT Cette année »), "
                        "répartie selon les passages du planning (un passage planifié = un passage fait). Contrats actifs en haut, "
                        "contrats perdus ou archivés sans production restante en bas. TS et animations : lignes à part, selon leurs dates."],
        ["Part maintenance", "Onglet Travaux, groupe « détails » : part d'un chantier qui relève de la maintenance. Elle est retirée "
                             "du reste à produire Travaux. Elle n'est ajoutée nulle part ailleurs : si elle doit compter en Maintenance, "
                             "ajouter une ligne manuelle dans l'onglet Maintenance."],
        ["Pipe", "Probabilité Furious ; 0 % compte 50 % comme dans le dashboard. Modifiable ligne par ligne."],
        ["État d'un contrat", "Safe : confirmé par le responsable de pôle pour les 1 à 3 ans qui viennent (tapez « Safe » et la "
                              "date « Confirmé jusqu'au »). À risque : pas encore confirmé, ou un signal (fin dépassée, pas de "
                              "date de fin réelle comme les tacites 2050, fin dans moins de 3 mois, « À surveiller » dans Notion…), "
                              "détaillé dans la colonne « Pourquoi ». Mort : perdu ou archivé dans Notion. Un Safe dont la date "
                              "est passée redevient À risque."],
        ["Fenêtre", "18 mois à partir du mois en cours. Les mois clos viennent de la comptabilité (onglet Réalisé)."],
    ]


# ---------------------------------------------------------------------- summary

BRIEF_FIRST = 5  # rows 5-8: poles, row 9: total; header on row 4
CHART_TOP_ROW = 11  # the "all poles" chart sits on rows 11 to 29
POLE_CHARTS_TOP_ROW = 31  # one chart per pole, side by side, rows 31 to 47
YEARS_FIRST = 50  # first year block
CHART_COL, CHART_ROW = 30, 4  # chart data from AE4 (hidden columns): Total, then one block per pole
CHART_BLOCK = 6  # columns per chart: month, réalisé, signé, pipe, objectif, gap
STATUS_COL = 10  # column K: maintenance contracts by status, next to "En bref"

TOT_COL = 18  # column S: yearly total, then objective (T), gap (U), left to sign (V)

SUMMARY_LINES = ["Réalisé (compta)", "Signé à produire", "Pipe pondéré", "Total prévu", "Objectif", "Écart au mois",
                 "Coût prévu", "KV"]


def render_summary(output: Output, state: SheetState, updated_at: datetime):
    """Year blocks: for each pole, actuals until the last closed month, then signed + weighted pipe."""
    params = state.params
    years = list(range(M.year_of(params.last_closed), M.year_of(output.grid[-1]) + 1))
    grid_cols = {c.field: col_letter(i) for i, c in enumerate(pole_columns(output.grid)) if M.is_key(c.field)}
    act_col = {k: v for k, v in state.pl_columns.items() if M.is_key(k)}
    report_year = min((M.year_of(k) for k in act_col), default=years[0])
    values: List[List[object]] = [["Synthèse de production · Merci Raymond"],
                                  [f"Réalisé = chiffres de la comptabilité jusqu'à {M.label(params.last_closed)} inclus ; ensuite prévision : "
                                   f"signé + pipe pondéré par sa probabilité. Mis à jour le {updated_at:%d/%m/%Y à %H:%M}."], [""]]
    formulas: List[Tuple[str, str]] = []
    styled: Dict[str, list] = {"header": [], "kv": [], "brief": [], "lines": [], "brief_rows": [], "notes": [], "status_rows": []}
    row = YEARS_FIRST
    width = 2 + 12 + 4 + 4  # label, report, 12 months, 4 quarters, total / objective / gap / to sign
    year_rows: Dict[int, Dict[str, Dict[str, int]]] = {}
    year_header: Dict[int, int] = {}
    for year in years:
        folded = [m for m in range(1, 13) if year == report_year and M.index(M.key(year, m)) <= M.index(params.last_closed)
                  and all(state.pl.get(p, {}).get(M.key(year, m)) is None for p in POLES)]
        short = lambda m: M.label(M.key(year, m)).split(" ")[0]
        report_title = f"Cumul {short(folded[0])} à {short(folded[-1])}" if folded else ""
        header = [f"Année {year}", report_title] + [M.label(M.key(year, m)) for m in range(1, 13)] + \
                 [f"T{q} {year}" for q in range(1, 5)] + \
                 [f"Total {year}", f"Objectif {year}", "Écart", "Manque pour l'objectif (pipe compris)"]
        while len(values) < row:
            values.append([""] * width)
        values[row - 1] = header
        styled["header"].append(row)
        year_header[year] = row
        block_rows: Dict[str, Dict[str, int]] = {}
        year_rows[year] = block_rows
        r = row + 1
        for pole in list(POLES) + ["Total"]:
            block_rows[pole] = {}
            for line in SUMMARY_LINES:
                while len(values) < r:
                    values.append([""] * width)
                values[r - 1] = [f"{pole} · {line}"] + [""] * (width - 1)
                block_rows[pole][line] = r
                styled["lines"].append((r, pole, line))
                if line == "KV":
                    styled["kv"].append(r)
                r += 1
            r += 1
        for pole in list(POLES) + ["Total"]:
            rows_ = block_rows[pole]
            for j in range(0, 13):  # 0 = report column, 1..12 months
                col = col_letter(1 + j)
                month = M.key(year, j) if j else None
                cells = {}
                if pole == "Total":
                    for line in SUMMARY_LINES:
                        if line == "KV":
                            cells[line] = f"=IFERROR({col}{rows_['Total prévu']}/{col}{rows_['Coût prévu']};\"\")"
                        else:
                            cells[line] = "=" + "+".join(f"N({col}{block_rows[p][line]})" for p in POLES)
                else:
                    pole_row = ACT_PROD_FIRST + POLES.index(pole)
                    cost_row = ACT_COST_FIRST + POLES.index(pole)
                    kv_obj = params.objectives.get((pole, year), (0, 0, {}))[1] or 1
                    kv_txt = str(kv_obj).replace(".", ",")
                    obj_row = params.objective_rows.get((pole, year))
                    monthly = state.pl.get(pole, {})
                    reported = [m for m in range(1, 13) if year == report_year
                                and M.index(M.key(year, m)) <= M.index(params.last_closed)
                                and monthly.get(M.key(year, m)) is None]
                    if j == 0:
                        rep = state.pl_columns.get("report")
                        if year == report_year and rep:
                            cells["Réalisé (compta)"] = f"=N({quote(TAB_ACTUALS)}!{rep}{pole_row})"
                            cells["Coût prévu"] = (f"=IF(N({quote(TAB_ACTUALS)}!{rep}{cost_row})>0;{quote(TAB_ACTUALS)}!{rep}{cost_row};"
                                                   f"N({col}{rows_['Réalisé (compta)']})/{kv_txt})")
                            if obj_row and reported:
                                cells["Objectif"] = "=" + "+".join(f"N({quote(TAB_PARAMS)}!{col_letter(3 + m)}{obj_row})" for m in reported)
                                cells["Écart au mois"] = f"=N({col}{rows_['Total prévu']})-N({col}{rows_['Objectif']})"
                    elif j in reported:
                        pass  # counted in the "Report" column
                    elif M.index(month) <= M.index(params.last_closed):
                        a = act_col.get(month)
                        if a:
                            cells["Réalisé (compta)"] = f"=N({quote(TAB_ACTUALS)}!{a}{pole_row})"
                            cells["Coût prévu"] = (f"=IF(N({quote(TAB_ACTUALS)}!{a}{cost_row})>0;{quote(TAB_ACTUALS)}!{a}{cost_row};"
                                                   f"N({col}{rows_['Réalisé (compta)']})/{kv_txt})")
                    elif month in grid_cols:
                        g = grid_cols[month]
                        tabs = POLE_TAB_LIST[pole]  # Maintenance: contracts tab + TS tab
                        cells["Signé à produire"] = "=" + "+".join(f"N({quote(t)}!{g}{ROW_SIGNED})" for t in tabs)
                        cells["Pipe pondéré"] = "=" + "+".join(f"N({quote(t)}!{g}{ROW_PIPE})" for t in tabs)
                        cells["Coût prévu"] = "=" + "+".join(f"N({quote(t)}!{g}{ROW_COST})" for t in tabs)
                    if j == 0 or j not in reported:
                        cells["Total prévu"] = (f"=N({col}{rows_['Réalisé (compta)']})+N({col}{rows_['Signé à produire']})"
                                                f"+N({col}{rows_['Pipe pondéré']})")
                    if j and j not in reported and obj_row:
                        cells["Objectif"] = f"=N({quote(TAB_PARAMS)}!{col_letter(3 + j)}{obj_row})"
                        cells["Écart au mois"] = f"=N({col}{rows_['Total prévu']})-N({col}{rows_['Objectif']})"
                    if "Total prévu" in cells:
                        cells["KV"] = f"=IFERROR({col}{rows_['Total prévu']}/{col}{rows_['Coût prévu']};\"\")"
                for line, formula in cells.items():
                    formulas.append((f"{col}{rows_[line]}", formula))
            # quarter columns (a quarter with a month folded into "Report" stays empty)
            pole_reported = set()
            for p_ in (POLES if pole == "Total" else [pole]):
                monthly_p = state.pl.get(p_, {})
                pole_reported |= {m for m in range(1, 13) if year == report_year
                                  and M.index(M.key(year, m)) <= M.index(params.last_closed)
                                  and monthly_p.get(M.key(year, m)) is None}
            for q in range(1, 5):
                qcol = col_letter(13 + q)
                months_q = [3 * (q - 1) + i for i in (1, 2, 3)]
                if any(m in pole_reported for m in months_q):
                    continue
                a, b = col_letter(1 + months_q[0]), col_letter(1 + months_q[-1])
                for line in SUMMARY_LINES:
                    rr = rows_[line]
                    if line == "KV":
                        formulas.append((f"{qcol}{rr}", f"=IFERROR({qcol}{rows_['Total prévu']}/{qcol}{rows_['Coût prévu']};\"\")"))
                    else:
                        formulas.append((f"{qcol}{rr}", f"=SUM({a}{rr}:{b}{rr})"))
            # yearly columns
            tot, obj, gap, need = (col_letter(TOT_COL), col_letter(TOT_COL + 1), col_letter(TOT_COL + 2), col_letter(TOT_COL + 3))
            for line in SUMMARY_LINES:
                rr = rows_[line]
                if line == "KV":
                    formulas.append((f"{tot}{rr}", f"=IFERROR({tot}{rows_['Total prévu']}/{tot}{rows_['Coût prévu']};\"\")"))
                else:
                    formulas.append((f"{tot}{rr}", f"=SUM(B{rr}:N{rr})"))
            if pole == "Total":
                formulas.append((f"{obj}{rows_['Total prévu']}", "=" + "+".join(f"N({obj}{block_rows[p]['Total prévu']})" for p in POLES)))
            else:
                obj_row = params.objective_rows.get((pole, year))
                if obj_row:
                    formulas.append((f"{obj}{rows_['Total prévu']}", f"=N({quote(TAB_PARAMS)}!C{obj_row})"))
            formulas.append((f"{gap}{rows_['Total prévu']}", f"={tot}{rows_['Total prévu']}-{obj}{rows_['Total prévu']}"))
            formulas.append((f"{need}{rows_['Total prévu']}", f"=MAX(0;{obj}{rows_['Total prévu']}-{tot}{rows_['Total prévu']})"))
        row = r + 1

    # "En bref": the four answers for the current year, per pole, read from the year block.
    year = M.year_of(output.open_from)
    blocks = year_rows.get(year)
    if blocks:
        done = (f"Réalisé compta (janv. à {M.label(params.last_closed)})" if M.year_of(params.last_closed) == year
                else "Réalisé compta (aucun mois clos)")
        values[BRIEF_FIRST - 2] = [f"En bref · {year}", done, f"Signé, reste à produire en {year}", f"Pipe pondéré {year}",
                                   f"Total prévu fin {year} (atterrissage)", f"Objectif {year}", "Écart à l'objectif",
                                   "% de l'objectif", "Manque pour l'objectif (sans compter le pipe)"] + [""] * (width - 9)
        styled["header"].append(BRIEF_FIRST - 1)
        tot, obj = col_letter(TOT_COL), col_letter(TOT_COL + 1)
        for i, pole in enumerate(list(POLES) + ["Total"]):
            r = BRIEF_FIRST + i
            b = blocks[pole]
            values[r - 1] = [pole] + [""] * (width - 1)
            styled["brief"].append(r)
            styled["brief_rows"].append((r, pole))
            formulas += [
                (f"B{r}", f"={tot}{b['Réalisé (compta)']}"),
                (f"C{r}", f"={tot}{b['Signé à produire']}"),
                (f"D{r}", f"={tot}{b['Pipe pondéré']}"),
                (f"E{r}", f"={tot}{b['Total prévu']}"),
                (f"F{r}", f"={obj}{b['Total prévu']}"),
                (f"G{r}", f"=E{r}-F{r}"),
                (f"H{r}", f'=IFERROR(E{r}/F{r};"")'),
                (f"I{r}", f"=MAX(0;F{r}-B{r}-C{r})"),
            ]
        # Maintenance contracts by status (columns K to O, next to "En bref"), read from the contracts tab.
        cols = {c.field: col_letter(i) for i, c in enumerate(pole_columns(output.grid))}
        ct = quote(TAB_CONTRACTS)
        rng = lambda field: f"{ct}!{cols[field]}{ROW_FIRST}:{cols[field]}2000"
        next_q = [fid for fid, _, _ in quarters(output.grid) if fid.startswith(f"q:{year + 1}-")]
        values[BRIEF_FIRST - 2][STATUS_COL:STATUS_COL + 5] = [
            "Contrats d'entretien", "Nombre", "Valeur annuelle", f"Reste à produire {year}", f"Production {year + 1}"]
        for i, status in enumerate(list(HEALTH) + ["Total"]):
            r = BRIEF_FIRST + i
            k = col_letter(STATUS_COL)
            values[r - 1][STATUS_COL] = status
            styled["status_rows"].append((r, status))
            if status == "Total":  # the three statuses added up (devis and sites without amount have none)
                formulas += [(f"{col_letter(STATUS_COL + j)}{r}", f"=SUM({col_letter(STATUS_COL + j)}{BRIEF_FIRST}:"
                                                                   f"{col_letter(STATUS_COL + j)}{r - 1})") for j in range(1, 5)]
                continue
            cond = lambda f: f'SUMIFS({rng(f)};{rng("health")};"{status}")'
            formulas += [
                (f"{col_letter(STATUS_COL + 1)}{r}", f'=COUNTIF({rng("health")};"{status}")'),
                (f"{col_letter(STATUS_COL + 2)}{r}", f"={cond('ca')}"),
                (f"{col_letter(STATUS_COL + 3)}{r}", f"={cond('this_year')}"),
                (f"{col_letter(STATUS_COL + 4)}{r}", "=" + ("+".join(cond(q) for q in next_q) if next_q else "0")),
            ]
        # Chart data (hidden columns from AE): one block of 5 columns per chart (Total, then each pole), every month
        # from the first one after the "Report" block to the end of the window, read from the year blocks. The
        # months folded into "Report" come first as one bar: their monthly average, in the réalisé colour.
        def folded_months(poles) -> List[int]:
            return [m for m in range(1, 13) if M.index(M.key(report_year, m)) <= M.index(params.last_closed)
                    and all(state.pl.get(p, {}).get(M.key(report_year, m)) is None for p in poles)]

        reported_total = folded_months(POLES)
        first_month = M.key(report_year, max(reported_total)) if reported_total else M.add(M.key(report_year, 1), -1)
        blocks_out = []
        for name in ["Total"] + list(POLES):
            folded = folded_months(POLES if name == "Total" else [name])
            rows_out = [["Mois", "Réalisé", "Signé", "Pipe pondéré", "Objectif"]]
            if folded and report_year in year_rows:
                tb, n = year_rows[report_year][name], len(folded)
                short = lambda m: M.label(M.key(report_year, m)).split(" ")[0]
                rows_out.append([f"Moy. {short(folded[0])} à {short(folded[-1])}", f"=N(B{tb['Réalisé (compta)']})/{n}", 0, 0,
                                 f"=N(B{tb['Objectif']})/{n}"])
            for m in M.span(M.add(first_month, 1), output.grid[-1]):
                y, mm = M.parse(m)
                if y not in year_rows:
                    continue
                tb, c = year_rows[y][name], col_letter(1 + mm)
                rows_out.append([M.label(m), f"=N({c}{tb['Réalisé (compta)']})", f"=N({c}{tb['Signé à produire']})",
                                 f"=N({c}{tb['Pipe pondéré']})", f"=N({c}{tb['Objectif']})"])
            blocks_out.append(rows_out)
        chart_rows = blocks_out[0]
        styled["chart_rows"] = [max(len(b) for b in blocks_out)]
        for b, block in enumerate(blocks_out):
            for k, line in enumerate(block):
                for j, value in enumerate(line):
                    col0 = CHART_COL + CHART_BLOCK * b + j
                    cell = f"{col_letter(col0)}{CHART_ROW + k}"
                    if str(value).startswith("="):
                        formulas.append((cell, value))
                    else:
                        while len(values) < CHART_ROW + k:
                            values.append([""] * width)
                        line_values = values[CHART_ROW + k - 1]
                        while len(line_values) <= col0:
                            line_values.append("")
                        line_values[col0] = value
    return values, formulas, styled


# ---------------------------------------------------------------------- formats

def _repeat(sheet_id: int, r0: int, r1: int, c0: int, c1: int, fmt: dict, fields: str) -> dict:
    return {"repeatCell": {"range": {"sheetId": sheet_id, "startRowIndex": r0, "endRowIndex": r1,
                                     "startColumnIndex": c0, "endColumnIndex": c1},
                           "cell": {"userEnteredFormat": fmt}, "fields": fields}}


def pole_format_requests(sheet_id: int, rend: Rendered, pole: str) -> List[dict]:
    cols = rend.columns
    n_rows = len(rend.values)
    light, strong = rgb(POLE_COLORS[pole]["light"]), rgb(POLE_COLORS[pole]["strong"])
    thin = {"style": "SOLID", "color": rgb("#d9d9d9")}
    medium = {"style": "SOLID_MEDIUM", "color": rgb("#7f7f7f")}
    req: List[dict] = [
        {"updateSheetProperties": {"properties": {"sheetId": sheet_id, "gridProperties": {
            "frozenRowCount": ROW_HEADER, "frozenColumnCount": 4}}, "fields": "gridProperties(frozenRowCount,frozenColumnCount)"}},
        _repeat(sheet_id, 0, n_rows + 200, 0, len(cols), {"backgroundColor": WHITE, "textFormat": {"bold": False,
                "foregroundColor": {"red": 0, "green": 0, "blue": 0}}, "wrapStrategy": "CLIP"},
                "userEnteredFormat(backgroundColor,textFormat,wrapStrategy)"),
        _repeat(sheet_id, ROW_TITLE - 1, ROW_TITLE, 0, len(cols), {"backgroundColor": strong, "textFormat": {"bold": True, "fontSize": 13}},
                "userEnteredFormat(backgroundColor,textFormat)"),
        _repeat(sheet_id, ROW_TITLE - 1, ROW_LEGEND, 0, len(cols), {"wrapStrategy": "OVERFLOW_CELL"}, "userEnteredFormat.wrapStrategy"),
        {"repeatCell": {"range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": n_rows + 200, "startColumnIndex": 0,
                                  "endColumnIndex": len(cols)}, "cell": {"note": ""}, "fields": "note"}},
        _repeat(sheet_id, ROW_SIGNED - 1, ROW_COST, 0, len(cols), {
            "backgroundColor": WHITE, "textFormat": {"bold": False, "fontSize": 10, "foregroundColor": DARK_TEXT},
            "verticalAlignment": "MIDDLE",
            "borders": {"bottom": thin, "top": thin}}, "userEnteredFormat(backgroundColor,textFormat,verticalAlignment,borders)"),
        _repeat(sheet_id, ROW_SIGNED - 1, ROW_COST, 0, 4, {
            "backgroundColor": light, "textFormat": {"bold": True, "fontSize": 10, "foregroundColor": DARK_TEXT},
            "horizontalAlignment": "LEFT"}, "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)"),
        _repeat(sheet_id, ROW_TOTAL - 1, ROW_TOTAL, 0, len(cols), {
            "backgroundColor": strong, "textFormat": {"bold": True, "fontSize": 11, "foregroundColor": DARK_TEXT},
            "borders": {"top": medium, "bottom": medium}}, "userEnteredFormat(backgroundColor,textFormat,borders)"),
        _repeat(sheet_id, ROW_COST - 1, ROW_COST, 0, len(cols), {
            "textFormat": {"italic": True, "foregroundColor": GREY_TEXT}}, "userEnteredFormat.textFormat(italic,foregroundColor)"),
        _repeat(sheet_id, ROW_HEADER - 1, ROW_HEADER, 0, len(cols), {"backgroundColor": strong, "wrapStrategy": "WRAP",
                "textFormat": {"bold": True, "foregroundColor": {"red": 0, "green": 0, "blue": 0}}, "verticalAlignment": "MIDDLE"},
                "userEnteredFormat(backgroundColor,textFormat,wrapStrategy,verticalAlignment)"),
        {"updateSheetProperties": {"properties": {"sheetId": sheet_id, "tabColorStyle": {"rgbColor": strong}},
                                   "fields": "tabColorStyle"}},
        {"updateDimensionProperties": {"range": {"sheetId": sheet_id, "dimension": "ROWS", "startIndex": ROW_FIELDS - 1,
                                                 "endIndex": ROW_FIELDS}, "properties": {"hiddenByUser": True}, "fields": "hiddenByUser"}},
    ]
    for i, c in enumerate(cols):
        if c.kind in FORMATS:
            req.append(_repeat(sheet_id, ROW_SIGNED - 1, n_rows + 200, i, i + 1, {"numberFormat": FORMATS[c.kind]},
                               "userEnteredFormat.numberFormat"))
        req.append({"updateDimensionProperties": {"range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": i,
                                                            "endIndex": i + 1},
                                                  "properties": {"pixelSize": c.width, "hiddenByUser": pole not in c.only},
                                                  "fields": "pixelSize,hiddenByUser"}})
    if rend.backgrounds:
        req.append({"updateCells": {
            "range": {"sheetId": sheet_id, "startRowIndex": ROW_FIRST - 1, "endRowIndex": ROW_FIRST - 1 + len(rend.backgrounds),
                      "startColumnIndex": 0, "endColumnIndex": len(cols)},
            "rows": [{"values": [{"userEnteredFormat": {"backgroundColor": bg}} for bg in line]} for line in rend.backgrounds],
            "fields": "userEnteredFormat.backgroundColor"}})
    for r0, c0 in rend.red_cells:
        req.append(_repeat(sheet_id, r0, r0 + 1, c0, c0 + 1, {"textFormat": {"foregroundColor": RED_TEXT}},
                           "userEnteredFormat.textFormat.foregroundColor"))
    for r0, c0, colour in rend.type_colors:
        req.append(_repeat(sheet_id, r0, r0 + 1, c0, c0 + 1, {"textFormat": {"foregroundColor": colour, "bold": True}},
                           "userEnteredFormat.textFormat(foregroundColor,bold)"))
    # Legend on row 2: the real colours of the cells
    fields = [c.field for c in cols]
    label_i, owner_i = fields.index("label"), fields.index("owner")
    border = {"style": "SOLID", "color": {"red": 0.6, "green": 0.6, "blue": 0.6}}
    req += [
        {"unmergeCells": {"range": {"sheetId": sheet_id, "startRowIndex": ROW_SIGNED - 1, "endRowIndex": ROW_COST,
                                    "startColumnIndex": 0, "endColumnIndex": len(cols)}}},
    ] + [
        {"mergeCells": {"range": {"sheetId": sheet_id, "startRowIndex": r - 1, "endRowIndex": r, "startColumnIndex": 0,
                                  "endColumnIndex": 4}, "mergeType": "MERGE_ALL"}} for r in range(ROW_SIGNED, ROW_COST + 1)
    ] + [
        {"updateDimensionProperties": {"range": {"sheetId": sheet_id, "dimension": "ROWS", "startIndex": ROW_SIGNED - 1,
                                                 "endIndex": ROW_COST}, "properties": {"pixelSize": 24}, "fields": "pixelSize"}},
        {"unmergeCells": {"range": {"sheetId": sheet_id, "startRowIndex": ROW_LEGEND - 1, "endRowIndex": ROW_LEGEND,
                                    "startColumnIndex": 0, "endColumnIndex": len(cols)}}},
        {"mergeCells": {"range": {"sheetId": sheet_id, "startRowIndex": ROW_LEGEND - 1, "endRowIndex": ROW_LEGEND,
                                  "startColumnIndex": 0, "endColumnIndex": label_i}, "mergeType": "MERGE_ALL"}},
        _repeat(sheet_id, ROW_LEGEND - 1, ROW_LEGEND, 0, label_i, {"backgroundColor": BLUE_INPUT, "textFormat": {"bold": True},
                "verticalAlignment": "MIDDLE", "horizontalAlignment": "CENTER"},
                "userEnteredFormat(backgroundColor,textFormat,verticalAlignment,horizontalAlignment)"),
        _repeat(sheet_id, ROW_LEGEND - 1, ROW_LEGEND, label_i, label_i + 1, {"backgroundColor": TYPED_BG, "wrapStrategy": "WRAP",
                "textFormat": {"bold": True}, "verticalAlignment": "MIDDLE"},
                "userEnteredFormat(backgroundColor,wrapStrategy,textFormat,verticalAlignment)"),
        _repeat(sheet_id, ROW_LEGEND - 1, ROW_LEGEND, owner_i, owner_i + 1, {"backgroundColor": WHITE, "wrapStrategy": "WRAP",
                "textFormat": {"bold": True}, "verticalAlignment": "MIDDLE",
                "borders": {"top": border, "bottom": border, "left": border, "right": border}},
                "userEnteredFormat(backgroundColor,wrapStrategy,textFormat,verticalAlignment,borders)"),
        {"updateDimensionProperties": {"range": {"sheetId": sheet_id, "dimension": "ROWS", "startIndex": ROW_LEGEND - 1,
                                                 "endIndex": ROW_LEGEND}, "properties": {"pixelSize": 38}, "fields": "pixelSize"}},
    ]
    if "health" in [c.field for c in cols] and pole == TAB_CONTRACTS:
        h_i = [c.field for c in cols].index("health")
        req.append({"setDataValidation": {"range": {"sheetId": sheet_id, "startRowIndex": ROW_FIRST - 1, "endRowIndex": n_rows + 200,
                                                    "startColumnIndex": h_i, "endColumnIndex": h_i + 1},
                                          "rule": {"condition": {"type": "ONE_OF_LIST", "values": [{"userEnteredValue": v} for v in HEALTH]},
                                                   "strict": False, "showCustomUi": True}}})
    cat_i = [c.field for c in cols].index("cat")
    req.append({"setDataValidation": {"range": {"sheetId": sheet_id, "startRowIndex": ROW_FIRST - 1, "endRowIndex": n_rows + 200,
                                                "startColumnIndex": cat_i, "endColumnIndex": cat_i + 1},
                                      "rule": {"condition": {"type": "ONE_OF_LIST", "values": [{"userEnteredValue": "Signé"},
                                                                                               {"userEnteredValue": "Pipe"}]},
                                               "strict": False, "showCustomUi": True}}})
    return req


def summary_format_requests(sheet_id: int, output: Output, layout_rows: Dict[str, list]) -> List[dict]:
    req = [
        _repeat(sheet_id, 0, 300, 0, 22, {"backgroundColor": WHITE, "textFormat": {"bold": False}, "wrapStrategy": "CLIP"},
                "userEnteredFormat(backgroundColor,textFormat,wrapStrategy)"),
        {"repeatCell": {"range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 300, "startColumnIndex": 0,
                                  "endColumnIndex": 22}, "cell": {"note": ""}, "fields": "note"}},
        {"updateSheetProperties": {"properties": {"sheetId": sheet_id, "tabColorStyle": {"rgbColor": rgb(POLE_COLORS["Total"]["strong"])}},
                                   "fields": "tabColorStyle"}},
        _repeat(sheet_id, 0, 2, 0, 22, {"wrapStrategy": "OVERFLOW_CELL"}, "userEnteredFormat.wrapStrategy"),
        _repeat(sheet_id, 0, 1, 0, 22, {"textFormat": {"bold": True, "fontSize": 14}}, "userEnteredFormat.textFormat"),
        _repeat(sheet_id, 3, 300, 1, 22, {"numberFormat": FORMATS["money"]}, "userEnteredFormat.numberFormat"),
    ]
    for r in layout_rows.get("header", []):
        req.append(_repeat(sheet_id, r - 1, r, 0, 22, {"backgroundColor": HEADER_BG, "wrapStrategy": "WRAP", "verticalAlignment": "MIDDLE",
                                                         "textFormat": {"bold": True, "foregroundColor": HEADER_FG}},
                           "userEnteredFormat(backgroundColor,textFormat,wrapStrategy,verticalAlignment)"))
    req.append({"updateDimensionProperties": {"range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 1, "endIndex": 22},
                                              "properties": {"pixelSize": 104}, "fields": "pixelSize"}})
    for r, pole, line in layout_rows.get("lines", []):
        strong = line == "Total prévu"
        colour = rgb(POLE_COLORS[pole]["strong" if strong else "light"])
        req.append(_repeat(sheet_id, r - 1, r, 0, 22, {"backgroundColor": colour, "textFormat": {"bold": strong}},
                           "userEnteredFormat(backgroundColor,textFormat)"))
    for r, pole in layout_rows.get("brief_rows", []):
        req.append(_repeat(sheet_id, r - 1, r, 0, 9, {"backgroundColor": rgb(POLE_COLORS[pole]["light"])},
                           "userEnteredFormat.backgroundColor"))
        req.append(_repeat(sheet_id, r - 1, r, 4, 5, {"backgroundColor": rgb(POLE_COLORS[pole]["strong"])},
                           "userEnteredFormat.backgroundColor"))
    for r, status in layout_rows.get("status_rows", []):
        total = status == "Total"
        req.append(_repeat(sheet_id, r - 1, r, STATUS_COL, STATUS_COL + 5,
                           {"backgroundColor": rgb(POLE_COLORS["Maintenance"]["strong" if total else "light"]),
                            "textFormat": {"bold": total}}, "userEnteredFormat(backgroundColor,textFormat)"))
        req.append(_repeat(sheet_id, r - 1, r, STATUS_COL + 1, STATUS_COL + 2, {"numberFormat": FORMATS["int"]},
                           "userEnteredFormat.numberFormat"))
        if status in HEALTH_TEXT:
            req.append(_repeat(sheet_id, r - 1, r, STATUS_COL, STATUS_COL + 1,
                               {"textFormat": {"bold": True, "foregroundColor": rgb(HEALTH_TEXT[status])}},
                               "userEnteredFormat.textFormat"))
    for r, c, text in layout_rows.get("notes", []):
        req.append({"updateCells": {"range": {"sheetId": sheet_id, "startRowIndex": r - 1, "endRowIndex": r, "startColumnIndex": c,
                                              "endColumnIndex": c + 1}, "rows": [{"values": [{"note": text}]}], "fields": "note"}})
    for r in layout_rows.get("kv", []):
        req.append(_repeat(sheet_id, r - 1, r, 1, 22, {"numberFormat": FORMATS["kv"]}, "userEnteredFormat.numberFormat"))
    for r in layout_rows.get("brief", []):
        req.append(_repeat(sheet_id, r - 1, r, 7, 8, {"numberFormat": FORMATS["pct"]}, "userEnteredFormat.numberFormat"))
        req.append(_repeat(sheet_id, r - 1, r, 0, 9, {"textFormat": {"bold": True, "fontSize": 11}}, "userEnteredFormat.textFormat"))
    req.append({"updateDimensionProperties": {"range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": CHART_COL,
                                                        "endIndex": CHART_COL + CHART_BLOCK * (1 + len(POLES))},
                                              "properties": {"hiddenByUser": True}, "fields": "hiddenByUser"}})
    return req + [
        {"updateDimensionProperties": {"range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 0, "endIndex": 1},
                                       "properties": {"pixelSize": 260}, "fields": "pixelSize"}},
        {"updateSheetProperties": {"properties": {"sheetId": sheet_id, "gridProperties": {"frozenColumnCount": 1}},
                                   "fields": "gridProperties.frozenColumnCount"}},
    ]


def static_format_requests(ids: Dict[str, int], first_year: int, last_year: int) -> List[dict]:
    p, a, h = ids[TAB_PARAMS], ids[TAB_ACTUALS], ids[TAB_HOME]
    req = [
        _repeat(p, 0, 1, 0, 4, {"textFormat": {"bold": True, "fontSize": 14}}, "userEnteredFormat.textFormat"),
        _repeat(p, 3, 7, 1, 2, {"backgroundColor": BLUE_INPUT}, "userEnteredFormat.backgroundColor"),
        _repeat(p, 5, 6, 1, 2, {"numberFormat": {"type": "DATE", "pattern": "dd/mm/yyyy"}}, "userEnteredFormat.numberFormat"),
        _repeat(p, 6, 7, 1, 2, {"numberFormat": FORMATS["pct"]}, "userEnteredFormat.numberFormat"),
        _repeat(p, P_KV_FIRST_ROW - 1, P_KV_FIRST_ROW - 1 + P_KV_ROWS, 1, 2, {"backgroundColor": BLUE_INPUT, "numberFormat": FORMATS["kv"]},
                "userEnteredFormat(backgroundColor,numberFormat)"),
        _repeat(p, P_OBJ_HEADER_ROW, P_OBJ_HEADER_ROW + 3 * (last_year - first_year + 1), 2, 16,
                {"backgroundColor": BLUE_INPUT, "numberFormat": FORMATS["money"]}, "userEnteredFormat(backgroundColor,numberFormat)"),
        _repeat(p, P_OBJ_HEADER_ROW, P_OBJ_HEADER_ROW + 3 * (last_year - first_year + 1), 3, 4,
                {"numberFormat": FORMATS["kv"]}, "userEnteredFormat.numberFormat"),
        _repeat(p, P_OBJ_HEADER_ROW - 1, P_OBJ_HEADER_ROW, 0, 16, {"backgroundColor": HEADER_BG, "textFormat": {"bold": True,
                "foregroundColor": HEADER_FG}}, "userEnteredFormat(backgroundColor,textFormat)"),
        {"updateDimensionProperties": {"range": {"sheetId": p, "dimension": "COLUMNS", "startIndex": 0, "endIndex": 1},
                                       "properties": {"pixelSize": 380}, "fields": "pixelSize"}},
        _repeat(a, 0, 1, 0, 4, {"textFormat": {"bold": True, "fontSize": 14}}, "userEnteredFormat.textFormat"),
        {"updateDimensionProperties": {"range": {"sheetId": a, "dimension": "ROWS", "startIndex": ACT_FIELDS_ROW - 1,
                                                 "endIndex": ACT_FIELDS_ROW}, "properties": {"hiddenByUser": True}, "fields": "hiddenByUser"}},
        _repeat(a, ACT_PROD_FIRST - 1, ACT_PROD_FIRST + 2, 1, 40, {"backgroundColor": BLUE_INPUT, "numberFormat": FORMATS["money"]},
                "userEnteredFormat(backgroundColor,numberFormat)"),
        _repeat(a, ACT_COST_FIRST - 1, ACT_COST_FIRST + 2, 1, 40, {"backgroundColor": BLUE_INPUT, "numberFormat": FORMATS["money"]},
                "userEnteredFormat(backgroundColor,numberFormat)"),
        _repeat(a, ACT_PROJ_FIRST - 1, 2000, 3, 4, {"backgroundColor": BLUE_INPUT, "numberFormat": FORMATS["money"]},
                "userEnteredFormat(backgroundColor,numberFormat)"),
        _repeat(a, ACT_PROJ_FIRST - 1, 2000, 4, 6, {"backgroundColor": BLUE_INPUT}, "userEnteredFormat.backgroundColor"),
        _repeat(a, ACT_PROJ_FIRST - 1, 2000, 6, 7, {"numberFormat": FORMATS["money"]}, "userEnteredFormat.numberFormat"),
        _repeat(a, ACT_PROJ_HEADER - 1, ACT_PROJ_HEADER, 0, 8, {"backgroundColor": HEADER_BG, "textFormat": {"bold": True,
                "foregroundColor": HEADER_FG}}, "userEnteredFormat(backgroundColor,textFormat)"),
        _repeat(h, 0, 1, 0, 2, {"textFormat": {"bold": True, "fontSize": 16}}, "userEnteredFormat.textFormat"),
        {"updateDimensionProperties": {"range": {"sheetId": h, "dimension": "COLUMNS", "startIndex": 0, "endIndex": 1},
                                       "properties": {"pixelSize": 220}, "fields": "pixelSize"}},
        {"updateDimensionProperties": {"range": {"sheetId": h, "dimension": "COLUMNS", "startIndex": 1, "endIndex": 2},
                                       "properties": {"pixelSize": 900}, "fields": "pixelSize"}},
        _repeat(h, 0, 40, 1, 2, {"wrapStrategy": "WRAP"}, "userEnteredFormat.wrapStrategy"),
    ]
    for title in (TAB_INPUTS, TAB_RENDER, TAB_CALC):
        req.append({"updateSheetProperties": {"properties": {"sheetId": ids[title], "hidden": True}, "fields": "hidden"}})
    return req
