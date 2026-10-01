"""
Notion access for the EPI form (REST API, Notion-Version 2025-09-03).

Three bases are read (team, catalogue) and two are written:
- "Demandes EPI": one page per request (who, what, when), status En attente,
  Validée or Refusée;
- "Registre EPI": one line per requested article, linked to its request.
Validating a request gives each pending line its decision (Remis by default,
or À commander, or Refusé). A "Remis" line is what makes the stock formulas of
"Articles EPI" move, by themselves.
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import requests

API = "https://api.notion.com/v1"
NOTION_VERSION = "2025-09-03"
RETRYABLE = {409, 429, 500, 502, 503, 504}
# Notion accepts about 3 requests per second per integration: writing 3 pages
# at once divides the wait by 3 without tripping the limit (a 429 is retried).
PARALLEL_CALLS = 3

# Which size of "Équipe EPI" pre-selects the articles of each family.
SIZE_FIELD_BY_FAMILY = {
    "T-shirt": "T-shirt",
    "Pantalon": "Pantalon",
    "Chaussures": "Chaussures",
    "Polaire": "Polaire / pull",
    "Coupe-vent": "Coupe-vent",
    "Veste hiver": "Manteau",
    "Gants": "Gants",
}
FAMILY_ORDER = ["T-shirt", "Pantalon", "Short", "Polaire", "Coupe-vent", "Veste hiver", "Kit pluie", "Chaussures",
                "Gants", "Casquette", "Casque", "Lunettes", "Protection auditive", "Chasuble", "Surchaussures",
                "Harnais", "Outil", "Autre"]
MOTIFS = ["Nouvel arrivant", "Usure", "Perte", "Casse", "Mauvaise taille", "Saison", "Chantier", "Autre"]
PENDING_LINE_STATUSES = {None, "", "Demandé"}
WAITING = "En attente"
VALIDATED = "Validée"
REFUSED = "Refusée"
# What the office can decide for each article when it validates a request.
# Only "Remis" moves the stock; "À commander" feeds the order list.
HANDED, TO_ORDER, LINE_REFUSED = "Remis", "À commander", "Refusé"
LINE_CHOICES = (HANDED, TO_ORDER, LINE_REFUSED)
# Set by the office on a worn item; the service turns it into WORN_OUT plus a replacement line,
# directly "À commander" or, when a new one is already on the shelf, "Prêt à récupérer".
TO_REPLACE, WORN_OUT, READY = "À remplacer", "Hors d'usage", "Prêt à récupérer"


class NotionError(RuntimeError):
    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status   # HTTP status of the failed call, when there was one


class RequestGone(NotionError):
    """The request page does not exist, is in the trash, or is not a page of "Demandes EPI"."""


class NotionClient:
    """Minimal REST client: retries rate limits and server errors with backoff.

    Each thread gets its own HTTP session, so parallel calls are safe and each
    thread keeps its connection to Notion open between calls (no new TLS
    handshake every time).
    """

    def __init__(self, token: str, session: Optional[requests.Session] = None,
                 sleep: Callable[[float], None] = time.sleep, attempts: int = 5):
        self._shared_session = session
        self._local = threading.local()
        self.headers = {"Authorization": f"Bearer {token}", "Notion-Version": NOTION_VERSION,
                        "Content-Type": "application/json"}
        self.sleep = sleep
        self.attempts = attempts

    @property
    def session(self) -> requests.Session:
        if self._shared_session is not None:
            return self._shared_session
        if getattr(self._local, "session", None) is None:
            self._local.session = requests.Session()
        return self._local.session

    def call(self, method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        delay = 1.0
        for attempt in range(1, self.attempts + 1):
            response = self.session.request(method, f"{API}/{path}", headers=self.headers, json=body, timeout=30)
            if response.status_code in RETRYABLE and attempt < self.attempts:
                self.sleep(float(response.headers.get("Retry-After", delay)))
                delay *= 2
                continue
            if response.status_code >= 400:
                raise NotionError(f"{method} {path} -> {response.status_code}: {response.text[:300]}",
                                  status=response.status_code)
            return response.json()
        raise NotionError(f"{method} {path}: too many retries")  # pragma: no cover

    def query_all(self, data_source_id: str, body: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        body = dict(body or {}, page_size=100)
        pages: List[Dict[str, Any]] = []
        while True:
            result = self.call("POST", f"data_sources/{data_source_id}/query", body)
            pages.extend(result.get("results", []))
            if not result.get("has_more"):
                return pages
            body["start_cursor"] = result["next_cursor"]

    def create_page(self, data_source_id: str, properties: Dict[str, Any]) -> Dict[str, Any]:
        return self.call("POST", "pages", {"parent": {"data_source_id": data_source_id}, "properties": properties})

    def update_page(self, page_id: str, properties: Dict[str, Any]) -> Dict[str, Any]:
        return self.call("PATCH", f"pages/{page_id}", {"properties": properties})

    def get_page(self, page_id: str) -> Dict[str, Any]:
        return self.call("GET", f"pages/{page_id}")

    def trash_page(self, page_id: str) -> Dict[str, Any]:
        return self.call("PATCH", f"pages/{page_id}", {"in_trash": True})

    def users(self) -> List[Dict[str, Any]]:
        found, cursor = [], None
        while True:
            result = self.call("GET", "users?page_size=100" + (f"&start_cursor={cursor}" if cursor else ""))
            found.extend(u for u in result.get("results", []) if u.get("type") == "person")
            if not result.get("has_more"):
                return found
            cursor = result["next_cursor"]


def in_parallel(calls: List[Callable[[], Any]]) -> List[Tuple[Any, Optional[Exception]]]:
    """Run the calls PARALLEL_CALLS at a time; one (result, error) pair per call, in order.

    Example: 4 register lines at about 0.55 s each take about 1.1 s instead of 2.2 s.
    Every call runs to its end, even when another one fails, so the caller knows
    exactly which pages exist (to put them in the trash, for instance).
    """
    def attempt(call: Callable[[], Any]) -> Tuple[Any, Optional[Exception]]:
        try:
            return call(), None
        except Exception as exc:  # reported to the caller, never swallowed
            return None, exc

    if len(calls) <= 1:
        return [attempt(call) for call in calls]
    with ThreadPoolExecutor(max_workers=min(PARALLEL_CALLS, len(calls))) as pool:
        return list(pool.map(attempt, calls))


# --------------------------------------------------------------------- values
def plain(prop: Optional[Dict[str, Any]]) -> Any:
    """Python value of a Notion property as returned by the API."""
    kind = (prop or {}).get("type")
    value = (prop or {}).get(kind)
    if kind in ("title", "rich_text"):
        return "".join(part.get("plain_text", "") for part in value or [])
    if kind in ("select", "status"):
        return (value or {}).get("name")
    if kind == "people":
        return [person["id"] for person in value or []]
    if kind == "relation":
        return [item["id"] for item in value or []]
    if kind == "date":
        return (value or {}).get("start")
    if kind in ("number", "checkbox", "url", "created_time"):
        return value
    if kind in ("formula", "rollup"):
        return (value or {}).get((value or {}).get("type"))
    if kind == "unique_id":
        return f"{value.get('prefix')}-{value.get('number')}" if value and value.get("prefix") else (value or {}).get("number")
    return None


def _prop(page: Dict[str, Any], name: str) -> Any:
    return plain((page.get("properties") or {}).get(name))


def _text(value: str) -> Dict[str, Any]:
    chunks = [value[i:i + 1900] for i in range(0, len(value), 1900)] if value else []
    return {"rich_text": [{"text": {"content": chunk}} for chunk in chunks]}


def _title(value: str) -> Dict[str, Any]:
    return {"title": [{"text": {"content": value[:200]}}]}


def _select(name: Optional[str]) -> Dict[str, Any]:
    return {"select": {"name": name} if name else None}


def _people(user_ids: Iterable[Optional[str]]) -> Dict[str, Any]:
    return {"people": [{"object": "user", "id": uid} for uid in user_ids if uid]}


def _relation(page_ids: Iterable[Optional[str]]) -> Dict[str, Any]:
    return {"relation": [{"id": pid} for pid in page_ids if pid]}


def _date(day: date) -> Dict[str, Any]:
    return {"date": {"start": day.isoformat()}}


# ---------------------------------------------------------------------- reads
def load_team(client: NotionClient, equipe_ds: str) -> List[Dict[str, Any]]:
    """Active people with their sizes and Notion account, sorted by name."""
    pages = client.query_all(equipe_ds, {"filter": {"property": "Statut", "select": {"equals": "Actif"}}})
    team = []
    for page in pages:
        name = (_prop(page, "Nom") or "").strip()
        if not name:
            continue
        accounts = _prop(page, "Compte Notion") or []
        team.append({
            "id": page["id"],
            "name": name,
            "user_id": accounts[0] if accounts else None,
            "sizes": {field: _prop(page, field) for field in sorted(set(SIZE_FIELD_BY_FAMILY.values()))},
        })
    return sorted(team, key=lambda p: p["name"].casefold())


def load_catalogue(client: NotionClient, articles_ds: str) -> List[Dict[str, Any]]:
    """Active new articles (used ones are handed out by the office), in family order."""
    pages = client.query_all(articles_ds, {"filter": {"and": [
        {"property": "Actif", "checkbox": {"equals": True}},
        {"property": "État", "select": {"does_not_equal": "Usé"}},
    ]}})
    rank = {family: i for i, family in enumerate(FAMILY_ORDER)}
    items = [{
        "id": page["id"],
        "title": _prop(page, "Article") or "",
        "famille": _prop(page, "Famille") or "Autre",
        "taille": _prop(page, "Taille") or "",
        "mode": _prop(page, "Mode") or "Dotation",
        "stock": _prop(page, "Stock"),
        "disponible": _prop(page, "Disponible"),
        "pending": _prop(page, "Demandé") or 0,     # units requested and not handed out yet, all requests
        "a_compter": bool(_prop(page, "À compter")),
    } for page in pages]
    return sorted(items, key=lambda i: (rank.get(i["famille"], len(rank)), i["title"].casefold()))


def summarize(lines: List[Dict[str, Any]]) -> str:
    """'T-shirt noir · M × 2' per line: the request at a glance, in Notion and in the e-mail."""
    return "\n".join(f"{line['title']} × {line['qty']}" for line in lines)


def stock_label(item: Optional[Dict[str, Any]]) -> str:
    """Units on the shelf as the office reads them: a number, "à compter" (never counted) or "?"."""
    if not item:
        return "?"
    if item.get("a_compter"):
        return "à compter"
    stock = item.get("stock")
    return "?" if stock is None else f"{stock:g}"


def shortage(item: Optional[Dict[str, Any]], qty: float) -> float:
    """Units missing to hand out qty from the counted stock.

    0 when the stock covers it, and also when the stock was never counted
    ("à compter"): nobody knows, so the office decides. Example: stock 1,
    request 3, shortage 2.
    """
    if not item or item.get("a_compter") or item.get("stock") is None:
        return 0
    return max(0, qty - max(item["stock"], 0))


def default_choice(item: Optional[Dict[str, Any]], qty: float) -> str:
    """Pre-selected decision on the validation page: hand it out, unless the counted stock is short."""
    return TO_ORDER if shortage(item, qty) else HANDED


def pending_elsewhere(item: Optional[Dict[str, Any]], qty: float) -> float:
    """Units other requests still wait for. "Demandé" counts this request too, hence the subtraction.

    Example: 3 units requested in total, this request asks 1: 2 wait elsewhere.
    """
    if not item:
        return 0
    return max(0, (item.get("pending") or 0) - qty)


# --------------------------------------------------------------------- writes
def create_request(client: NotionClient, demandes_ds: str, registre_ds: str, person: Dict[str, Any],
                   lines: List[Dict[str, Any]], motif: Optional[str], urgent: bool, commentaire: str,
                   today: date, source: str = "Formulaire web", line_status: str = "Demandé",
                   decided: bool = False, decided_by: Optional[str] = None) -> Dict[str, Any]:
    """Create the request page, then its register lines, PARALLEL_CALLS at a time.

    A worker's request waits for the office (request En attente, lines Demandé).
    A request the office has already decided (decided=True, for a replacement)
    is created Validée, its lines directly in line_status (À commander, or
    Prêt à récupérer), with who decided when known.
    All or nothing: if Notion fails on any line, every page already created
    goes to the trash and the error is raised, so the worker can simply send
    again without leaving a half request behind.
    """
    total = sum(line["qty"] for line in lines)
    title = f"{person['name']} · {today:%d/%m} · {total} article{'s' if total > 1 else ''}"
    urgence = "Urgent" if urgent else "Normal"
    request_props: Dict[str, Any] = {
        "Demande": _title(title),
        "Demandeur": _relation([person["id"]]),
        "Bénéficiaire": _people([person.get("user_id")]),
        "Statut": _select(VALIDATED if decided else WAITING),
        "Résumé": _text(summarize(lines)),
        "Nb articles": {"number": total},
        "Motif": _select(motif),
        "Urgence": _select(urgence),
        "Commentaire": _text(commentaire),
    }
    if decided:
        request_props.update({"Validée le": _date(today), "Traitée via": _select("Notion"),
                              "Validée par": _people([decided_by])})
    page = client.create_page(demandes_ds, request_props)

    def create_line(line: Dict[str, Any]) -> Callable[[], Dict[str, Any]]:
        return lambda: client.create_page(registre_ds, {
            "Détail": _title(line["title"]),
            "Article": _relation([line["article_id"]]),
            "Quantité": {"number": line["qty"]},
            "Bénéficiaire": _people([person.get("user_id")]),
            "Type": _select("Sortie"),
            "Statut": _select(line_status),
            "Source": _select(source),
            "Motif": _select(motif),
            "Urgence": _select(urgence),
            "Demande": _relation([page["id"]]),
            "Traité par": _people([decided_by]),
        })

    outcomes = in_parallel([create_line(line) for line in lines])
    errors = [error for _, error in outcomes if error is not None]
    if errors:
        created = [page["id"]] + [result["id"] for result, _ in outcomes if result]
        for _, error in in_parallel([lambda pid=pid: client.trash_page(pid) for pid in created]):
            pass  # best effort: the original error matters more
        raise errors[0]
    return {"id": page["id"], "number": _prop(page, "N°"), "url": page.get("url"), "title": title, "total": total}


RETURNED_STATUSES = ("Rendu", "Rendu usé", "Hors d'usage", "Perdu")


def fill_missing_dates(client: NotionClient, registre_ds: str, today: date) -> Dict[str, int]:
    """Date the lines whose status was changed by hand in Notion, as a Notion automation would.

    A "Remis" line without "Remis le" gets today: the wear clock starts from
    that date, so an empty one would never turn orange. A returned, worn-out or
    lost line without "Rendu le" gets today. Lines already dated are left alone.
    """
    handed = client.query_all(registre_ds, {"filter": {"and": [
        {"property": "Statut", "select": {"equals": HANDED}},
        {"property": "Remis le", "date": {"is_empty": True}},
    ]}})
    returned = client.query_all(registre_ds, {"filter": {"and": [
        {"or": [{"property": "Statut", "select": {"equals": status}} for status in RETURNED_STATUSES]},
        {"property": "Rendu le", "date": {"is_empty": True}},
    ]}})
    updates = [lambda pid=page["id"]: client.update_page(pid, {"Remis le": _date(today)}) for page in handed]
    updates += [lambda pid=page["id"]: client.update_page(pid, {"Rendu le": _date(today)}) for page in returned]
    errors = [error for _, error in in_parallel(updates) if error is not None]
    if errors:
        raise errors[0]
    return {"remis": len(handed), "rendus": len(returned)}


def lines_to_replace(client: NotionClient, registre_ds: str) -> List[Dict[str, Any]]:
    """Register lines the office set to "À remplacer" (handed-out items only)."""
    pages = client.query_all(registre_ds, {"filter": {"property": "Statut", "select": {"equals": TO_REPLACE}}})
    return [{
        "id": page["id"],
        "title": _prop(page, "Détail") or "",
        "qty": _prop(page, "Quantité") or 1,
        "article_id": (_prop(page, "Article") or [None])[0],
        "user_ids": _prop(page, "Bénéficiaire") or [],
        "request_ids": _prop(page, "Demande") or [],
        "handed_on": _prop(page, "Remis le"),
        "changed_by": (page.get("last_edited_by") or {}).get("id"),   # who clicked "À remplacer", usually
    } for page in pages if (_prop(page, "Type") or "Sortie") == "Sortie"]


def requester_of(client: NotionClient, request_id: str) -> Optional[str]:
    """The "Équipe EPI" page of the person who made a request."""
    return (_prop(client.get_page(request_id), "Demandeur") or [None])[0]


def set_worn_out(client: NotionClient, line_id: str, today: Optional[date]) -> None:
    """Hors d'usage with today's "Rendu le"; today=None puts the line back to "À remplacer"."""
    if today is None:
        client.update_page(line_id, {"Statut": _select(TO_REPLACE), "Rendu le": {"date": None}})
    else:
        client.update_page(line_id, {"Statut": _select(WORN_OUT), "Rendu le": _date(today)})


def store_links(client: NotionClient, request_id: str, urls: Dict[str, str]) -> None:
    """Save the "valider" and "refuser" URLs that Notion shows as the Valider / Refuser columns."""
    client.update_page(request_id, {"Lien valider": {"url": urls["valider"]},
                                    "Lien refuser": {"url": urls["refuser"]}})


def load_request(client: NotionClient, registre_ds: str, demande_id: str,
                 demandes_ds: Optional[str] = None) -> Dict[str, Any]:
    """The request and its register lines. RequestGone for a missing, trashed or foreign page.

    The page and its lines are read at the same time (two calls in parallel).
    """
    (page, page_error), (lines, lines_error) = in_parallel([
        lambda: client.get_page(demande_id),
        lambda: client.query_all(registre_ds, {"filter": {"property": "Demande",
                                                          "relation": {"contains": demande_id}}}),
    ])
    if page_error is not None:
        if isinstance(page_error, NotionError) and page_error.status in (400, 404):
            raise RequestGone(str(page_error), page_error.status)
        raise page_error
    if page.get("in_trash") or page.get("archived"):
        raise RequestGone("the request is in the trash")
    parent = (page.get("parent") or {}).get("data_source_id", "")
    if demandes_ds and parent.replace("-", "") != demandes_ds.replace("-", ""):
        raise RequestGone("page is not a request of Demandes EPI")
    if lines_error is not None:
        raise lines_error
    return {
        "id": page["id"],
        "url": page.get("url"),
        "title": _prop(page, "Demande"),
        "number": _prop(page, "N°"),
        "statut": _prop(page, "Statut"),
        "motif": _prop(page, "Motif"),
        "urgence": _prop(page, "Urgence"),
        "commentaire": _prop(page, "Commentaire") or "",
        "validated_on": _prop(page, "Validée le"),
        "lines": [{
            "id": line["id"],
            "title": _prop(line, "Détail"),
            "qty": _prop(line, "Quantité") or 1,
            "statut": _prop(line, "Statut"),
            "article_id": (_prop(line, "Article") or [None])[0],
        } for line in lines],
    }


def decide_request(client: NotionClient, registre_ds: str, demande_id: str, decision: str,
                   user_id: Optional[str], via: str, today: date,
                   demandes_ds: Optional[str] = None,
                   choices: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Validate or refuse a waiting request. A request already decided is left alone.

    Validée: each pending line takes its choice (line id -> Remis, À commander
    or Refusé; Remis when not given). "Remis" is dated today, so the stock drops.
    Refusée: every pending line becomes "Refusé".
    Lines the office already moved by hand in Notion are not touched.
    A validation where every line ends "Refusé" is recorded as Refusée.
    """
    if decision not in (VALIDATED, REFUSED):
        raise ValueError(decision)
    wanted = {key.replace("-", ""): value for key, value in (choices or {}).items()}
    unknown = sorted(set(wanted.values()) - set(LINE_CHOICES))
    if unknown:
        raise ValueError(f"unknown line decision: {unknown}")
    request = load_request(client, registre_ds, demande_id, demandes_ds)
    if request["statut"] != WAITING:
        return {"changed": False, "statut": request["statut"], "lines": [], "request": request}
    moved, updates = [], []
    for line in request["lines"]:
        if line["statut"] not in PENDING_LINE_STATUSES:
            continue
        target = LINE_REFUSED if decision == REFUSED else wanted.get(line["id"].replace("-", ""), HANDED)
        props: Dict[str, Any] = {"Statut": _select(target)}
        if target == HANDED:
            props["Remis le"] = _date(today)
        if user_id:
            props["Traité par"] = _people([user_id])
        updates.append(lambda line_id=line["id"], props=props: client.update_page(line_id, props))
        moved.append(dict(line, statut=target))
    # The lines first (in parallel), the request last: if Notion fails on a line,
    # the request stays "En attente" and the same link finishes the job later
    # (lines already moved are no longer pending, so they are not redone).
    errors = [error for _, error in in_parallel(updates) if error is not None]
    if errors:
        raise errors[0]
    final = decision
    if decision == VALIDATED and moved and all(line["statut"] == LINE_REFUSED for line in moved):
        final = REFUSED
    props = {"Statut": _select(final), "Validée le": _date(today), "Traitée via": _select(via)}
    if user_id:
        props["Validée par"] = _people([user_id])
    client.update_page(demande_id, props)
    return {"changed": True, "statut": final, "lines": moved, "request": request}
