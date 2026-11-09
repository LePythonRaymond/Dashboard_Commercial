"""
Furious reads for the Suivi de production. Read only.

Four sources, each one row per Furious record:

- invoices (/stand-alone-invoice/, "Invoices"): issued invoices and credit notes (statut 1 or 2), plus
  the planned ones (statut 0: no number, no date, they are the invoicing schedule). A cancelled invoice
  and its credit note are both present, so issued rows are summed as they are.
- purchases (/purchase/, "Purchase"): supplier invoices; "cost" is the amount before tax. A purchase
  received in parts keeps its full order amount in "cost"; the parts are a separate entity
  ("PurchasePartial", linked by bc_number = purchase id), not read here yet.
- time (/sprint/, "Sprint"): time entries; "time" is in days and "cjm_sprint" is the cost of the
  entry, already multiplied (0.357 days at 250 € a day gives cjm_sprint = 89.29).
- budgets (/project-rate-card/, "ProjectRateCard"): the budget lines of each project.

Every read goes through ``fetch_all``, which refuses to return a silently wrong list: an HTTP error is
retried then raised, a GraphQL error or a missing result is raised, and a page that repeats ids
(a pagination loop) is raised. The caller checks the counts (see ``src/run_checks.py``).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

import requests

from config.settings import settings

logger = logging.getLogger(__name__)

PAGE = 250
RETRY_WAITS = (5, 20, 60)


class FuriousReadError(RuntimeError):
    """A Furious read that cannot be trusted: the run must stop."""


@dataclass(frozen=True)
class Source:
    key: str  # our name
    path: str  # API path
    entity: str  # GraphQL root field
    fields: Sequence[str]


SOURCES: Dict[str, Source] = {s.key: s for s in [
    Source("invoices", "stand-alone-invoice", "Invoices",
           ("id", "project_id", "statut", "is_cancelled", "invoice_date", "amount_ht", "entity", "cf_project_bu")),
    Source("purchases", "purchase", "Purchase",
           ("id", "project_id", "cost", "statut", "purchase_date", "document_date", "created_at", "type_name", "bu_id")),
    Source("time", "sprint", "Sprint",
           ("id", "project_id", "time", "start_date", "cjm_sprint", "assigned_people_name", "is_validated", "bu_id")),
    Source("budgets", "project-rate-card", "ProjectRateCard",
           ("id", "project_id", "quantity", "amount", "unit_type", "cjm", "category", "rate_card_row_label")),
]}


def _get(url: str, headers: dict) -> dict:
    """One page. Network errors, 429 and 5xx are retried; anything else is raised at once."""
    for attempt in range(len(RETRY_WAITS) + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=120)
            if resp.status_code != 429 and resp.status_code < 500:
                resp.raise_for_status()
                return resp.json()
            error: Exception = requests.HTTPError(f"Furious {resp.status_code}", response=resp)
        except (requests.ConnectionError, requests.Timeout) as exc:
            error = exc
        if attempt == len(RETRY_WAITS):
            raise FuriousReadError(f"{url.split('?')[0]}: {error}") from error
        logger.warning("Furious read failed (%s), new attempt in %ss", error, RETRY_WAITS[attempt])
        time.sleep(RETRY_WAITS[attempt])
    raise AssertionError("unreachable")


def fetch_all(auth, source: Source, filters: str = "") -> List[dict]:
    """Every row of ``source`` (optionally filtered, GraphQL filter syntax), page by page."""
    rows: List[dict] = []
    seen: set = set()
    offset = 0
    while True:
        filt = f",filter:{{{filters}}}" if filters else ""
        query = "{" + f"{source.entity}(limit:{PAGE},offset:{offset}{filt}){{{','.join(source.fields)}}}" + "}"
        data = _get(f"{settings.furious_api_url}/{source.path}/?query={requests.utils.quote(query)}", auth.get_headers())
        if data.get("errors"):
            raise FuriousReadError(f"{source.key}: {data['errors'][0].get('message', data['errors'])}")
        page = (data.get("data") or {}).get(source.entity)
        if page is None:
            raise FuriousReadError(f"{source.key}: no '{source.entity}' in the answer at offset {offset}")
        ids = {str(r.get("id")) for r in page} - {"0", "None", ""}  # planned invoices all have id "0"
        if offset and ids and ids <= seen:
            raise FuriousReadError(f"{source.key}: page at offset {offset} repeats rows already read")
        seen |= ids
        rows.extend(page)
        if len(page) < PAGE:
            return rows
        offset += PAGE


def fetch_sources(auth, keys: Optional[Sequence[str]] = None) -> Dict[str, List[dict]]:
    """All four sources (or ``keys``), each read in full."""
    out = {}
    for key in keys or SOURCES:
        started = time.time()
        out[key] = fetch_all(auth, SOURCES[key])
        logger.info("Furious %s: %d rows in %.0f s", key, len(out[key]), time.time() - started)
    return out


def num(value) -> float:
    """Furious numbers arrive as numbers, strings or null."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def day(value) -> str:
    """ISO day of a Furious date or datetime ("" when empty)."""
    return str(value or "")[:10]
