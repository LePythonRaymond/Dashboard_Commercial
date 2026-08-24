"""
Reconciliation / data-quality guard (Envoyé + Signé).

Independently re-derives, straight from the cleaned Furious data, the set of devis
that SHOULD appear in the Envoyé and Signé views for a given year, and compares it
against the IDs that actually landed in the Google Sheets the dashboard reads.

It surfaces two failure modes:
  - ``missing``: devis present in Furious but absent from the sheets — the silent gap
    that hid ~4.4 M€ of pending devis (the ``created_at`` keying bug). This is the
    one that matters most: no error, no crash, just wrong totals.
  - ``extra``: IDs present in the sheets but not a currently-valid Furious devis
    (stale / deleted). Informational for Signé (cumulative sheets keep history).

DESIGN NOTE — independence. The ground-truth side deliberately does NOT use
``ViewGenerator``. It is a flat status+date filter on the cleaned Furious data, so a
bug in the view logic would still leave this check "right" and therefore able to
catch the discrepancy. Reusing the view code here would make the test circular.
"""

from __future__ import annotations

import time

import pandas as pd
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, Any, Optional, Tuple

from config.settings import STATUS_WON, STATUS_WAITING


# A devis must be worth at least this much (gross) to count as a "real" drift, so a
# stray 0/1 € placeholder devis doesn't page you every morning.
DEFAULT_MIN_AMOUNT_EUR = 500.0

# An independent re-fetch always shows a little churn: a handful of devis get
# created / won / lost / re-dated between the pipeline writing the sheets and this
# check re-reading Furious. We only want to alert on a *systemic* gap (the
# created_at bug was 79 devis / 4.4 M€), not on that daily movement. So a view is
# only flagged when it crosses one of these thresholds.
DEFAULT_ALERT_MISSING_COUNT = 5         # >= this many significant missing devis
DEFAULT_ALERT_MISSING_GROSS = 100_000.0  # OR >= this much missing gross (€)
# Envoyé is a snapshot rewritten daily, so won/lost devis legitimately linger in it
# until the next rewrite. Only flag "extra" if there are a lot — which would mean the
# live rewrite itself stopped working.
DEFAULT_ALERT_EXTRA_COUNT = 30

# --- Reading the sheet side safely -------------------------------------------
# GoogleSheetsClient.list_worksheets() and read_worksheet() swallow EVERY exception
# (a Google rate-limit included) and return [] / an empty DataFrame. So an empty
# result is our only signal that a read failed, and we must never mistake it for
# "the sheet is really empty" — doing so once reported all 201 signed devis as
# missing (2026-08-23) when the sheets were perfectly fine.
SHEET_LIST_RETRIES = 3        # a configured year always has worksheets
SHEET_READ_PASSES = 2         # re-read the whole view if it comes back with no rows
SHEET_BACKOFF_S = 20.0        # Google read quota is per-minute; wait it out
SHEET_PACING_S = 1.0          # spread reads so we don't trip the quota to begin with
# Below this many expected devis, a genuinely empty sheet side is plausible
# (new year, first month), so we still treat it as real data rather than a failure.
EMPTY_SIDE_MIN_TRUTH = 5


def _is_manual_id(value: Any) -> bool:
    """MAN- rows are dashboard-only manual projects; they never exist in Furious."""
    return str(value).strip().upper().startswith("MAN-")


def _year(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce").dt.year


def _amount(value: Any) -> float:
    try:
        amt = float(pd.to_numeric(value, errors="coerce"))
    except (TypeError, ValueError):
        return 0.0
    return amt if amt == amt else 0.0  # guard NaN


@dataclass
class ViewRecon:
    """Reconciliation result for a single view (envoye | signe) for one year."""
    view: str
    year: int
    furious_count: int
    sheet_count: int
    furious_gross: float
    sheet_gross: float
    missing: List[Dict[str, Any]] = field(default_factory=list)  # in Furious, not in sheets
    extra: List[Dict[str, Any]] = field(default_factory=list)    # in sheets, not in Furious
    # Set when the sheet side could not be read reliably. The view is then reported
    # as "not verified" — never as devis missing, which would be a false alarm.
    inconclusive: bool = False
    problems: List[str] = field(default_factory=list)
    sheets_listed: int = 0
    sheets_with_rows: int = 0

    def significant_missing(self, min_amount: float) -> List[Dict[str, Any]]:
        return [m for m in self.missing if abs(m["amount"]) >= min_amount]

    def to_dict(self, min_amount: float) -> Dict[str, Any]:
        sig = self.significant_missing(min_amount)
        return {
            "view": self.view,
            "year": self.year,
            "furious_count": self.furious_count,
            "sheet_count": self.sheet_count,
            "furious_gross": round(self.furious_gross, 2),
            "sheet_gross": round(self.sheet_gross, 2),
            "inconclusive": self.inconclusive,
            "problems": self.problems,
            "sheets_listed": self.sheets_listed,
            "sheets_with_rows": self.sheets_with_rows,
            "missing_count": len(self.missing),
            "missing_significant_count": len(sig),
            "missing_gross": round(sum(m["amount"] for m in self.missing), 2),
            "extra_count": len(self.extra),
            "extra_gross": round(sum(e["amount"] for e in self.extra), 2),
            "missing": self.missing,
            "extra": self.extra,
        }


def _ground_truth(df: pd.DataFrame, view: str, year: int) -> pd.DataFrame:
    """Which REAL Furious devis belong in ``view`` for ``year`` — independent of view code.

    - envoye: currently-pending pipe (statut ∈ WAITING) whose devis ``date`` is in ``year``.
    - signe:  won (statut ∈ WON) whose devis ``date`` OR effective signature date is in ``year``.

    MAN- rows are excluded (not in Furious). Excluded owners are assumed already
    dropped upstream by ``DataCleaner.clean`` so both sides stay consistent.
    """
    if df is None or df.empty or "statut_clean" not in df.columns:
        return pd.DataFrame(columns=getattr(df, "columns", None))
    status = df["statut_clean"].astype(str)
    date_year = _year(df["date"]) if "date" in df.columns else pd.Series(False, index=df.index)
    if view == "envoye":
        mask = status.isin(STATUS_WAITING) & (date_year == year)
    elif view == "signe":
        if "date_effective_won" in df.columns:
            won_year = _year(df["date_effective_won"])
        else:
            won_year = pd.Series(False, index=df.index)
        mask = status.isin(STATUS_WON) & ((date_year == year) | (won_year == year))
    else:
        raise ValueError(f"unknown view: {view}")
    out = df[mask].copy()
    if "id" in out.columns:
        out = out[~out["id"].map(_is_manual_id)]
    return out


@dataclass
class SheetSide:
    """What we managed to read from the dashboard's sheets for one view/year."""
    amounts: Dict[str, float] = field(default_factory=dict)
    sheets_listed: int = 0
    sheets_with_rows: int = 0
    problems: List[str] = field(default_factory=list)


def _list_sheets(sheets_client, view: str, year: int) -> Tuple[List[str], List[str]]:
    """List the view's worksheets, retrying while the listing comes back empty.

    ``list_worksheets`` returns [] both when the spreadsheet genuinely has no tabs
    and when the API call failed, so we retry: for a configured year an empty
    listing is never legitimate.
    """
    problems: List[str] = []
    for attempt in range(SHEET_LIST_RETRIES):
        try:
            names = list(sheets_client.list_worksheets(view_type=view, year=year) or [])
        except Exception as exc:
            names = []
            problems.append(f"listing {view} {year} a échoué ({type(exc).__name__}: {exc})")
        if names:
            return names, problems
        if attempt < SHEET_LIST_RETRIES - 1:
            time.sleep(SHEET_BACKOFF_S * (attempt + 1))
    problems.append(f"aucun onglet listé pour {view} {year}")
    return [], problems


def _read_pass(
    sheets_client, view: str, year: int, names: List[str]
) -> Tuple[Dict[str, float], int, List[str]]:
    """One read of every worksheet: {id -> amount}, how many tabs yielded rows, problems."""
    amounts: Dict[str, float] = {}
    problems: List[str] = []
    with_rows = 0
    for idx, name in enumerate(names):
        if idx and SHEET_PACING_S:
            time.sleep(SHEET_PACING_S)  # stay under the per-minute read quota
        try:
            d = sheets_client.read_worksheet(name, view_type=view, year=year)
        except Exception as exc:
            problems.append(f"lecture de '{name}' impossible ({type(exc).__name__}: {exc})")
            continue
        if d is None or d.empty:
            continue
        if "id" not in d.columns:
            problems.append(f"onglet '{name}' lu sans colonne 'id'")
            continue
        rows_here = 0
        for _, row in d.iterrows():
            rid = str(row.get("id", "")).strip()
            if not rid or _is_manual_id(rid):
                continue
            rows_here += 1
            if rid not in amounts:
                amounts[rid] = _amount(row.get("amount"))
        if rows_here:
            with_rows += 1
    return amounts, with_rows, problems


def _read_sheet_side(sheets_client, view: str, year: int) -> SheetSide:
    """Read the sheet side, retrying the whole view if it comes back with no rows."""
    names, problems = _list_sheets(sheets_client, view, year)
    side = SheetSide(sheets_listed=len(names), problems=list(problems))
    if not names:
        return side

    for attempt in range(SHEET_READ_PASSES):
        amounts, with_rows, pass_problems = _read_pass(sheets_client, view, year, names)
        if with_rows or attempt == SHEET_READ_PASSES - 1:
            side.amounts = amounts
            side.sheets_with_rows = with_rows
            side.problems.extend(pass_problems)
            return side
        # Every tab came back empty — far more likely a throttled read than a
        # genuinely empty year. Wait out the quota window and read again.
        time.sleep(SHEET_BACKOFF_S)
    return side


def reconcile_view(df: pd.DataFrame, sheets_client, view: str, year: int) -> ViewRecon:
    """Diff the independent Furious ground truth against the sheets for one view/year."""
    truth = _ground_truth(df, view, year)
    truth_rows: Dict[str, pd.Series] = {}
    for _, r in truth.iterrows():
        truth_rows[str(r.get("id", "")).strip()] = r
    side = _read_sheet_side(sheets_client, view, year)
    sheet = side.amounts
    truth_ids = set(truth_rows)
    sheet_ids = set(sheet)
    truth_gross = float(sum(_amount(r.get("amount")) for r in truth_rows.values()))

    # Could we trust what we read? An unreadable sheet side must never be reported
    # as "every devis is missing" — that is a checker failure, not a data gap.
    blocker: Optional[str] = None
    if side.sheets_listed == 0:
        blocker = "aucun onglet n'a pu être listé (lecture Google Sheets en échec)"
    elif not sheet_ids and len(truth_ids) >= EMPTY_SIDE_MIN_TRUTH:
        blocker = (
            f"les {side.sheets_listed} onglets ont été lus vides alors que Furious "
            f"en attend {len(truth_ids)} — lecture Google Sheets en échec (quota ?)"
        )
    if blocker is not None:
        return ViewRecon(
            view=view,
            year=year,
            furious_count=len(truth_ids),
            sheet_count=0,
            furious_gross=truth_gross,
            sheet_gross=0.0,
            inconclusive=True,
            problems=[blocker] + side.problems,
            sheets_listed=side.sheets_listed,
            sheets_with_rows=side.sheets_with_rows,
        )

    missing = []
    for rid in truth_ids - sheet_ids:
        r = truth_rows[rid]
        missing.append({
            "id": rid,
            "title": str(r.get("title", ""))[:90],
            "amount": _amount(r.get("amount")),
            "owner": str(r.get("assigned_to", "")),
            "typologie": str(r.get("cf_typologie_de_devis", "")),
            "date": str(r.get("date", ""))[:10],
            "statut": str(r.get("statut", r.get("statut_clean", ""))),
        })
    extra = [{"id": rid, "amount": sheet[rid]} for rid in sheet_ids - truth_ids]

    missing.sort(key=lambda m: -abs(m["amount"]))
    extra.sort(key=lambda e: -abs(e["amount"]))

    return ViewRecon(
        view=view,
        year=year,
        furious_count=len(truth_ids),
        sheet_count=len(sheet_ids),
        furious_gross=truth_gross,
        sheet_gross=float(sum(sheet.values())),
        missing=missing,
        extra=extra,
        problems=side.problems,
        sheets_listed=side.sheets_listed,
        sheets_with_rows=side.sheets_with_rows,
    )


def reconcile(
    df: pd.DataFrame,
    sheets_client,
    year: int,
    *,
    views: Optional[List[str]] = None,
    min_amount: float = DEFAULT_MIN_AMOUNT_EUR,
    alert_missing_count: int = DEFAULT_ALERT_MISSING_COUNT,
    alert_missing_gross: float = DEFAULT_ALERT_MISSING_GROSS,
    alert_extra_count: int = DEFAULT_ALERT_EXTRA_COUNT,
) -> Dict[str, Any]:
    """Run reconciliation for the given views and decide whether to alert.

    A view is flagged when its *significant* missing devis (each gross >= ``min_amount``)
    cross a threshold — either ``alert_missing_count`` devis or ``alert_missing_gross``
    in total — so normal daily CRM churn stays silent while a systemic gap alerts. For
    Envoyé, a large ``extra`` count (>= ``alert_extra_count``) is also flagged, as that
    would mean the daily live rewrite stopped. Signé ``extra`` is expected history and
    is reported only. Returns a JSON-serialisable report (plus in-memory ``recons``).
    """
    views = views or ["envoye", "signe"]
    recons = {v: reconcile_view(df, sheets_client, v, year) for v in views}

    alert = False
    infrastructure_alert = False
    reasons: List[str] = []
    for v, rc in recons.items():
        if rc.inconclusive:
            # The sheets could not be read, so we know nothing about this view.
            # Say exactly that — never dress it up as missing devis.
            infrastructure_alert = True
            reasons.append(
                f"{v}: contrôle impossible — {rc.problems[0] if rc.problems else 'lecture en échec'} "
                f"(aucun devis n'est déclaré manquant)"
            )
            continue
        sig_missing = rc.significant_missing(min_amount)
        miss_gross = sum(m["amount"] for m in sig_missing)
        if len(sig_missing) >= alert_missing_count or miss_gross >= alert_missing_gross:
            alert = True
            reasons.append(
                f"{v}: {len(sig_missing)} devis manquant(s) (≥{min_amount:.0f}€) "
                f"= {miss_gross:,.0f}€"
            )
        elif sig_missing:
            # Below threshold — noted for the report, but not an alert.
            reasons.append(
                f"{v}: {len(sig_missing)} devis manquant(s) sous le seuil "
                f"({miss_gross:,.0f}€) — churn normal, pas d'alerte"
            )
        if v == "envoye":
            sig_extra = [e for e in rc.extra if abs(e["amount"]) >= min_amount]
            if len(sig_extra) >= alert_extra_count:
                alert = True
                reasons.append(
                    f"{v}: {len(sig_extra)} ligne(s) en trop — la réécriture quotidienne "
                    f"a peut-être cessé"
                )

    return {
        "year": year,
        "min_amount_eur": min_amount,
        "thresholds": {
            "alert_missing_count": alert_missing_count,
            "alert_missing_gross": alert_missing_gross,
            "alert_extra_count": alert_extra_count,
        },
        "alert": alert,
        "infrastructure_alert": infrastructure_alert,
        "reasons": reasons,
        "views": {v: rc.to_dict(min_amount) for v, rc in recons.items()},
        "recons": recons,  # in-memory objects (not serialised by callers that json.dump views only)
    }


# ---------------------------------------------------------------------------
# Email rendering
# ---------------------------------------------------------------------------

_VIEW_LABEL = {"envoye": "Envoyé", "signe": "Signé"}


def _rows_html(items: List[Dict[str, Any]], cols: List, limit: int = 40) -> str:
    if not items:
        return '<tr><td colspan="6" style="padding:6px;color:#16a34a;">— aucun —</td></tr>'
    out = []
    for it in items[:limit]:
        tds = "".join(
            f'<td style="padding:4px 8px;border-bottom:1px solid #eee;{align}">{val(it)}</td>'
            for val, align in cols
        )
        out.append(f"<tr>{tds}</tr>")
    if len(items) > limit:
        out.append(
            f'<tr><td colspan="{len(cols)}" style="padding:4px 8px;color:#666;">'
            f'… +{len(items) - limit} autres</td></tr>'
        )
    return "".join(out)


def build_report_html(report: Dict[str, Any]) -> str:
    year = report["year"]
    min_amount = report["min_amount_eur"]
    if report["alert"]:
        status_color, status_text = "#dc2626", "⚠️ ÉCART DÉTECTÉ"
    elif report.get("infrastructure_alert"):
        status_color, status_text = "#d97706", "🔧 CONTRÔLE IMPOSSIBLE"
    else:
        status_color, status_text = "#16a34a", "✅ Cohérent"

    blocks = []
    for v in ("envoye", "signe"):
        rc = report["views"].get(v)
        if not rc:
            continue
        label = _VIEW_LABEL.get(v, v)
        if rc.get("inconclusive"):
            problems_html = "".join(f"<li>{p}</li>" for p in rc.get("problems", []))
            blocks.append(f"""
        <h3 style="margin:18px 0 4px;">{label} {year} — non vérifié</h3>
        <div style="padding:10px 12px;background:#fef3c7;border-left:4px solid #d97706;">
          <p style="margin:0 0 6px;">
            Les onglets Google Sheets de cette vue n'ont pas pu être lus, donc
            <b>aucune comparaison n'a été faite</b>. Ce n'est pas un problème de données :
            les {rc['furious_count']} devis correspondants dans Furious ne sont
            <b>pas</b> déclarés manquants.
          </p>
          <ul style="margin:0;color:#78350f;font-size:13px;">{problems_html}</ul>
          <p style="margin:6px 0 0;color:#78350f;font-size:12px;">
            Cause la plus fréquente : quota de lecture Google Sheets atteint.
            La vue sera revérifiée au prochain passage.
          </p>
        </div>
        """)
            continue
        miss_cols = [
            (lambda it: it["id"], "text-align:left;"),
            (lambda it: it["title"], "text-align:left;"),
            (lambda it: f'{it["amount"]:,.0f}€', "text-align:right;font-weight:bold;"),
            (lambda it: it["typologie"], "text-align:left;"),
            (lambda it: it["owner"], "text-align:left;"),
            (lambda it: it["date"], "text-align:left;"),
        ]
        miss_header = (
            '<tr style="background:#f3f4f6;">'
            '<th style="padding:4px 8px;text-align:left;">ID</th>'
            '<th style="padding:4px 8px;text-align:left;">Titre</th>'
            '<th style="padding:4px 8px;text-align:right;">Montant</th>'
            '<th style="padding:4px 8px;text-align:left;">Typologie</th>'
            '<th style="padding:4px 8px;text-align:left;">Commercial</th>'
            '<th style="padding:4px 8px;text-align:left;">Date devis</th></tr>'
        )
        delta_count = rc["furious_count"] - rc["sheet_count"]
        blocks.append(f"""
        <h3 style="margin:18px 0 4px;">{label} {year}</h3>
        <p style="margin:0 0 6px;color:#444;">
          Furious : <b>{rc['furious_count']}</b> devis / {rc['furious_gross']:,.0f}€ &nbsp;·&nbsp;
          Dashboard : <b>{rc['sheet_count']}</b> devis / {rc['sheet_gross']:,.0f}€ &nbsp;·&nbsp;
          écart : <b style="color:{'#dc2626' if delta_count else '#16a34a'}">{delta_count:+d}</b> devis
        </p>
        <p style="margin:0 0 4px;font-weight:bold;">Manquants (dans Furious, absents du dashboard) — {rc['missing_count']} :</p>
        <table style="border-collapse:collapse;font-size:13px;width:100%;">{miss_header}{_rows_html(rc['missing'], miss_cols)}</table>
        """)

    reasons_html = "".join(f"<li>{r}</li>" for r in report["reasons"]) or "<li>—</li>"

    return f"""
    <html><body style="font-family:Arial,Helvetica,sans-serif;color:#111;max-width:820px;">
      <h2 style="margin:0 0 2px;">Réconciliation Dashboard ↔ Furious — {year}</h2>
      <p style="margin:0 0 10px;">
        <span style="color:{status_color};font-weight:bold;font-size:16px;">{status_text}</span>
        &nbsp;·&nbsp;<span style="color:#666;">seuil {min_amount:.0f}€ · {datetime.now().strftime('%d/%m/%Y %H:%M')}</span>
      </p>
      <ul style="margin:0 0 8px;color:#444;">{reasons_html}</ul>
      {''.join(blocks)}
      <p style="margin-top:18px;color:#888;font-size:12px;">
        Contrôle indépendant : la vérité Furious est recalculée par un simple filtre statut+date,
        sans passer par la logique des vues, afin de détecter tout devis qui n'arriverait pas dans le dashboard.
      </p>
    </body></html>
    """
