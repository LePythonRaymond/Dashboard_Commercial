"""
Assembly of the forecast: sources + finance figures + typed values -> rows -> displayed values.

Nothing here talks to Google: ``sheet.py`` reads the spreadsheet into a ``SheetState`` and writes
the ``Output`` back. That keeps every rule testable offline.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

import pandas as pd

from . import months as M
from .engine import EPS, myrium_allocation
from .maintenance import Site, monthly_values, passages_in_year, year_value
from .model import EDITABLE_FIELDS, Row, Shown, compute_row
from .sources import Legacy, norm

KIND_ORDER = {"Projet": 0, "Contrat": 1, "TS": 2, "Animation": 3, "Devis": 4, "Manuel": 5}
CATEGORY_ORDER = {"Signé": 0, "Pipe": 1}


@dataclass
class FinanceProject:
    """One line of the "production par projet" table in the Réalisé tab (finance owns it)."""

    pole: str
    number: str
    label: str
    produced: float
    closed_month: str
    state: str = "ouvert"  # "ouvert" or "fermé"
    ca: float = 0.0  # CA known by finance, used when Furious has no such project
    comment: str = ""


@dataclass
class Params:
    last_closed: str  # last month closed by finance (P&L and projects)
    fallback_months: int = 2
    new_projects_since: Optional[date] = None
    kv: Dict[str, float] = field(default_factory=dict)
    objectives: Dict[Tuple[str, int], Tuple[float, float, Dict[str, float]]] = field(default_factory=dict)
    objective_rows: Dict[Tuple[str, int], int] = field(default_factory=dict)  # (pole, year) -> row in Paramètres


@dataclass
class SheetState:
    params: Params
    finance: List[FinanceProject]
    pl: Dict[str, Dict[str, float]]  # pole -> month key or "report" -> production (P&L)
    pl_costs: Dict[str, Dict[str, float]]
    inputs: Dict[str, Dict[str, object]]  # Réf -> typed values (field id -> value)
    rendered: Dict[str, Dict[str, object]]  # Réf -> editable cells as last written
    visible: Dict[str, List[Dict[str, object]]]  # pole -> rows as read (field id -> value)
    pl_columns: Dict[str, str] = field(default_factory=dict)  # "report" or month key -> column of the Réalisé tab


@dataclass
class Output:
    grid: List[str]
    open_from: str
    rows: Dict[str, List[Tuple[Row, Shown]]]  # pole -> rows in display order
    inputs: Dict[str, Dict[str, object]]
    finance: List[FinanceProject]
    checks: List[Tuple[str, str, str, str]]  # (pole, Réf, label, message)
    notes: List[str]
    pending_from: str = ""  # first month a devis not signed yet can produce


# --------------------------------------------------------------------------- helpers

def window(last_closed: str, today: date) -> Tuple[List[str], str]:
    """Grid months and first open month. The window runs 18 months from the current month."""
    current = M.key(today.year, today.month)
    open_from = max(current, M.add(last_closed, 1), key=M.index)
    end = M.add(current, 17)
    return M.span(M.add(last_closed, 1), end), open_from


def kv_default(pole: str, typologie: str, ca: float, kv: Dict[str, float]) -> float:
    words = set(norm(typologie).split())
    if pole == "Conception":
        return kv.get("Conception", 2.2)
    if pole == "Maintenance":
        return kv.get("Maintenance TS", 1.7) if "ts" in words else kv.get("Maintenance", 2.3)
    if "ts" in words:
        return kv.get("Travaux TS", 1.7)
    if "dv" in words:
        return kv.get("Travaux DV +100k", 1.5) if ca >= 100000 else kv.get("Travaux DV", 1.7)
    if "conception" in words:
        return kv.get("Travaux Conception", 1.5)
    return kv.get("Travaux Direct", 1.4)


def _month(value) -> Optional[str]:
    try:
        return M.of(value) if value not in (None, "") else None
    except (ValueError, TypeError):
        return None


def _ascii_words(text: str) -> List[str]:
    plain = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return re.findall(r"[0-9A-Za-z]+", plain)


def site_number(site: Site) -> str:
    """Furious number of a site: the one in its title (« Cogéva - 1116 (EXT) »), else the "ID furious" column.

    The title wins because it is the one people read and correct: on 2026-10-26 Cogéva's "ID furious" said 1119
    (Green Corner's number) while its title and Furious said 1116.
    """
    m = re.search(r"-\s*(\d+(?:\s*\+\s*\d+)*)\s*\(", site.name or "")
    return "-".join(_ascii_words(m.group(1) if m else site.furious_id))


def site_refs(sites: List[Site]) -> Dict[str, str]:
    """Short, stable, unique references for maintenance contracts.

    1. "S" + the site's Furious number (``site_number``).
    2. When several sites share it, + the label in brackets at the end of the title: EXT, INT, Maraichage...
       (or the INT/EXT column when the title has none).
    3. Still shared (three buildings on one contract), + the first word of the name.
    4. No number, or still shared: "S" + the end of the Notion page id.
    """
    def label(s: Site) -> str:
        m = re.search(r"\(([^()]*)\)\s*$", s.name or "")
        return "-".join(_ascii_words(m.group(1) if m else s.int_ext))

    def unique(candidates: Dict[str, str]) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for ref in candidates.values():
            if ref:
                counts[ref] = counts.get(ref, 0) + 1
        return counts

    wanted = {s.page_id: ("S" + site_number(s)) if site_number(s) else "" for s in sites}
    for extra in (label, lambda s: "-".join(_ascii_words(s.name)[:1]).upper()):
        counts = unique(wanted)
        for s in sites:
            ref = wanted[s.page_id]
            if ref and counts[ref] > 1 and extra(s):
                wanted[s.page_id] = f"{ref}-{extra(s)}"
    counts = unique(wanted)
    return {s.page_id: wanted[s.page_id] if wanted[s.page_id] and counts[wanted[s.page_id]] == 1
            else "S" + s.page_id.replace("-", "")[-6:] for s in sites}


def _pole_of_bu(bu: str) -> Optional[str]:
    return {"TRAVAUX": "Travaux", "CONCEPTION": "Conception", "MAINTENANCE": "Maintenance"}.get(str(bu or "").upper())


def _owner(value) -> str:
    text = str(value or "").strip()
    return "" if text.lower() in ("nan", "none") else text


# --------------------------------------------------------------------------- start-up from the legacy workbook

def seed_from_legacy(legacy: Legacy, pipe: pd.DataFrame, last_closed: str, projects: Optional[pd.DataFrame] = None) -> Tuple[
    List[FinanceProject], Dict[str, Dict[str, object]], List[str]
]:
    """Finance table and typed values that reproduce Arthur's workbook on day one."""
    finance: List[FinanceProject] = []
    inputs: Dict[str, Dict[str, object]] = {}
    notes: List[str] = []
    for r in legacy.rows:
        if not r.number:
            continue
        finance.append(
            FinanceProject(r.pole, r.number, r.label, r.produced, last_closed, r.state, r.ca, "reprise du fichier d'Arthur")
        )
        typed: Dict[str, object] = dict(r.pins)
        if r.kv:
            typed["kv"] = r.kv
        if r.part_mai:
            typed["mai"] = r.part_mai
        if r.comment:
            typed["comment"] = r.comment
        if typed:
            inputs[f"P{r.number}"] = typed

    # Conception opportunities typed by name in Arthur's workbook. A name that became a Furious project
    # recently gets its months on that project; otherwise on the pending devis carrying all its words
    # (counted at 100 % as in the workbook); otherwise it stays a manual line.
    stop_words = {"de", "la", "le", "du", "des", "et", "etude", "projet", "p"}
    recent = pd.Timestamp(datetime.now()) - pd.DateOffset(days=150)
    con_projects = {}
    if projects is not None and not projects.empty:
        for _, p in projects[projects["cf_bu"] == "CONCEPTION"].iterrows():
            created = pd.to_datetime(p.get("created_at"), errors="coerce")
            if pd.notna(created) and created >= recent:
                con_projects[str(p["id"])] = (set(norm(p["title"]).split()), p)
    con = pipe[pipe["final_bu"] == "CONCEPTION"] if not pipe.empty else pipe
    titles = {str(d["id"]): set(norm(d["title"]).split()) for _, d in con.iterrows()}
    listed = {(f.pole, f.number) for f in finance}
    manual_n = 0
    for r in legacy.rows:
        if r.number or not r.pins or sum(r.pins.values()) == 0:
            continue
        words = set(norm(r.label).split()) - stop_words
        typed = dict(r.pins)
        project_hits = [i for i, (t, _) in con_projects.items() if words and words <= t]
        devis_hits = [i for i, t in titles.items() if words and words <= t]
        if len(project_hits) == 1:
            pid = project_hits[0]
            typed["comment"] = "Mois repris du fichier d'Arthur (opportunité devenue projet)"
            inputs.setdefault(f"P{pid}", {}).update(typed)
            if ("Conception", pid) not in listed:
                p = con_projects[pid][1]
                finance.append(FinanceProject("Conception", pid, str(p["title"]), 0.0, last_closed, "ouvert",
                                              float(p["total_amount"] or 0), "projet récent repris du fichier d'Arthur"))
                listed.add(("Conception", pid))
            notes.append(f"Conception « {r.label} » rattaché au projet Furious {pid}")
        elif len(devis_hits) == 1:
            typed.update({"proba": 1.0, "comment": "Mois et proba 100 % repris du fichier d'Arthur"})
            inputs[f"D{devis_hits[0]}"] = typed
            notes.append(f"Conception « {r.label} » rattaché au devis Furious {devis_hits[0]}")
        else:
            manual_n += 1
            typed.update({"_pole": "Conception", "cat": "Pipe", "proba": 1.0, "label": f"{r.label} (reprise)",
                          "comment": "Mois et proba 100 % repris du fichier d'Arthur"})
            inputs[f"M{manual_n}"] = typed
            notes.append(f"Conception « {r.label} » gardé en ligne manuelle ({len(devis_hits)} devis possibles)")
    for adj in legacy.adjustments:
        manual_n += 1
        typed = dict(adj["pins"])
        typed.update({"_pole": adj["pole"], "cat": "Signé", "label": adj["label"]})
        if adj.get("comment"):
            typed["comment"] = adj["comment"]
        inputs[f"M{manual_n}"] = typed
    return finance, inputs, notes


# --------------------------------------------------------------------------- typed values found in the sheet

def _same(a, b) -> bool:
    empty = (None, "")
    if a in empty and b in empty:
        return True
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return abs(float(a) - float(b)) <= max(0.005, 1e-9 * abs(float(b)))
    return str(a).strip() == str(b).strip()


def detect_typed(
    state: SheetState, manual_fields: Dict[str, List[str]], normalize
) -> Tuple[Dict[str, Dict[str, object]], List[str]]:
    """Find cells changed by hand since the last write that the Apps Script did not record.

    - A cell that differs from what was last written is a typed value.
    - A cell emptied by hand goes back to automatic.
    - A row whose label no longer matches its Réf (a partial sort) is skipped and reported.
    - A new line typed under the table becomes a manual line (Réf M1, M2...).
    ``normalize(field, value)`` turns a cell into the stored form (months for dates, floats).
    """
    inputs = {ref: dict(v) for ref, v in state.inputs.items()}
    notes: List[str] = []
    next_manual = 1 + max([int(r[1:]) for r in inputs if re.fullmatch(r"M\d+", r)] or [0])
    seen_refs = set()
    for pole, rows in state.visible.items():
        for cells in rows:
            ref = str(cells.get("ref") or "").strip()
            if not ref:
                typed = {}
                for f in manual_fields.get(pole, []):
                    value = normalize(f, cells.get(f))
                    if value not in (None, ""):
                        typed[f] = value
                if typed.get("label") or any(M.is_key(k) for k in typed):
                    typed["_pole"] = pole
                    if typed.get("cat") not in ("Signé", "Pipe"):
                        typed["cat"] = "Signé"
                    inputs[f"M{next_manual}"] = typed
                    notes.append(f"Nouvelle ligne manuelle M{next_manual} dans {pole} : {typed.get('label', '')}")
                    next_manual += 1
                continue
            seen_refs.add(ref)
            before = state.rendered.get(ref)
            if before is None:
                continue
            if not ref.startswith("M") and not _same(cells.get("label"), before.get("label")):
                notes.append(f"{pole} {ref} : libellé différent de la ligne attendue, saisies ignorées (tri partiel ?)")
                continue
            for f, was in before.items():
                if f == "label" and not ref.startswith("M"):
                    continue
                now = cells.get(f)
                if _same(now, was):
                    continue
                value = normalize(f, now)
                if value in (None, ""):
                    inputs.get(ref, {}).pop(f, None)
                else:
                    inputs.setdefault(ref, {})[f] = value
    for ref in list(inputs):
        if ref.startswith("M") and ref in state.rendered and ref not in seen_refs:
            notes.append(f"Ligne manuelle {ref} supprimée dans la feuille")
            inputs.pop(ref)
    return {k: v for k, v in inputs.items() if v}, notes


# --------------------------------------------------------------------------- rows

def assemble(
    state: SheetState,
    projects: pd.DataFrame,
    pipe: pd.DataFrame,
    sites: List[Site],
    today: date,
) -> Output:
    params = state.params
    grid, open_from = window(params.last_closed, today)
    years = sorted({M.year_of(m) for m in grid})
    rows: Dict[str, List[Row]] = {p: [] for p in ("Travaux", "Conception", "Maintenance")}
    checks: List[Tuple[str, str, str, str]] = []
    notes: List[str] = []
    kv = params.kv

    by_id = {str(r["id"]): r for _, r in projects.iterrows()} if not projects.empty else {}
    finance = list(state.finance)
    listed = {(f.pole, f.number) for f in finance}

    # New Furious projects since the date set in Paramètres join the finance table at 0.
    if params.new_projects_since is not None and not projects.empty:
        for _, p in projects.iterrows():
            pole = _pole_of_bu(p.get("cf_bu"))
            if pole not in ("Travaux", "Conception"):
                continue
            created = pd.to_datetime(p.get("created_at"), errors="coerce")
            if pd.isna(created) or created.date() < params.new_projects_since:
                continue
            if (pole, str(p["id"])) in listed:
                continue
            finance.append(FinanceProject(pole, str(p["id"]), str(p.get("title") or ""), 0.0, params.last_closed, "ouvert",
                                          float(p.get("total_amount") or 0.0), "ajouté automatiquement (nouveau projet Furious)"))
            listed.add((pole, str(p["id"])))
            notes.append(f"Nouveau projet Furious {p['id']} ajouté dans {pole}")

    grid_set = set(grid)
    for f in finance:
        p = by_id.get(f.number)
        closed = f.state == "fermé"
        if closed and not any(M.is_key(k) and k in grid_set for k in state.inputs.get(f"P{f.number}", {})):
            continue  # a closed project shows up only for the months typed on it (a last amount after the close)
        ca = float(p["total_amount"]) if p is not None and float(p["total_amount"] or 0) > 0 else f.ca
        typologie = ", ".join(p["typologies"]) if p is not None else ""
        row = Row(
            ref=f"P{f.number}",
            pole=f.pole,
            category="Signé",
            kind="Projet",
            label=str(p["title"]) if p is not None else f.label,
            owner=_owner(p.get("project_manager")) if p is not None else "",
            typologie=typologie,
            ca=ca,
            produced=f.produced,
            start=_month(p.get("start_date")) if p is not None else None,
            stop=_month(p.get("end_date")) if p is not None else None,
            rule="CONCEPTION" if f.pole == "Conception" else "TRAVAUX",
            kv_default=kv_default(f.pole, typologie, ca, kv),
            proposal_id=str(p.get("proposal_id") or "") if p is not None else "",
            closed=closed,
        )
        if p is None:
            row.alerts.append("Projet introuvable dans Furious (CA de la finance)")
        elif f.ca and abs(f.ca - ca) > max(1.0, 0.01 * ca):
            checks.append((f.pole, row.ref, row.label, f"CA Furious {ca:,.0f} € différent du CA finance {f.ca:,.0f} €".replace(",", " ")))
        rows[f.pole].append(row)

    # Maintenance: contracts (Notion), TS and animations (Furious projects), pipe (Furious devis).
    refs = site_refs(sites)
    year = M.year_of(open_from)
    for s in sites:
        # A contract renews itself: only the months of the window count (nothing "after the window").
        fixed = {} if s.archived else {
            m: v for m, v in monthly_values(s, years).items() if m in grid_set
        }
        value = year_value(s, year)
        planned = passages_in_year(s, year)
        status = "Archivé" if s.archived else ("Perdu" if s.lost else ("Sans montant" if s.annual <= 0 else "Actif"))
        row = Row(
            ref=refs[s.page_id], pole="Maintenance", category="Signé", kind="Contrat", label=s.name, owner=s.owner,
            typologie="Entretien" + (f" ({s.int_ext})" if s.int_ext else ""), ca=s.annual,
            start=_month(s.start), stop=_month(s.end) if s.lost else None, rule="MAINTENANCE",
            kv_default=kv.get("Maintenance", 2.3), fixed=fixed, inactive=not fixed,
            extra={
                "client": s.client, "furious_no": s.furious_id, "status": status, "renewal": s.reconduction,
                "contract_end": s.end, "frequency": s.frequency, "passages_year": planned,
                "passages_done": passages_in_year(s, year, before=today), "year_value": value,
                "per_passage": value / planned if planned else "",
            },
        )
        listed = "-".join(_ascii_words(s.furious_id))
        if listed and site_number(s) and listed != site_number(s):
            checks.append(("Maintenance", row.ref, s.name, f"N° Furious du titre ({site_number(s)}) différent de la colonne "
                                                           f"« ID furious » de Notion ({s.furious_id}) : corriger dans Notion"))
        if s.annual <= 0:
            row.alerts.append("Pas de montant annuel dans Notion")
        elif not s.passages and fixed:
            row.alerts.append("Aucun passage au planning : montant réparti à parts égales")
        rows["Maintenance"].append(row)

    if not projects.empty:
        horizon = pd.Timestamp(today) - pd.DateOffset(months=24)
        for _, p in projects.iterrows():
            if _pole_of_bu(p.get("cf_bu")) != "Maintenance":
                continue
            typ = " ".join(p["typologies"])
            kind = "TS" if "TS" in typ else ("Animation" if "Animation" in typ else None)
            if kind is None:
                continue
            created = pd.to_datetime(p.get("created_at"), errors="coerce")
            if pd.isna(created) or created < horizon:
                continue
            start = p.get("start_date") or p.get("created_at")
            stop = p.get("end_date") or start
            alloc = myrium_allocation("TRAVAUX", float(p["total_amount"] or 0), start, stop)
            fixed = {m: v for m, v in alloc.items() if M.index(m) >= M.index(grid[0])}
            if sum(fixed.values()) <= EPS:
                continue
            rows["Maintenance"].append(Row(
                ref=f"T{p['id']}", pole="Maintenance", category="Signé", kind=kind, label=str(p["title"]),
                proposal_id=str(p.get("proposal_id") or ""),
                owner=_owner(p.get("project_manager")), typologie=typ, ca=float(p["total_amount"] or 0),
                start=_month(start), stop=_month(stop), rule="TRAVAUX",
                kv_default=kv.get("Maintenance TS", 1.7) if kind == "TS" else kv.get("Maintenance", 2.3), fixed=fixed,
            ))

    if not pipe.empty:
        for _, d in pipe.iterrows():
            pole = _pole_of_bu(d.get("final_bu"))
            amount = float(d.get("amount") or 0)
            if pole is None or amount <= 0:
                continue
            typ = str(d.get("cf_typologie_de_devis") or d.get("cf_typologie_myrium") or "")
            rule = "TRAVAUX" if (pole == "Maintenance" and "TS" in typ.upper().split()) else d["final_bu"]
            row = Row(
                ref=f"D{d['id']}", pole=pole, category="Pipe", kind="Devis", label=str(d.get("title") or ""),
                owner=_owner(d.get("assigned_to")), typologie=typ, ca=amount,
                proba=float(d["probability_factor"]) if pd.notna(d.get("probability_factor")) else None,
                start=_month(d.get("projet_start")), stop=_month(d.get("projet_stop")),
                proposal_date=_month(d.get("date")), rule=str(rule).upper(),
                kv_default=kv_default(pole, typ, amount, kv),
            )
            raw = d.get("probability")
            if pd.notna(raw) and float(raw) == 0:
                row.proba = 0.0
            devis_date = pd.to_datetime(d.get("date"), errors="coerce")
            if pd.notna(devis_date) and devis_date < pd.Timestamp(today) - pd.DateOffset(months=12):
                checks.append((pole, row.ref, row.label, f"Devis en attente depuis le {devis_date:%d/%m/%Y} : à gagner ou perdre dans Furious"))
            else:
                alloc = myrium_allocation(str(rule).upper(), amount, row.start, row.stop, row.proposal_date)
                if alloc and M.index(min(alloc)) <= M.index(M.key(today.year, today.month)):
                    checks.append((pole, row.ref, row.label, f"Début prévu {M.label(min(alloc))}, devis pas encore signé : "
                                                             f"compté à partir du mois prochain, à redater dans Furious"))
            rows[pole].append(row)

    # Manual lines live only in the typed values.
    for ref, typed in state.inputs.items():
        if not re.fullmatch(r"M\d+", ref):
            continue
        pole = typed.get("_pole")
        if pole not in rows:
            continue
        rows[pole].append(Row(
            ref=ref, pole=pole, category=typed.get("cat") if typed.get("cat") in ("Signé", "Pipe") else "Signé",
            kind="Manuel", label=str(typed.get("label") or "Ligne manuelle"), rule="TRAVAUX",
            kv_default=kv_default(pole, "", 0, kv),
        ))

    # A devis won in Furious leaves the pipe and becomes a project (P or T row). What people typed on
    # the devis (months, KV, cost, dates, comment) moves to the project once, unless the project has its own.
    inputs = {ref: dict(v) for ref, v in state.inputs.items()}
    pending_refs = {r.ref for items in rows.values() for r in items if r.kind == "Devis"}
    for items in rows.values():
        for r in items:
            source = f"D{r.proposal_id}" if r.proposal_id else ""
            if not source or source in pending_refs or source not in inputs:
                continue
            moved = {k: v for k, v in inputs.pop(source).items() if k not in ("proba", "cat", "label", "ca", "_pole")}
            target = inputs.setdefault(r.ref, {})
            for k, v in moved.items():
                target.setdefault(k, v)
            notes.append(f"Devis {r.proposal_id} gagné : ses saisies passent sur la ligne {r.ref}")

    current = M.key(today.year, today.month)
    pending_from = max(M.add(current, 1), open_from, key=M.index)  # a devis not signed yet starts next month at the earliest
    out_rows: Dict[str, List[Tuple[Row, Shown]]] = {}
    for pole, items in rows.items():
        shown = []
        for row in items:
            typed = {k: v for k, v in inputs.get(row.ref, {}).items() if not k.startswith("_")}
            s = compute_row(row, typed, grid, open_from, params.fallback_months, pending_from)
            if row.kind in ("Projet", "Devis", "Manuel", "Contrat") or abs(s.remaining) > EPS or s.pinned:
                shown.append((row, s))
            for a in s.alerts:
                if a.startswith(("Saisies supérieures", "Réalisé supérieur", "Projet introuvable")) or "sans mois ouvert" in a:
                    checks.append((pole, row.ref, s.label, a))
        shown.sort(key=lambda rs: (
            rs[0].inactive, CATEGORY_ORDER.get(rs[1].category, 9), KIND_ORDER.get(rs[0].kind, 9),
            rs[0].owner.lower(), rs[1].label.lower(),
        ))
        out_rows[pole] = shown

    return Output(grid, open_from, out_rows, inputs, finance, checks, notes, pending_from)
