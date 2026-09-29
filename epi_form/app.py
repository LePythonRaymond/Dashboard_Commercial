"""
Web service of the EPI form (FastAPI).

Routes:
- GET  /f/<key>            the form page (the key keeps the page private)
- GET  /f/<key>/data       team (names and sizes) and catalogue, for the page
- POST /f/<key>/demande    creates the request in Notion, e-mails the office
- GET  /v/<id>/<action>/<who>/<signature>   confirmation page, one decision per article
- POST /v/<id>/<action>/<who>/<signature>   applies the decision in Notion
- GET  /health

<who> is the office e-mail a link was sent to, or "notion" for the links shown
in the Valider / Refuser columns of "Demandes EPI" (the page then asks who is
deciding). The signature covers id, action and <who>: see security.py.

Run locally:  uvicorn epi_form.app:app --port 8765   (settings: see config.py)
"""

import threading
import time
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

import requests
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import mailer, notion
from .config import Config
from .security import check, decode_recipient, encode_recipient, same_key, sign

PARIS = ZoneInfo("Europe/Paris")
FORM_HTML = Path(__file__).with_name("static").joinpath("form.html")
CACHE_SECONDS = 60
USERS_CACHE_SECONDS = 3600
# At most 60 requests every 10 minutes per client address. On the VPS every
# client reaches the service through Docker's gateway (the logs show one
# address for everybody), so in practice this is one shared safety valve
# against a runaway script, sized well above a busy Monday for the team.
RATE_LIMIT = (60, 600)
ACTIONS = {"valider": notion.VALIDATED, "refuser": notion.REFUSED}
NOTION_RECIPIENT = "notion"     # <who> of the links stored in Notion
SECURITY_HEADERS = {
    "Referrer-Policy": "no-referrer",          # the URLs carry the form key and signatures
    "X-Robots-Tag": "noindex, nofollow",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}
# Confirmation page: remember who decides (Notion links) and block double clicks.
CONFIRM_SCRIPT = """<script>
(function () {
  var form = document.querySelector("form");
  if (form) form.addEventListener("submit", function () {
    var button = form.querySelector("button");
    if (button) { button.disabled = true; button.textContent = "Un instant..."; }
  });
  var par = document.getElementById("par");
  if (!par) return;
  var saved = null;
  try { saved = localStorage.getItem("epi-office"); } catch (e) {}
  for (var i = 0; i < par.options.length; i++) if (saved && par.options[i].value === saved) par.value = saved;
  par.addEventListener("change", function () { try { localStorage.setItem("epi-office", par.value); } catch (e) {} });
})();
</script>"""


class Item(BaseModel):
    article_id: str = Field(min_length=32, max_length=36)
    qty: int = Field(ge=1, le=50)


class DemandeIn(BaseModel):
    person_id: str = Field(min_length=32, max_length=36)
    items: List[Item] = Field(min_length=1, max_length=40)
    motif: Optional[str] = None
    urgent: bool = False
    commentaire: str = Field(default="", max_length=1000)


class Refused(Exception):
    """A request the form rejects, with the message shown to the worker."""


def _norm(page_id: Optional[str]) -> str:
    return (page_id or "").replace("-", "").lower()


class _Cache:
    """Tiny time-based cache: the form reloads the team and catalogue at most once a minute."""

    def __init__(self):
        self._values: Dict[str, Any] = {}
        self._lock = threading.Lock()

    def get(self, name: str, seconds: int, load: Callable[[], Any]) -> Any:
        with self._lock:
            stamp, value = self._values.get(name, (0.0, None))
            if value is not None and time.monotonic() - stamp < seconds:
                return value
        value = load()
        with self._lock:
            self._values[name] = (time.monotonic(), value)
        return value

    def clear(self, name: str) -> None:
        with self._lock:
            self._values.pop(name, None)


class SecurityHeaders:
    """ASGI middleware that adds SECURITY_HEADERS to every response."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    headers.setdefault(name, value)
            await send(message)

        await self.app(scope, receive, send_with_headers)


def _page(title: str, body: str, status: int = 200, script: str = "") -> HTMLResponse:
    html = f"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{escape(title)}</title>
<style>body{{font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;max-width:680px;margin:0 auto;
padding:24px 16px;color:#1f1f1f;background:#fafaf8}}h1{{font-size:22px;font-weight:600}}
table{{border-collapse:collapse;width:100%;margin:12px 0}}td,th{{padding:8px;border-bottom:1px solid #e5e5e5;text-align:left}}
select{{font:inherit;font-size:15px;padding:6px;border-radius:6px;border:1px solid #ccc;background:#fff;color:#1f1f1f;max-width:100%}}
td.decision{{width:9.5em}}td.decision select{{width:100%}}
button{{font-size:16px;padding:12px 20px;border-radius:8px;border:0;background:#2f6b4f;color:#fff;cursor:pointer}}
button.secondary{{background:#e9e9e6;color:#1f1f1f}}button:disabled{{opacity:.5}}.muted{{color:#666;font-size:14px}}
.warn,.error{{color:#b42318}}.error{{font-weight:600}}
@media (prefers-color-scheme: dark){{body{{background:#161616;color:#eee}}td,th{{border-color:#333}}
.muted{{color:#aaa}}button.secondary{{background:#333;color:#eee}}select{{background:#222;color:#eee;border-color:#444}}
.warn,.error{{color:#f97066}}a{{color:#8ab4f8}}}}</style></head>
<body><h1>{escape(title)}</h1>{body}{script}</body></html>"""
    return HTMLResponse(html, status_code=status, headers={"Cache-Control": "no-store"})


def create_app(config: Optional[Config] = None, client: Optional[notion.NotionClient] = None,
               send: Optional[Callable[[str, Dict[str, str]], None]] = None,
               now: Callable[[], datetime] = lambda: datetime.now(PARIS)) -> FastAPI:
    cfg = config or Config.from_env()
    api = client or notion.NotionClient(cfg.notion_token)
    cache = _Cache()
    hits: Dict[str, List[float]] = {}
    decide_lock = threading.Lock()   # one decision at a time: two quick clicks cannot both apply
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(SecurityHeaders)
    for problem in cfg.problems():
        print(f"[epi-form] configuration: {problem}")

    # ------------------------------------------------------------- helpers
    def deliver(to: str, message: Dict[str, str]) -> None:
        try:
            if send:
                send(to, message)
            else:
                mailer.send_mail(cfg.smtp_host, cfg.smtp_port, cfg.smtp_user, cfg.smtp_password, to, message,
                                 dry_run_dir=cfg.mail_dry_run_dir)
            print(f"[epi-form] e-mail sent to {to}: {message['subject']}")
        except Exception as exc:  # the request exists in Notion even if the e-mail fails
            print(f"[epi-form] e-mail to {to} failed: {exc}")

    def team() -> List[Dict[str, Any]]:
        return cache.get("team", CACHE_SECONDS, lambda: notion.load_team(api, cfg.equipe_ds))

    def catalogue(fresh: bool = False) -> List[Dict[str, Any]]:
        if fresh:
            cache.clear("catalogue")
        return cache.get("catalogue", CACHE_SECONDS, lambda: notion.load_catalogue(api, cfg.articles_ds))

    def stock_now() -> Dict[str, Dict[str, Any]]:
        """Fresh catalogue by normalised id; empty (stock shown as "?") if Notion does not answer."""
        try:
            return {_norm(item["id"]): item for item in catalogue(fresh=True)}
        except (notion.NotionError, requests.RequestException) as exc:
            print(f"[epi-form] stock unavailable: {exc}")
            return {}

    def users_by_email() -> Dict[str, Dict[str, Any]]:
        try:
            users = cache.get("users", USERS_CACHE_SECONDS, api.users)
        except (notion.NotionError, requests.RequestException) as exc:
            print(f"[epi-form] Notion users unavailable: {exc}")
            return {}
        found = {}
        for user in users:
            email = ((user.get("person") or {}).get("email") or "").lower()
            if email:
                found[email] = user
        return found

    def office() -> List[Tuple[str, str]]:
        """(e-mail, name) of the people who receive and decide the requests."""
        known = users_by_email()
        return [(email, (known.get(email) or {}).get("name") or email) for email in cfg.notify_emails]

    def require_form_key(key: str) -> None:
        if not same_key(cfg.form_key, key) or len(cfg.form_key) < 16:
            raise HTTPException(status_code=404)

    def allowed(request: Request) -> bool:
        ip = request.client.host if request.client else "?"
        limit, window = RATE_LIMIT
        recent = [t for t in hits.get(ip, []) if time.monotonic() - t < window]
        if len(recent) >= limit:
            hits[ip] = recent
            return False
        hits[ip] = recent + [time.monotonic()]
        return True

    def links_for(request_id: str, recipient: str) -> Dict[str, str]:
        who = encode_recipient(recipient)
        return {action: f"{cfg.public_url}/v/{request_id}/{action}/{who}/{sign(cfg.signing_secret, request_id, action, recipient)}"
                for action in ACTIONS}

    def verify_link(request_id: str, action: str, who: str, signature: str) -> str:
        """The recipient of a genuine link (an office e-mail or "notion"), otherwise 403."""
        try:
            recipient = decode_recipient(who)
        except Exception:
            raise HTTPException(status_code=403)
        known = recipient == NOTION_RECIPIENT or recipient in cfg.notify_emails
        if action not in ACTIONS or not known or not check(cfg.signing_secret, signature, request_id, action, recipient):
            raise HTTPException(status_code=403)
        return recipient

    def load_or_404(request_id: str) -> Dict[str, Any]:
        try:
            return notion.load_request(api, cfg.registre_ds, request_id, cfg.demandes_ds)
        except notion.RequestGone:
            raise HTTPException(status_code=404)
        except (notion.NotionError, requests.RequestException) as exc:
            print(f"[epi-form] Notion error while loading {request_id}: {exc}")
            raise HTTPException(status_code=502)

    # --------------------------------------------------------------- pages
    def summary(req: Dict[str, Any]) -> str:
        bits = []
        if req.get("motif"):
            bits.append(f"Motif : {escape(req['motif'])}")
        if req.get("urgence") == "Urgent":
            bits.append("<span class='warn'>Urgent</span>")
        if req.get("commentaire"):
            bits.append(f"Précisions : {escape(req['commentaire'])}")
        return f"<p>{escape(req.get('title') or '')}</p>" + "".join(f"<p class='muted'>{bit}</p>" for bit in bits)

    def notion_link(req: Dict[str, Any]) -> str:
        return f"<p><a href='{escape(req['url'])}'>Ouvrir dans Notion</a></p>" if req.get("url") else ""

    def status_table(req: Dict[str, Any], after: Optional[Dict[str, str]] = None) -> str:
        after = after or {}
        rows = "".join(
            f"<tr><td>{escape(line['title'] or '')}</td><td>{line['qty']:g}</td>"
            f"<td>{escape(after.get(line['id']) or line['statut'] or 'Demandé')}</td></tr>" for line in req["lines"])
        return f"<table><tr><th>Article</th><th>Qté</th><th>Statut</th></tr>{rows}</table>"

    def already_decided(req: Dict[str, Any]) -> HTMLResponse:
        return _page(f"Demande {req.get('number') or ''} déjà traitée",
                     f"<p>Statut : <b>{escape(req['statut'] or '')}</b>. Rien n'a changé.</p>"
                     f"{status_table(req)}{notion_link(req)}")

    def confirmation(req: Dict[str, Any], action: str, recipient: str, stock: Dict[str, Dict[str, Any]],
                     error: str = "", status: int = 200) -> HTMLResponse:
        pending = {line["id"] for line in req["lines"] if line["statut"] in notion.PENDING_LINE_STATUSES}
        rows = []
        for line in req["lines"]:
            item = stock.get(_norm(line["article_id"]))
            missing = notion.shortage(item, line["qty"])
            in_stock = escape(notion.stock_label(item)) + (
                f" <span class='warn'>(manque {missing:g})</span>" if missing else "")
            title = escape(line["title"] or "")
            if line["id"] not in pending:
                decision = f"<span class='muted'>{escape(line['statut'] or '')}, déjà traité</span>"
            elif action == "valider":
                default = notion.default_choice(item, line["qty"])
                options = "".join(f"<option{' selected' if choice == default else ''}>{escape(choice)}</option>"
                                  for choice in notion.LINE_CHOICES)
                decision = f"<select name='ligne_{_norm(line['id'])}' aria-label='Décision : {title}'>{options}</select>"
            else:
                decision = notion.LINE_REFUSED
            # Two columns only, so the page fits a phone: quantity and stock go under the article.
            rows.append(f"<tr><td>{title}<div class='muted'>Qté {line['qty']:g} · en stock : {in_stock}</div></td>"
                        f"<td class='decision'>{decision}</td></tr>")
        table = "<table><tr><th>Article</th><th>Décision</th></tr>" + "".join(rows) + "</table>"
        verb = "Valider" if action == "valider" else "Refuser"
        who = ""
        if recipient == NOTION_RECIPIENT:
            options = "".join(f"<option value='{escape(email)}'>{escape(name)}</option>" for email, name in office())
            who = (f"<p><label>Qui {'valide' if action == 'valider' else 'refuse'} ? "
                   f"<select name='par' id='par' required><option value=''>Choisis</option>{options}</select></label></p>")
        effect = ("« Remis » : la personne a l'article, le stock baisse. « À commander » : l'article va dans "
                  "la liste de commande, le stock ne bouge pas. « Refusé » : rien ne bouge."
                  if action == "valider" else "Tous les articles passeront en « Refusé ». Le stock ne bouge pas.")
        button = f"<button{' class=secondary' if action == 'refuser' else ''}>{verb} la demande</button>"
        body = (summary(req) + (f"<p class='error'>{escape(error)}</p>" if error else "")
                + f"<form method='post'>{table}{who}<p class='muted'>{effect}</p>{button}</form>" + notion_link(req))
        return _page(f"{verb} la demande {req.get('number') or ''} ?", body, status=status, script=CONFIRM_SCRIPT)

    # -------------------------------------------------------------- routes
    @app.exception_handler(StarletteHTTPException)   # also catches the router's own 404
    async def http_error(_request: Request, exc: StarletteHTTPException):
        messages = {400: "Demande invalide.", 403: "Lien invalide ou expiré.",
                    404: "Page introuvable (la demande a peut-être été supprimée).",
                    429: "Trop de demandes, réessaie plus tard.",
                    502: "Notion ne répond pas. Réessaie dans une minute."}
        return _page("Oups", f"<p>{escape(messages.get(exc.status_code, 'Erreur.'))}</p>", status=exc.status_code)

    @app.get("/health")
    def health():
        return {"ok": not cfg.problems()}

    @app.get("/", response_class=HTMLResponse)
    def root():
        return _page("Page privée", "<p class='muted'>Demande le lien du formulaire au bureau.</p>")

    @app.get("/f/{key}", response_class=HTMLResponse)
    def form(key: str):
        require_form_key(key)
        return HTMLResponse(FORM_HTML.read_text(encoding="utf-8"), headers={"Cache-Control": "no-store"})

    @app.get("/f/{key}/data")
    def form_data(key: str):
        require_form_key(key)
        people = [{"id": p["id"], "name": p["name"], "sizes": p["sizes"]} for p in team()]
        items = [{k: item[k] for k in ("id", "title", "famille", "taille", "couleur", "mode")} for item in catalogue()]
        return JSONResponse({"team": people, "catalogue": items, "motifs": notion.MOTIFS,
                             "size_field_by_family": notion.SIZE_FIELD_BY_FAMILY},
                            headers={"Cache-Control": "no-store"})

    def register(payload: DemandeIn) -> Tuple[Dict[str, Any], List[Tuple[str, Dict[str, str]]]]:
        """Create the request in Notion; return it with the e-mails to send. Blocking: runs in a thread."""
        person = next((p for p in team() if _norm(p["id"]) == _norm(payload.person_id)), None)
        if person is None:
            raise Refused("Nom inconnu. Recharge la page.")
        by_id = {_norm(item["id"]): item for item in catalogue()}
        quantities: Dict[str, int] = {}
        for entry in payload.items:
            key = _norm(entry.article_id)
            if key not in by_id:
                raise Refused("Un article n'est plus disponible. Recharge la page.")
            quantities[key] = quantities.get(key, 0) + entry.qty
        lines = [{"article_id": by_id[k]["id"], "title": by_id[k]["title"], "qty": min(q, 50)}
                 for k, q in quantities.items()]
        motif = payload.motif if payload.motif in notion.MOTIFS else None
        commentaire = payload.commentaire.strip()
        when = now()
        created = notion.create_request(api, cfg.demandes_ds, cfg.registre_ds, person, lines, motif,
                                        payload.urgent, commentaire, when.date(),
                                        links=lambda request_id: links_for(request_id, NOTION_RECIPIENT))
        stock = {item["id"]: item for item in catalogue(fresh=True)}
        messages = [(email, mailer.request_email(person["name"], created, lines, stock, motif, payload.urgent,
                                                 commentaire, links_for(created["id"], email), created.get("url"), when))
                    for email in cfg.notify_emails]
        print(f"[epi-form] request {created.get('number')} from {person['name']}: {created['total']} article(s)")
        return created, messages

    @app.post("/f/{key}/demande")
    async def submit(key: str, request: Request, background: BackgroundTasks):
        require_form_key(key)
        if not allowed(request):
            raise HTTPException(status_code=429)
        try:
            payload = DemandeIn.model_validate(await request.json())
        except (ValidationError, ValueError):
            return JSONResponse({"ok": False, "error": "Demande incomplète."}, status_code=400)
        try:
            created, messages = await run_in_threadpool(register, payload)
        except Refused as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        except (notion.NotionError, requests.RequestException) as exc:
            print(f"[epi-form] Notion error while creating a request: {exc}")
            return JSONResponse({"ok": False, "error": "Notion ne répond pas. Réessaie dans une minute."},
                                status_code=502)
        for email, message in messages:   # sent after the answer: the worker does not wait for SMTP
            background.add_task(deliver, email, message)
        return {"ok": True, "numero": created.get("number")}

    @app.get("/v/{request_id}/{action}/{who}/{signature}", response_class=HTMLResponse)
    def confirm(request_id: str, action: str, who: str, signature: str):
        recipient = verify_link(request_id, action, who, signature)
        req = load_or_404(request_id)
        if req["statut"] != notion.WAITING:
            return already_decided(req)
        return confirmation(req, action, recipient, stock_now())

    def apply_decision(request_id: str, action: str, recipient: str, fields: Dict[str, List[str]]) -> HTMLResponse:
        """Blocking part of the POST: runs in a thread, one decision at a time."""
        decider = recipient
        if recipient == NOTION_RECIPIENT:
            decider = ((fields.get("par") or [""])[0]).strip().lower()
            if decider not in cfg.notify_emails:
                req = load_or_404(request_id)
                if req["statut"] != notion.WAITING:
                    return already_decided(req)
                return confirmation(req, action, recipient, stock_now(), error="Choisis qui décide.", status=400)
        choices = {name[len("ligne_"):]: values[0] for name, values in fields.items()
                   if name.startswith("ligne_") and values}
        if any(value not in notion.LINE_CHOICES for value in choices.values()):
            raise HTTPException(status_code=400)
        user_id = (users_by_email().get(decider) or {}).get("id")
        via = "Notion" if recipient == NOTION_RECIPIENT else "Email"
        with decide_lock:
            try:
                result = notion.decide_request(api, cfg.registre_ds, request_id, ACTIONS[action], user_id, via,
                                               now().date(), cfg.demandes_ds,
                                               choices if action == "valider" else None)
            except notion.RequestGone:
                raise HTTPException(status_code=404)
            except (notion.NotionError, requests.RequestException) as exc:
                print(f"[epi-form] Notion error while deciding {request_id}: {exc}")
                return _page("Notion n'a pas répondu",
                             "<p>Réessaie dans une minute avec le même lien : ce qui est déjà fait ne sera pas refait.</p>",
                             status=502)
        cache.clear("catalogue")
        req = result["request"]
        if not result["changed"]:
            return already_decided(req)
        after = {line["id"]: line["statut"] for line in result["lines"]}
        notes = []
        if notion.HANDED in after.values():
            notes.append("Le stock est à jour.")
        if notion.TO_ORDER in after.values():
            notes.append("Les articles « À commander » sont dans la liste de commande.")
        verdict = "validée" if result["statut"] == notion.VALIDATED else "refusée"
        print(f"[epi-form] request {req.get('number')} {verdict} by {decider} via {via}")
        return _page(f"Demande {req.get('number') or ''} {verdict}",
                     status_table(req, after) + "".join(f"<p class='muted'>{note}</p>" for note in notes)
                     + notion_link(req))

    @app.post("/v/{request_id}/{action}/{who}/{signature}", response_class=HTMLResponse)
    async def decide(request_id: str, action: str, who: str, signature: str, request: Request):
        recipient = verify_link(request_id, action, who, signature)
        fields = parse_qs((await request.body()).decode("utf-8", "replace"), keep_blank_values=True)
        return await run_in_threadpool(apply_decision, request_id, action, recipient, fields)

    return app


app = create_app()
