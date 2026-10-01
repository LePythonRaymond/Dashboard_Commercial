"""
Office form ("formulaire bureau"): what neither Notion nor the workers' form covers.

One page at /b/<EPI_OFFICE_KEY>, six screens:
- Livraison: "Entrée stock" lines, several articles and sizes at once. The
  waiting "À commander" lines of those articles become "Prêt à récupérer",
  oldest first, as far as the delivered quantity goes.
- Remise: hand over (Remis, dated today) or order (À commander) for someone,
  new or used articles, without a request to validate.
- État des lieux: what a person already had before the system, with a date.
  The stock does not move: the register formula "Effet stock" counts a
  Sortie line of Source "État des lieux" as 0 while held (it had left the
  shelf before the count) and +quantity once "Rendu".
- Comptage: the counted number is typed; the difference with the stock is
  written as an "Ajustement inventaire" line and "À compter" is unticked.
- Perte au dépôt: "Perte / casse" lines.
- Retour ou départ: what the person holds, each piece kept (nothing changes),
  Rendu, Rendu usé (a "Retour" line puts it in the used stock), Hors d'usage
  or Perdu; pieces of one line may end differently. For a departure the
  person also goes "Parti".
Every line written here is "Source Bureau" (État des lieux for that screen)
and "Traité par" the person who entered it.

Example (Livraison): 4 polaires M arrive while Alex waits for 1 and Zoé for 1:
one "Entrée stock" line of 4 (stock +4), and both waiting lines become
"Prêt à récupérer".
"""

import threading
from collections import OrderedDict
from datetime import date
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional

import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from . import notion
from .notion import _date, _people, _relation, _select, _title, in_parallel

OFFICE_HTML = Path(__file__).with_name("static").joinpath("office.html")
BUREAU, START = "Bureau", "État des lieux"
DELIVERY, RETURN, LOSS, ADJUST, OUT = "Entrée stock", "Retour", "Perte / casse", "Ajustement inventaire", "Sortie"
OUTCOMES = ("Rendu", "Rendu usé", "Hors d'usage", "Perdu")   # in this order: the first one given keeps the line
LEFT = "Parti"
REMEMBERED_SUBMISSIONS = 500   # a double click or a retry with the same id is answered without writing twice


# ------------------------------------------------------------------ payloads
class Qty(BaseModel):
    article_id: str = Field(min_length=32, max_length=36)
    qty: int = Field(ge=1, le=500)


class HandoverItem(Qty):
    statut: Literal["Remis", "À commander"] = "Remis"


class Count(BaseModel):
    article_id: str = Field(min_length=32, max_length=36)
    counted: int = Field(ge=0, le=100000)


class Part(BaseModel):
    outcome: Literal["Rendu", "Rendu usé", "Hors d'usage", "Perdu"]
    qty: int = Field(ge=1, le=500)


class LineReturn(BaseModel):
    line_id: str = Field(min_length=32, max_length=36)
    line_qty: float = Field(gt=0, le=100000)   # the quantity the page showed: a line changed since is left alone
    parts: List[Part] = Field(min_length=1, max_length=8)


class Base(BaseModel):
    submission_id: str = Field(min_length=8, max_length=64)
    by: str = Field(min_length=3, max_length=200)          # office e-mail of whoever enters it


class DeliveryIn(Base):
    items: List[Qty] = Field(min_length=1, max_length=120)
    ready_orders: bool = True


class HandoverIn(Base):
    person_id: str = Field(min_length=32, max_length=36)
    items: List[HandoverItem] = Field(min_length=1, max_length=60)
    motif: Optional[str] = None


class StartIn(Base):
    person_id: str = Field(min_length=32, max_length=36)
    items: List[Qty] = Field(min_length=1, max_length=80)
    handed_on: date


class InventoryIn(Base):
    counts: List[Count] = Field(min_length=1, max_length=300)


class LossIn(Base):
    items: List[Qty] = Field(min_length=1, max_length=60)
    comment: str = Field(default="", max_length=200)


class ReturnIn(Base):
    person_id: str = Field(min_length=32, max_length=36)
    returns: List[LineReturn] = Field(default_factory=list, max_length=150)
    set_left: bool = False                     # a departure: the person also goes "Parti"


class Refused(Exception):
    """Input the office form rejects, with the message shown on the page."""


# ------------------------------------------------------------------ notion side
def _norm(page_id: Optional[str]) -> str:
    return (page_id or "").replace("-", "").lower()


def line(title: str, article_id: str, qty: float, kind: str, by: Optional[str], statut: Optional[str] = None,
         source: str = BUREAU, user_id: Optional[str] = None, handed_on: Optional[date] = None,
         motif: Optional[str] = None) -> Dict[str, Any]:
    """Properties of one register line written by the office."""
    props: Dict[str, Any] = {
        "Détail": _title(title),
        "Article": _relation([article_id]),
        "Quantité": {"number": qty},
        "Type": _select(kind),
        "Source": _select(source),
        "Traité par": _people([by]),
    }
    if statut:
        props["Statut"] = _select(statut)
    if user_id:
        props["Bénéficiaire"] = _people([user_id])
    if handed_on:
        props["Remis le"] = _date(handed_on)
    if motif:
        props["Motif"] = _select(motif)
    return props


def create_lines(client: notion.NotionClient, registre_ds: str, lines: List[Dict[str, Any]]) -> List[str]:
    """All or nothing, notion.PARALLEL_CALLS at a time: on any failure the created lines go to the trash."""
    outcomes = in_parallel([lambda props=props: client.create_page(registre_ds, props) for props in lines])
    errors = [error for _, error in outcomes if error is not None]
    if errors:
        in_parallel([lambda pid=result["id"]: client.trash_page(pid) for result, _ in outcomes if result])
        raise errors[0]
    return [result["id"] for result, _ in outcomes]


def _updates(client: notion.NotionClient, changes: List[Any]) -> None:
    """Apply (page_id, properties) pairs in parallel; raise the first error once all are done."""
    errors = [error for _, error in in_parallel([lambda pid=pid, props=props: client.update_page(pid, props)
                                                  for pid, props in changes]) if error is not None]
    if errors:
        raise errors[0]


def _first(page: Dict[str, Any], name: str) -> Optional[str]:
    return (notion.plain((page.get("properties") or {}).get(name)) or [None])[0]


def _number(page: Dict[str, Any], name: str, default: float = 1) -> float:
    value = notion.plain((page.get("properties") or {}).get(name))
    return default if value is None else value


def waiting_orders(client: notion.NotionClient, registre_ds: str) -> List[Dict[str, Any]]:
    """Lines "À commander", oldest first: who waits for which article."""
    pages = client.query_all(registre_ds, {
        "filter": {"property": "Statut", "select": {"equals": notion.TO_ORDER}},
        "sorts": [{"timestamp": "created_time", "direction": "ascending"}],
    })
    return [{"id": page["id"], "article_id": _first(page, "Article"), "qty": _number(page, "Quantité"),
             "user_ids": notion.plain(page["properties"].get("Bénéficiaire")) or []}
            for page in pages if (notion.plain(page["properties"].get("Type")) or OUT) == OUT]


def held_items(client: notion.NotionClient, registre_ds: str, user_id: str) -> List[Dict[str, Any]]:
    """What a person holds now (lines Remis with that Notion account), with what a split line copies."""
    pages = client.query_all(registre_ds, {"filter": {"and": [
        {"property": "Statut", "select": {"equals": notion.HANDED}},
        {"property": "Bénéficiaire", "people": {"contains": user_id}},
    ]}})

    def value(page: Dict[str, Any], name: str) -> Any:
        return notion.plain(page["properties"].get(name))

    return [{"id": page["id"], "title": value(page, "Détail") or "", "article_id": _first(page, "Article"),
             "qty": _number(page, "Quantité"), "handed_on": value(page, "Remis le"),
             "user_ids": value(page, "Bénéficiaire") or [], "request_ids": value(page, "Demande") or [],
             "source": value(page, "Source"), "motif": value(page, "Motif"), "urgence": value(page, "Urgence")}
            for page in pages if (value(page, "Type") or OUT) == OUT]


def used_equivalent(article: Dict[str, Any], articles: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The "usé" article matching a new one: same title with "usé" before the size, else the
    only used article of the same family and size. Example: "T-shirt noir · M" -> "T-shirt noir usé · M"."""
    used = [a for a in articles if a.get("etat") == "Usé"]
    title = article.get("title") or ""
    if " · " in title:
        model, size = title.rsplit(" · ", 1)
        named = f"{model} usé · {size}".casefold()
        match = next((a for a in used if a["title"].casefold() == named), None)
        if match:
            return match
    same = [a for a in used if a["famille"] == article.get("famille") and a["taille"] == article.get("taille")]
    return same[0] if len(same) == 1 else None


# ------------------------------------------------------------------ the screens
def record_delivery(client, registre_ds, items, by, ready_orders):
    """items: [(article, qty)]. Entrée stock lines, then waiting orders made ready, oldest first.

    Returns (orders made ready, error). Once the delivery lines exist, a failure on
    the orders is reported, not raised: raising would invite a retry that adds the
    delivery a second time.
    """
    create_lines(client, registre_ds, [line(f"Livraison · {a['title']}", a["id"], qty, DELIVERY, by)
                                       for a, qty in items])
    ready: List[Dict[str, Any]] = []
    if not ready_orders:
        return ready, None
    try:
        left = {_norm(a["id"]): qty for a, qty in items}
        for order in waiting_orders(client, registre_ds):
            key = _norm(order["article_id"])
            if key in left and order["qty"] <= left[key]:   # the oldest that fits; a bigger one waits
                left[key] -= order["qty"]
                ready.append(order)
        _updates(client, [(order["id"], {"Statut": _select(notion.READY)}) for order in ready])
    except (notion.NotionError, requests.RequestException) as exc:
        return [], exc
    return ready, None


def for_whom(article: Dict[str, Any], person: Dict[str, Any]) -> str:
    """Line title. Without a Notion account the "Bénéficiaire" stays empty, so the name goes in the title:
    "T-shirt noir · M · pour Vincent DA SILVA" instead of a line nobody can trace back."""
    return article["title"] if person.get("user_id") else f"{article['title']} · pour {person['name']}"


def record_handover(client, registre_ds, person, items, by, today, motif):
    """items: [(article, qty, statut)] with statut Remis (dated today) or À commander."""
    create_lines(client, registre_ds, [
        line(for_whom(a, person), a["id"], qty, OUT, by, statut=statut, user_id=person.get("user_id"),
             handed_on=today if statut == notion.HANDED else None, motif=motif)
        for a, qty, statut in items])


def record_start(client, registre_ds, person, items, by, handed_on):
    """État des lieux: items the person already had, handed out on an approximate date."""
    create_lines(client, registre_ds, [
        line(for_whom(a, person), a["id"], qty, OUT, by, statut=notion.HANDED, source=START,
             user_id=person.get("user_id"), handed_on=handed_on)
        for a, qty in items])


def record_inventory(client, articles_ds, registre_ds, counts, by):
    """counts: [(article read just now, counted)]. Writes the differences, unticks "À compter".

    Example: Notion says 9, 7 are counted: an "Ajustement inventaire" line of -2.
    """
    adjustments = [(a, counted - (a.get("stock") or 0)) for a, counted in counts]
    create_lines(client, registre_ds, [line(f"Comptage · {a['title']} : {counted:g}", a["id"], diff, ADJUST, by)
                                       for (a, diff), (_, counted) in zip(adjustments, counts) if diff])
    _updates(client, [(a["id"], {"À compter": {"checkbox": False}}) for a, _ in counts if a.get("a_compter")])
    return [(a, diff) for a, diff in adjustments if diff]


def record_loss(client, registre_ds, items, by, comment):
    suffix = f" ({comment})" if comment else ""
    create_lines(client, registre_ds, [line(f"Perte au dépôt · {a['title']}{suffix}", a["id"], qty, LOSS, by)
                                       for a, qty in items])


def part_line(item: Dict[str, Any], outcome: str, qty: float, by: Optional[str], today: date) -> Dict[str, Any]:
    """A new line for pieces of a held line that end differently: same article, holder, request,
    source, motif and handover date as the line, with their own outcome."""
    props: Dict[str, Any] = {
        "Détail": _title(item["title"]), "Article": _relation([item["article_id"]]),
        "Quantité": {"number": qty}, "Type": _select(OUT), "Statut": _select(outcome),
        "Bénéficiaire": _people(item.get("user_ids") or []), "Traité par": _people([by]), "Rendu le": _date(today),
    }
    if item.get("handed_on"):
        props["Remis le"] = {"date": {"start": item["handed_on"]}}
    if item.get("request_ids"):
        props["Demande"] = _relation(item["request_ids"])
    for name, key in (("Source", "source"), ("Motif", "motif"), ("Urgence", "urgence")):
        if item.get(key):
            props[name] = _select(item[key])
    return props


def plan_return(item: Dict[str, Any], parts: Dict[str, int], articles: List[Dict[str, Any]], by: Optional[str],
                today: date) -> Any:
    """What one held line becomes. parts: {outcome: pieces}, together at most the line's quantity.

    Returns (change of the line, new lines, the line title when no used article matches).
    The pieces not given back stay on the line, still "Remis". Example, a line of
    3 polaires: 2 "Rendu usé" and 1 "Hors d'usage" -> the line becomes 2 "Rendu usé",
    a new line holds the 1 "Hors d'usage", and a "Retour" line puts 2 in the used stock.
    With 1 kept instead: the line stays "Remis" with 1 and new lines hold the rest.
    """
    given = [(outcome, parts[outcome]) for outcome in OUTCOMES if parts.get(outcome)]
    kept = item["qty"] - sum(qty for _, qty in given)
    if kept > 0:
        change, split = {"Quantité": {"number": kept}}, given
    else:
        (outcome, qty), split = given[0], given[1:]
        change = {"Statut": _select(outcome), "Rendu le": _date(today), "Traité par": _people([by])}
        if split:
            change["Quantité"] = {"number": qty}
    new_lines = [part_line(item, outcome, qty, by, today) for outcome, qty in split]
    worn, missing_used = sum(qty for outcome, qty in given if outcome == "Rendu usé"), None
    if worn:
        article = next((a for a in articles if _norm(a["id"]) == _norm(item["article_id"])), None)
        used = used_equivalent(article, articles) if article else None
        if used:
            new_lines.append(line(f"Retour au stock usé · {used['title']}", used["id"], worn, RETURN, by))
        else:
            missing_used = item["title"]
    return change, new_lines, missing_used


def settle_line(client: notion.NotionClient, registre_ds: str, line_id: str, change: Dict[str, Any],
                new_lines: List[Dict[str, Any]]) -> None:
    """Write one held line's outcome: the new lines first, then the line itself.

    If the line cannot be changed, the new lines go to the trash: the line is then
    untouched and a retry starts clean. The other way round, a failure after the
    change would lose the "Retour" line, since a retry no longer sees the line as held.
    """
    created: List[str] = []
    try:
        for props in new_lines:           # one by one: the lines themselves run 3 at a time
            created.append(client.create_page(registre_ds, props)["id"])
        client.update_page(line_id, change)
    except Exception:
        for page_id in created:
            try:
                client.trash_page(page_id)
            except Exception:             # best effort: the original error matters more
                pass
        raise


def record_return(client: notion.NotionClient, registre_ds: str, person: Dict[str, Any], plans: List[Any],
                  set_left: bool) -> None:
    """plans: [(line id, change, new lines)]. The person goes "Parti" only once every line went
    through, so a retry after a failure finishes the job (lines already done are skipped)."""
    errors = [error for _, error in in_parallel([lambda plan=plan: settle_line(client, registre_ds, *plan)
                                                  for plan in plans]) if error is not None]
    if errors:
        raise errors[0]
    if set_left:
        client.update_page(person["id"], {"Statut": _select(LEFT)})


# ------------------------------------------------------------------ routes
def register(app: FastAPI, ctx: Any) -> None:
    """Add the office routes. ctx gives cfg, api, team(), articles(fresh), office(), users_by_email(),
    now(), refresh() (caches to reload after a write) and same_key()."""
    cfg, api = ctx.cfg, ctx.api
    done: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
    lock = threading.Lock()

    def require_key(key: str) -> None:
        if len(cfg.office_key) < 16 or not ctx.same_key(cfg.office_key, key):
            raise HTTPException(status_code=404)

    def author(email: str) -> Optional[str]:
        """The Notion account of whoever enters it; only the office people may (EPI_OFFICE_EMAILS,
        else the people who receive the requests)."""
        email = email.strip().lower()
        if email not in (cfg.office_emails or cfg.notify_emails):
            raise Refused("Choisis qui fait la saisie.")
        return (ctx.users_by_email().get(email) or {}).get("id")

    def person_of(person_id: str) -> Dict[str, Any]:
        person = next((p for p in ctx.team() if _norm(p["id"]) == _norm(person_id)), None)
        if person is None:
            raise Refused("Personne inconnue. Recharge la page.")
        return person

    def article_map(fresh: bool = False) -> Dict[str, Dict[str, Any]]:
        return {_norm(a["id"]): a for a in ctx.articles(fresh)}

    def pick(articles: Dict[str, Dict[str, Any]], article_id: str) -> Dict[str, Any]:
        article = articles.get(_norm(article_id))
        if article is None:
            raise Refused("Un article n'existe plus. Recharge la page.")
        return article

    def no_account(person: Dict[str, Any]) -> List[str]:
        if person.get("user_id"):
            return []
        return [f"{person['name']} n'a pas de compte Notion : ces articles n'apparaîtront sur aucune carte."]

    # One function per screen: (payload) -> (message, details). Blocking: run in a thread.
    def delivery(p: DeliveryIn):
        by, articles = author(p.by), article_map()
        items = [(pick(articles, i.article_id), i.qty) for i in p.items]
        ready, error = record_delivery(api, cfg.registre_ds, items, by, p.ready_orders)
        people = {person.get("user_id"): person["name"] for person in ctx.team() if person.get("user_id")}
        names = {_norm(a["id"]): a["title"] for a, _ in items}
        details = [f"{a['title']} : +{qty}" for a, qty in items]
        details += [f"Prêt à récupérer pour {people.get((o['user_ids'] or [None])[0], 'quelqu’un')} : "
                    f"{names.get(_norm(o['article_id']), 'article')} × {o['qty']:g}" for o in ready]
        if error is not None:
            print(f"[epi-form] delivery recorded, waiting orders not updated: {error}")
            details.append("Notion n'a pas répondu pour les commandes en attente : passe-les en « Prêt à "
                           "récupérer » dans « Demandes à traiter ». La livraison, elle, est bien enregistrée.")
        total = sum(qty for _, qty in items)
        return f"Livraison enregistrée : {total:g} pièce{'s' if total > 1 else ''}.", details

    def handover(p: HandoverIn):
        by, person, articles = author(p.by), person_of(p.person_id), article_map()
        items = [(pick(articles, i.article_id), i.qty, i.statut) for i in p.items]
        for a, _, statut in items:
            if statut == notion.TO_ORDER and a.get("etat") == "Usé":
                raise Refused(f"{a['title']} : un article usé ne se commande pas. Commande le neuf.")
        motif = p.motif if p.motif in notion.MOTIFS else None
        record_handover(api, cfg.registre_ds, person, items, by, ctx.now().date(), motif)
        details = [f"{a['title']} × {qty} : {statut}" for a, qty, statut in items] + no_account(person)
        return f"Enregistré pour {person['name']}.", details

    def start(p: StartIn):
        by, person, articles = author(p.by), person_of(p.person_id), article_map()
        if p.handed_on > ctx.now().date():
            raise Refused("La date de remise ne peut pas être dans le futur.")
        items = [(pick(articles, i.article_id), i.qty) for i in p.items]
        record_start(api, cfg.registre_ds, person, items, by, p.handed_on)
        details = [f"{a['title']} × {qty}" for a, qty in items] + no_account(person)
        return f"État des lieux enregistré : {person['name']} (remis le {p.handed_on:%d/%m/%Y}).", details

    def inventory(p: InventoryIn):
        by, articles = author(p.by), article_map(fresh=True)   # the difference needs the stock of right now
        counts = [(pick(articles, c.article_id), c.counted) for c in p.counts]
        changed = record_inventory(api, cfg.articles_ds, cfg.registre_ds, counts, by)
        details = [f"{a['title']} : {diff:+g}" for a, diff in changed]
        same = len(counts) - len(changed)
        if same:
            details.append(f"{same} article{'s' if same > 1 else ''} déjà juste{'s' if same > 1 else ''}.")
        return f"Comptage enregistré : {len(counts)} article{'s' if len(counts) > 1 else ''}.", details

    def loss(p: LossIn):
        by, articles = author(p.by), article_map()
        items = [(pick(articles, i.article_id), i.qty) for i in p.items]
        record_loss(api, cfg.registre_ds, items, by, p.comment.strip())
        return "Perte enregistrée.", [f"{a['title']} : −{qty}" for a, qty in items]

    def give_back(p: ReturnIn):
        by, person, today = author(p.by), person_of(p.person_id), ctx.now().date()
        if len({_norm(r.line_id) for r in p.returns}) != len(p.returns):
            raise Refused("Une ligne apparaît deux fois. Recharge la page.")
        held = {_norm(item["id"]): item for item in (held_items(api, cfg.registre_ds, person["user_id"])
                                                      if person.get("user_id") else [])}
        articles = ctx.articles(False)
        plans: List[Any] = []
        done_text: List[str] = []
        notes: List[str] = []
        outcomes = set()
        gone = 0
        for r in p.returns:                # everything is checked before anything is written
            item = held.get(_norm(r.line_id))
            if item is None:               # already handled: a retry after a failure, or a change in Notion
                gone += 1
                continue
            if item["qty"] != r.line_qty:  # changed in Notion since the page opened (or half done by a retry)
                notes.append(f"{item['title']} : la quantité a changé entre-temps, ligne laissée telle quelle. "
                             "Recharge la page pour la traiter.")
                continue
            parts: Dict[str, int] = {}
            for part in r.parts:
                parts[part.outcome] = parts.get(part.outcome, 0) + part.qty
            kept = item["qty"] - sum(parts.values())
            if kept < 0:
                raise Refused(f"{item['title']} : plus de pièces que la personne n'en a.")
            change, new_lines, no_used = plan_return(item, parts, articles, by, today)
            plans.append((item["id"], change, new_lines))
            outcomes.update(parts)
            done_text.append(f"{item['title']} : " + ", ".join(f"{o} × {parts[o]}" for o in OUTCOMES if parts.get(o))
                             + (f" ({kept:g} encore chez la personne)" if kept > 0 else ""))
            if no_used:
                notes.append(f"{no_used} : pas d'article « usé » équivalent, le stock usé n'a pas bougé. "
                             "Crée-le dans Articles EPI pour le suivre.")
        if not plans and not p.set_left and not gone and not notes:
            raise Refused("Choisis au moins un article rendu, ou coche le départ.")
        record_return(api, cfg.registre_ds, person, plans, p.set_left)
        if gone:
            notes.append(f"{gone} ligne{'s' if gone > 1 else ''} n'étai{'en' if gone > 1 else ''}t plus chez la "
                         f"personne (déjà traitée{'s' if gone > 1 else ''}) : rien de plus n'a été fait.")
        if outcomes & {"Hors d'usage", "Perdu"} and not p.set_left:
            notes.append("Pour lui en donner un neuf : « Remettre ou commander », motif Perte ou Casse.")
        if p.set_left:
            notes.append(f"{person['name']} : statut « Parti », son nom disparaît des formulaires.")
            return f"Départ enregistré : {person['name']}.", done_text + notes
        if plans:
            return f"Retour enregistré : {person['name']}.", done_text + notes
        return "Rien de plus à enregistrer.", notes

    screens: Dict[str, Any] = {"livraison": (DeliveryIn, delivery), "remise": (HandoverIn, handover),
                               "etat-des-lieux": (StartIn, start), "comptage": (InventoryIn, inventory),
                               "perte": (LossIn, loss), "retour": (ReturnIn, give_back)}

    @app.get("/b/{key}", response_class=HTMLResponse)
    def office_page(key: str):
        require_key(key)
        return HTMLResponse(OFFICE_HTML.read_text(encoding="utf-8"), headers={"Cache-Control": "no-store"})

    @app.get("/b/{key}/data")
    def office_data(key: str):
        require_key(key)
        waiting: Dict[str, float] = {}
        try:
            for order in waiting_orders(api, cfg.registre_ds):
                waiting[_norm(order["article_id"])] = waiting.get(_norm(order["article_id"]), 0) + order["qty"]
        except (notion.NotionError, requests.RequestException) as exc:
            print(f"[epi-form] waiting orders unavailable: {exc}")
        today = ctx.now().date()
        return JSONResponse({
            "team": [{"id": p["id"], "name": p["name"], "account": bool(p.get("user_id"))} for p in ctx.team()],
            "articles": [{k: a[k] for k in ("id", "title", "famille", "taille", "mode", "etat", "a_compter")}
                         | {"stock": None if a["a_compter"] else a["stock"], "stock_raw": a["stock"] or 0,
                            "waiting": waiting.get(_norm(a["id"]), 0)}
                         for a in ctx.articles(False)],
            "office": [{"email": email, "name": name} for email, name in ctx.office()],
            "motifs": notion.MOTIFS,
            "today": today.isoformat(),
            "first_of_month": today.replace(day=1).isoformat(),
        }, headers={"Cache-Control": "no-store"})

    @app.get("/b/{key}/held/{person_id}")
    def office_held(key: str, person_id: str):
        require_key(key)
        try:
            person = person_of(person_id)
        except Refused as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        if not person.get("user_id"):
            return {"ok": True, "items": [], "warning": no_account(person)[0]}
        catalogue = ctx.articles(False)
        articles = {_norm(a["id"]): a for a in catalogue}
        items = held_items(api, cfg.registre_ds, person["user_id"])

        def used_title(item: Dict[str, Any]) -> Optional[str]:   # where a "Rendu usé" piece would go
            article = articles.get(_norm(item["article_id"]))
            return (used_equivalent(article, catalogue) or {}).get("title") if article else None

        return {"ok": True, "items": [{
            "line_id": item["id"], "title": item["title"], "qty": item["qty"], "handed_on": item["handed_on"],
            "mode": (articles.get(_norm(item["article_id"])) or {}).get("mode", "Dotation"), "used": used_title(item),
        } for item in items]}

    @app.post("/b/{key}/{screen}")
    async def office_submit(key: str, screen: str, request: Request):
        require_key(key)
        if screen not in screens:
            raise HTTPException(status_code=404)
        model, handler = screens[screen]
        try:
            payload = model.model_validate(await request.json())
        except (ValidationError, ValueError):
            return JSONResponse({"ok": False, "error": "Saisie incomplète."}, status_code=400)
        with lock:
            if payload.submission_id in done:          # double click or retry: answered, not written twice
                return done[payload.submission_id]
        try:
            message, details = await run_in_threadpool(handler, payload)
        except Refused as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        except (notion.NotionError, requests.RequestException) as exc:
            print(f"[epi-form] office {screen} failed: {exc}")
            return JSONResponse({"ok": False, "error": "Notion ne répond pas. Réessaie dans une minute : "
                                                       "ce qui a été enregistré ne sera pas refait."}, status_code=502)
        result = {"ok": True, "message": message, "details": details}
        with lock:
            done[payload.submission_id] = result
            while len(done) > REMEMBERED_SUBMISSIONS:
                done.popitem(last=False)
        ctx.refresh()
        print(f"[epi-form] office {screen} by {payload.by}: {message}")
        return result
