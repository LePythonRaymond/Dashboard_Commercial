"""
E-mail sent to the office for each request: who, what, current stock, and two
buttons (Valider, Refuser) that open a confirmation page of the service.

The buttons lead to a page with a confirmation button, never straight to the
action: some mail systems open every link of a message to scan it, and a
direct link would validate requests on its own.
"""

import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from html import escape
from pathlib import Path
from typing import Any, Dict, List, Optional

from .notion import shortage, stock_label

GREEN = "#2f6b4f"
RED = "#b42318"
SENDER_NAME = "EPI Merci Raymond"


def _stock_cell(item: Optional[Dict[str, Any]], qty: float) -> str:
    """'5', 'à compter', or '1 (manque 2)' in red when the counted stock is short."""
    missing = shortage(item, qty)
    label = escape(stock_label(item))
    if missing:
        return f"<span style='color:{RED}'>{label} (manque {missing:g})</span>"
    return label


def _text_line(line: Dict[str, Any], item: Optional[Dict[str, Any]]) -> str:
    """'- T-shirt noir · M x 2 (en stock : 1, manque 1)' for the plain-text version."""
    missing = shortage(item, line["qty"])
    extra = f", manque {missing:g}" if missing else ""
    return f"- {line['title']} x {line['qty']} (en stock : {stock_label(item)}{extra})"


def request_email(person_name: str, request: Dict[str, Any], lines: List[Dict[str, Any]],
                  catalogue_by_id: Dict[str, Dict[str, Any]], motif: Optional[str], urgent: bool,
                  commentaire: str, links: Dict[str, str], notion_url: Optional[str],
                  when: datetime) -> Dict[str, str]:
    """Subject, HTML and plain-text bodies of the office e-mail (when: Paris time of the request)."""
    total = sum(line["qty"] for line in lines)
    plural = "s" if total > 1 else ""
    number = str(request.get("number") or "")
    subject = f"{'[URGENT] ' if urgent else ''}Demande EPI {number} : {person_name}, {total} article{plural}"
    cell = "padding:6px 10px;border-bottom:1px solid #e5e5e5"
    rows = "".join(
        f"<tr><td style='{cell}'>{escape(line['title'])}</td>"
        f"<td style='{cell};text-align:center'>{line['qty']}</td>"
        f"<td style='{cell};text-align:center;color:#666'>"
        f"{_stock_cell(catalogue_by_id.get(line['article_id']), line['qty'])}</td></tr>"
        for line in lines)
    details = []
    if motif:
        details.append(f"<b>Motif :</b> {escape(motif)}")
    if urgent:
        details.append(f"<b style='color:{RED}'>Urgent</b>")
    if commentaire:
        details.append(f"<b>Précisions :</b> {escape(commentaire)}")
    button = ("display:inline-block;padding:10px 18px;border-radius:6px;text-decoration:none;"
              "font-weight:600;margin:4px 8px 4px 0")
    notion_link = f' (<a href="{escape(notion_url)}">ouvrir dans Notion</a>)' if notion_url else ""
    html = f"""<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#1f1f1f;max-width:560px">
<p><b>{escape(person_name)}</b> demande {total} article{plural}
({escape(number)}, le {when:%d/%m à %H:%M}){notion_link}.</p>
{''.join(f'<p style="margin:4px 0">{d}</p>' for d in details)}
<table style="border-collapse:collapse;margin:12px 0;width:100%">
<tr><th style="text-align:left;padding:6px 10px;border-bottom:2px solid #ccc">Article</th>
<th style="padding:6px 10px;border-bottom:2px solid #ccc">Qté</th>
<th style="padding:6px 10px;border-bottom:2px solid #ccc">En stock</th></tr>
{rows}
</table>
<p>
<a href="{escape(links['valider'])}" style="{button};background:{GREEN};color:#fff">Valider la demande</a>
<a href="{escape(links['refuser'])}" style="{button};background:#eee;color:#333">Refuser</a>
</p>
<p style="color:#666;font-size:12px">Valider ouvre une page où tu choisis, article par article :
« Remis » (le stock baisse), « À commander » (l'article part dans la liste de commande) ou « Refusé ».
Rien ne change tant que tu n'as pas confirmé sur cette page.</p>
</div>"""
    text = "\n".join([
        f"{person_name} demande {total} article{plural} ({number}, le {when:%d/%m à %H:%M}).",
        *(_text_line(line, catalogue_by_id.get(line["article_id"])) for line in lines),
        *([f"Motif : {motif}"] if motif else []),
        *(["URGENT"] if urgent else []),
        *([f"Précisions : {commentaire}"] if commentaire else []),
        "",
        f"Valider : {links['valider']}",
        f"Refuser : {links['refuser']}",
        *([f"Notion : {notion_url}"] if notion_url else []),
    ])
    return {"subject": subject, "html": html, "text": text}


def send_mail(host: str, port: int, user: str, password: str, to: str, message: Dict[str, str],
              dry_run_dir: str = "") -> None:
    """Send one e-mail (or write it to dry_run_dir for tests and local runs)."""
    if dry_run_dir:
        folder = Path(dry_run_dir)
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        (folder / f"{stamp}-{to}.html").write_text(
            "<!doctype html><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            f"<p><b>To:</b> {escape(to)}<br><b>Subject:</b> {escape(message['subject'])}</p><hr>{message['html']}",
            encoding="utf-8")
        return
    mail = MIMEMultipart("alternative")
    mail["Subject"] = message["subject"]
    mail["From"] = formataddr((SENDER_NAME, user))
    mail["To"] = to
    mail.attach(MIMEText(message["text"], "plain", "utf-8"))
    mail.attach(MIMEText(message["html"], "html", "utf-8"))
    with smtplib.SMTP(host, port, timeout=30) as server:
        server.starttls()
        server.login(user, password)
        server.sendmail(user, [to], mail.as_string())
