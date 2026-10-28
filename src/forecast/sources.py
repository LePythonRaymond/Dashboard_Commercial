"""
Readers for the production forecast. All of them only read.

- Furious projects (signed work) and pending devis (the pipe), through Myrium's clients.
- Notion "Sites" (maintenance contracts) and "Planning" (one page per passage).
- The legacy workbook ("Forecast 2", exported to .xlsx) for the one-time start-up.
"""

from __future__ import annotations

import logging
import re
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import pandas as pd
import requests

from config.settings import STATUS_WAITING, get_secret

from . import months as M
from .maintenance import Site, passage_dates_by_site, passages_by_site

logger = logging.getLogger(__name__)

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

NOTION_RETRY_WAITS = (2, 5, 15)  # seconds before the 2nd, 3rd and 4th attempt


def _post_with_retry(url: str, body: dict, headers: dict, waits: Sequence[float] = NOTION_RETRY_WAITS) -> dict:
    """One Notion query page. A dropped connection, a timeout, a 429 or a 5xx is retried: the Planning
    takes many pages, and one network hiccup used to cost the whole night's refresh (2026-10-27)."""
    for attempt in range(len(waits) + 1):
        try:
            resp = requests.post(url, json=body, headers=headers, timeout=90)
            if resp.status_code != 429 and resp.status_code < 500:
                resp.raise_for_status()
                return resp.json()
            error: Exception = requests.HTTPError(f"Notion {resp.status_code}", response=resp)
        except (requests.ConnectionError, requests.Timeout) as exc:
            error = exc
        if attempt == len(waits):
            raise error
        logger.warning("Notion query failed (%s), new attempt in %ss", error, waits[attempt])
        time.sleep(waits[attempt])
    raise AssertionError("unreachable")


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
        data = _post_with_retry(url, body, headers)
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


CORRECTION_COMMENT = (
    "Ventilation du trop comptabilisé : à fin août, la compta (1 859 642 €) dépasse de 114 121 € la somme des "
    "projets. Arthur l'étale à −28 530 € par mois de septembre à décembre (Projets Travaux, ligne 108). "
    "Situation exceptionnelle : à supprimer dès que le suivi de production de la compta remplace les montants des projets."
)


def load_legacy(xlsx_path: Path, last_closed: str = "2026-08", first_open: Optional[str] = None) -> Legacy:
    """Read the start-up data from the exported workbook (cached values, no formula is evaluated).

    ``last_closed`` is the last month the accounts have closed (August 2026: the workbook's
    "P&L fin août"). Months up to it count as produced; later months are typed values (pins).
    A project marked finished stays closed unless it has an amount from ``first_open`` on (the first month
    still to come; default the month after ``last_closed``). A closed project keeps only its typed months.
    - Travaux: produced = before 2026 (N) + first-half cut-off (S) + the chefs de projet's July to
      ``last_closed`` amounts (columns JUIL...), since the workbook has no per-project figure after June.
      Pins = the following months up to December, 0 when empty.
    - Conception: produced = before 2026 (AP) + 2026 to date (AU, which adds up to the accounts at
      the end of August: 430 259 € against 423 060 €). Pins = "Prod Sept" to "Prod Dec" after ``last_closed``.
    """
    import openpyxl

    wb = openpyxl.load_workbook(xlsx_path, data_only=True, read_only=True)
    closed_i = M.index(last_closed)
    open_i = M.index(first_open or M.add(last_closed, 1))
    rows: List[LegacyRow] = []

    ws = wb["Projets Travaux"]
    data = list(ws.iter_rows(min_row=1, max_row=103, values_only=True))
    head = {name: i for i, name in enumerate(data[0]) if name}
    col = lambda r, name: r[head[name]]
    trx_months = {"JUIL": "2026-07", "AOUT": "2026-08", "SEPT": "2026-09", "OCT": "2026-10", "NOV": "2026-11", "DEC": "2026-12"}
    for r in data[1:]:
        number = _opt(col(r, "N° Projet"))
        if number is None:
            continue
        planned = {m: col(r, name) for name, m in trx_months.items()}
        before = [v for m, v in planned.items() if M.index(m) <= closed_i]
        after = {m: v for m, v in planned.items() if M.index(m) > closed_i}
        closed = str(col(r, "Etat") or "").strip().lower().startswith("ferm") and not any(
            _num(v) for m, v in after.items() if M.index(m) >= open_i)
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
                + sum(_num(v) for v in before),
                kv=_opt(col(r, "kv obj (hors 100%)")),
                pins={m: _num(v) for m, v in after.items() if not closed or _num(v)},
                comment=str(col(r, "Commentaire") or "").strip(),
            )
        )

    ws = wb["Projets Études V2"]
    data = list(ws.iter_rows(min_row=1, max_row=135, values_only=True))
    head = {name: i for i, name in enumerate(data[1]) if name}
    kv_col = [i for i, name in enumerate(data[1]) if name == "KV prévu"][-1]
    con_months = {"Prod Sept": "2026-09", "Prod Oct": "2026-10", "Prod Nov": "2026-11", "Prod Dec": "2026-12"}
    for r in data[2:]:
        label = str(r[head["Libellé"]] or "").strip()
        if not label:
            continue
        planned = {m: r[head[name]] for name, m in con_months.items()}
        before = [v for m, v in planned.items() if M.index(m) <= closed_i]
        after = {m: v for m, v in planned.items() if M.index(m) > closed_i}
        any_after = any(_num(v) for v in after.values())
        number = _opt(r[head["N° Projet"]])
        finished = str(r[head["Statut"]] or "").lower().startswith("ferm") or str(r[0] or "").lower() == "terminé"
        closed = finished and not any(_num(v) for m, v in after.items() if M.index(m) >= open_i)
        rows.append(
            LegacyRow(
                pole="Conception",
                number=str(int(number)) if number is not None else "",
                label=label,
                state="fermé" if closed else "ouvert",
                owner=str(r[head["Conduc/CDP"]] or "").strip(),
                ca=_num(r[head["CA"]]),
                part_mai=0.0,
                produced=_num(r[head["Prod à fin 2025"]]) + _num(r[head["Prod 2026"]]) + sum(_num(v) for v in before),
                kv=_opt(r[kv_col]),
                pins={} if number is None and not any_after else {m: _num(v) for m, v in after.items() if not closed or _num(v)},
            )
        )

    syn = wb["Synthèse Pôles 2026"]
    pole_rows = {"Maintenance": 7, "Travaux": 8, "Conception": 9}
    pl_report = {pole: _num(syn[f"C{r}"].value) for pole, r in pole_rows.items()}
    # September estimate per pole: only used when September is treated as closed (older start-ups).
    sept = {pole: _num(syn[f"D{r}"].value) for pole, r in pole_rows.items()} if closed_i >= M.index("2026-09") else {}
    trx = wb["Projets Travaux"]
    correction = _num(trx["AK107"].value)  # accounts at the end of August minus the projects' sum
    adjustments = []
    if correction:
        per_month = correction / 4  # the workbook spreads it over September to December (row 108)
        spread = ["2026-09", "2026-10", "2026-11", "2026-12"]
        if "Travaux" in sept:
            sept["Travaux"] -= per_month * sum(1 for m in spread if M.index(m) <= closed_i)
        adjustments.append(
            {
                "pole": "Travaux",
                "label": "Correction compta fin août (reprise du fichier d'Arthur)",
                "pins": {m: -per_month for m in spread if M.index(m) > closed_i},
                "comment": CORRECTION_COMMENT,
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
