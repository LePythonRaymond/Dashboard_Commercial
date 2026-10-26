"""
Readers for the production forecast. All of them only read.

- Furious projects (signed work) and pending devis (the pipe), through Myrium's clients.
- Notion "Sites" (maintenance contracts) and "Planning" (one page per passage).
- The legacy workbook ("Forecast 2", exported to .xlsx) for the one-time start-up.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import requests

from config.settings import STATUS_WAITING, get_secret

from . import months as M
from .maintenance import Site, passage_dates_by_site, passages_by_site

NOTION_VERSION = "2025-09-03"
SITES_DATA_SOURCE = "285d9278-02d7-808a-9395-000b04dfc654"
PLANNING_DATA_SOURCE = "342d9278-02d7-8016-9336-000b5f9f3f81"

PROJECT_FIELDS = [
    "id", "title", "total_amount", "total_cost", "start_date", "end_date", "planned_end_date",
    "created_at", "project_manager", "cf_bu", "cf_typologie_myrium", "proposal_id", "progress", "actif",
]


def norm(text: object) -> str:
    """Lower case, no accents, single spaces: for matching labels written by hand."""
    s = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode().lower()
    s = s.replace("&#039", "'")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


# --------------------------------------------------------------------------- Furious

def fetch_projects(auth) -> pd.DataFrame:
    """Every Furious project (all BUs) with the fields the forecast needs."""
    from src.api.projects import ProjectsClient

    client = ProjectsClient(auth=auth)
    client.fields = list(PROJECT_FIELDS)
    df = client.fetch_all()
    if df.empty:
        return df
    df["id"] = df["id"].astype(str)
    df["total_amount"] = pd.to_numeric(df["total_amount"], errors="coerce").fillna(0.0)
    df["typologies"] = (
        df["cf_typologie_myrium"].fillna("").astype(str).str.split(r",|\|#\|").map(lambda xs: [x.strip() for x in xs if x.strip()])
    )
    return df


def fetch_pipe(auth, project_root: Path) -> pd.DataFrame:
    """Pending devis exactly as the dashboard sees them (avenants merged, cleaned, dashboard overrides applied)."""
    from src.api.proposal_addons import ProposalAddonsClient, merge_addons_into_proposals
    from src.api.proposals import ProposalsClient
    from src.processing.cleaner import DataCleaner
    from src.processing.manual_and_overrides import apply_input_overrides, get_overrides_store

    df = ProposalsClient(auth=auth).fetch_all()
    try:
        addons = ProposalAddonsClient(auth=auth).fetch_all()
        df, _ = merge_addons_into_proposals(df, addons, target_year=datetime.now().year)
    except Exception:  # same tolerance as the daily pipeline: amounts without avenants
        df["addon_amount"] = 0
    df = DataCleaner().clean(df)
    df = apply_input_overrides(df, get_overrides_store(project_root))
    waiting = [s.lower() for s in STATUS_WAITING]
    df = df[df["statut"].astype(str).str.lower().isin(waiting)].copy()
    df["id"] = df["id"].astype(str)
    return df


# --------------------------------------------------------------------------- Notion

def _notion_query_all(data_source_id: str, api_key: str) -> List[dict]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }
    url = f"https://api.notion.com/v1/data_sources/{data_source_id}/query"
    pages, cursor = [], None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        resp = requests.post(url, json=body, headers=headers, timeout=90)
        resp.raise_for_status()
        data = resp.json()
        pages.extend(data.get("results", []))
        cursor = data.get("next_cursor")
        if not cursor:
            return pages


def _date(prop: dict) -> Optional[date]:
    start = ((prop or {}).get("date") or {}).get("start")
    return date.fromisoformat(start[:10]) if start else None


def _text(prop: dict) -> str:
    items = (prop or {}).get("rich_text") or (prop or {}).get("title") or []
    return "".join(t.get("plain_text", "") for t in items).strip()


def fetch_sites(api_key: Optional[str] = None) -> List[Site]:
    """Maintenance contracts (Notion "Sites") with their passages (Notion "Planning")."""
    api_key = api_key or get_secret("NOTION_API_KEY", "")
    planning = _notion_query_all(PLANNING_DATA_SOURCE, api_key)
    passages = passages_by_site(planning)
    dates = passage_dates_by_site(planning)
    sites = []
    for page in _notion_query_all(SITES_DATA_SOURCE, api_key):
        p = page["properties"]
        reconduction = [o["name"] for o in (p.get("Reconduction") or {}).get("multi_select", [])]
        if "TEST-SOLIDITE" in reconduction:
            continue
        page_id = page["id"].replace("-", "")
        sites.append(
            Site(
                page_id=page_id,
                name=_text(p.get("Nom")),
                annual=float((p.get("Total HT Annuel") or {}).get("number") or 0.0),
                start=_date(p.get("Date debut contrat")),
                end=_date(p.get("Date fin Contrat")),
                lost="Perdu" in reconduction,
                archived=bool((p.get("Archivé") or {}).get("checkbox")),
                furious_id=_text(p.get("ID furious")),
                int_ext=((p.get("INT EXT ?") or {}).get("select") or {}).get("name", "") or "",
                passages=passages.get(page_id, {}),
                owner=", ".join(u.get("name", "") for u in (p.get("Jardinier referent") or {}).get("people", []) if u.get("name")),
                client=_text(p.get("Client")),
                frequency=", ".join(o["name"] for o in (p.get("Fréquence") or {}).get("multi_select", [])),
                reconduction=", ".join(reconduction),
                dates=dates.get(page_id, []),
            )
        )
    return sites


# --------------------------------------------------------------------------- legacy workbook

@dataclass
class LegacyRow:
    pole: str  # "Travaux" or "Conception"
    number: str  # Furious project number, "" for a named opportunity
    label: str
    state: str  # "ouvert", "fermé"
    owner: str
    ca: float
    part_mai: float
    produced: float  # cumulative production at the last close (end of September 2026)
    kv: Optional[float]
    pins: Dict[str, float]
    comment: str = ""


@dataclass
class Legacy:
    rows: List[LegacyRow]
    pl_report: Dict[str, float]  # production January to August (P&L), per pole
    pl_september: Dict[str, float]  # September estimate per pole
    objectives: Dict[str, tuple]  # pole -> (annual objective, KV)
    kv: Dict[str, float]  # typologie -> default KV
    adjustments: List[dict] = field(default_factory=list)
    s1_kv: Dict[str, float] = field(default_factory=dict)  # real KV of the first half (accounts at the end of June)


def _num(value) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return 0.0


def _opt(value) -> Optional[float]:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def load_legacy(xlsx_path: Path, last_closed: str = "2026-09") -> Legacy:
    """Read the start-up data from the exported workbook (cached values, no formula is evaluated).

    Travaux: cumulative production at the end of September = before 2026 (N) + S1 cut-off (S)
    + the July, August and September production the chefs de projet planned (AJ to AL), since
    the workbook has no clean per-project figure after June. Pins = October to December.
    Conception: before 2026 (AP) + 2026 to date (AU, "Résultat T1 à T3"). Pins = AZ to BB.
    """
    import openpyxl

    wb = openpyxl.load_workbook(xlsx_path, data_only=True, read_only=True)
    oct_, nov, dec = M.add(last_closed, 1), M.add(last_closed, 2), M.add(last_closed, 3)
    rows: List[LegacyRow] = []

    ws = wb["Projets Travaux"]
    data = list(ws.iter_rows(min_row=1, max_row=103, values_only=True))
    head = {name: i for i, name in enumerate(data[0]) if name}
    col = lambda r, name: r[head[name]]
    for r in data[1:]:
        number = _opt(col(r, "N° Projet"))
        if number is None:
            continue
        planned = [col(r, m) for m in ("JUIL", "AOUT", "SEPT", "OCT", "NOV", "DEC")]
        q4_planned = any(_num(v) for v in planned[3:])
        closed = str(col(r, "Etat") or "").strip().lower().startswith("ferm") and not q4_planned
        rows.append(
            LegacyRow(
                pole="Travaux",
                number=str(int(number)),
                label=str(col(r, "Libellé") or "").strip(),
                state="fermé" if closed else "ouvert",
                owner=str(col(r, "Conduc/CDP") or "").strip(),
                ca=_num(col(r, "CA")),
                part_mai=_num(col(r, "Part MAI à extraire")),
                produced=_num(col(r, "Prod cumulée à fin 2025")) + _num(col(r, "Prod S1 2026 Cut Off"))
                + sum(_num(v) for v in planned[:3]),
                kv=_opt(col(r, "kv obj (hors 100%)")),
                pins={} if closed else {oct_: _num(planned[3]), nov: _num(planned[4]), dec: _num(planned[5])},
                comment=str(col(r, "Commentaire") or "").strip(),
            )
        )

    ws = wb["Projets Études V2"]
    data = list(ws.iter_rows(min_row=1, max_row=135, values_only=True))
    head = {name: i for i, name in enumerate(data[1]) if name}
    kv_col = [i for i, name in enumerate(data[1]) if name == "KV prévu"][-1]
    for r in data[2:]:
        label = str(r[head["Libellé"]] or "").strip()
        if not label:
            continue
        planned = [r[head[m]] for m in ("Prod Sept", "Prod Oct", "Prod Nov", "Prod Dec")]
        q4_planned = any(_num(v) for v in planned[1:])
        number = _opt(r[head["N° Projet"]])
        closed = (str(r[head["Statut"]] or "").lower().startswith("ferm") or str(r[0] or "").lower() == "terminé") and not q4_planned
        rows.append(
            LegacyRow(
                pole="Conception",
                number=str(int(number)) if number is not None else "",
                label=label,
                state="fermé" if closed else "ouvert",
                owner=str(r[head["Conduc/CDP"]] or "").strip(),
                ca=_num(r[head["CA"]]),
                part_mai=0.0,
                produced=_num(r[head["Prod à fin 2025"]]) + _num(r[head["Prod 2026"]]),
                kv=_opt(r[kv_col]),
                pins={} if closed or (number is None and not q4_planned) else
                {oct_: _num(planned[1]), nov: _num(planned[2]), dec: _num(planned[3])},
            )
        )

    syn = wb["Synthèse Pôles 2026"]
    pole_rows = {"Maintenance": 7, "Travaux": 8, "Conception": 9}
    pl_report = {pole: _num(syn[f"C{r}"].value) for pole, r in pole_rows.items()}
    sept = {pole: _num(syn[f"D{r}"].value) for pole, r in pole_rows.items()}
    trx = wb["Projets Travaux"]
    correction = _num(trx["AK107"].value)  # P&L at the end of August minus the projects' sum
    adjustments = []
    if correction:
        per_month = correction / 4  # spread over September to December in the workbook
        sept["Travaux"] -= per_month
        adjustments.append(
            {
                "pole": "Travaux",
                "label": "Correction compta fin août (reprise du fichier d'Arthur)",
                "pins": {oct_: -per_month, nov: -per_month, dec: -per_month},
            }
        )

    s = wb["Synthèse"]
    objectives = {
        "Conception": (_num(s["C7"].value), _num(s["D7"].value)),
        "Travaux": (_num(s["M7"].value), 1.47),
        "Maintenance": (_num(s["W7"].value), _num(s["X7"].value)),
    }
    infos = wb["Infos"]
    kv = {
        "Travaux Direct": _num(infos["C3"].value),
        "Travaux Conception": _num(infos["C4"].value),
        "Travaux DV": _num(infos["C5"].value),
        "Travaux TS": _num(infos["C6"].value),
        "Travaux DV +100k": _num(infos["C7"].value),
        "Conception": _num(infos["I3"].value),
        "Maintenance": _num(infos["U3"].value),
        "Maintenance TS": _num(infos["U7"].value),
    }
    tcd = next((wb[n] for n in wb.sheetnames if n.startswith("Tableau croisé dynamique") and wb[n]["C21"].value), None)
    s1_kv = {
        "Travaux": _num(tcd["C21"].value) if tcd is not None else 0.0,  # "coef de kv 1,5 pris sur P&L - fin juin"
        "Conception": _num(s["D4"].value),
        "Maintenance": _num(s["X4"].value),
    }
    return Legacy(rows, pl_report, sept, objectives, kv, adjustments, s1_kv)
