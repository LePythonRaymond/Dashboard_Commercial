#!/usr/bin/env python3
"""
Production forecast: fill the Google Sheet from Furious, Notion and what people typed.

    # first time: build the tabs from the exported legacy workbook (removes the old tabs with --delete-old-tabs)
    python scripts/run_forecast.py --spreadsheet ID --init --legacy "Copie de Forecast 2.xlsx" --delete-old-tabs
    # every night (and on demand)
    python scripts/run_forecast.py --spreadsheet ID
    # compute and print, write nothing
    python scripts/run_forecast.py --spreadsheet ID --dry-run

Reads only: Furious, Notion. Writes only: the forecast spreadsheet.
"""

from __future__ import annotations

import argparse
import logging
import sys
import warnings
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import get_secret  # noqa: E402
from src.forecast import months as M  # noqa: E402
from src.forecast.build import Params, SheetState, assemble, detect_typed, seed_from_legacy  # noqa: E402
from src.forecast.layout import POLES  # noqa: E402
from src.forecast.sheet import ForecastSheet, actual_month_columns, manual_fields, normalize_typed  # noqa: E402
from src.forecast.sources import fetch_pipe, fetch_projects, fetch_sites, load_legacy  # noqa: E402

PARIS = ZoneInfo("Europe/Paris")
log = logging.getLogger("forecast")


def summary(output, state) -> str:
    lines = []
    year = M.year_of(output.open_from)
    for pole in POLES:
        signed = {m: 0.0 for m in output.grid}
        pipe = {m: 0.0 for m in output.grid}
        for row, s in output.rows.get(pole, []):
            for m, v in s.months.items():
                (signed if s.category == "Signé" else pipe)[m] += v * (1 if s.category == "Signé" else s.proba)
        months_y = [m for m in output.grid if M.year_of(m) == year]
        lines.append(f"{pole}: {len(output.rows.get(pole, []))} lignes")
        lines.append("   " + "  ".join(f"{M.label(m)}: signé {signed[m]:>10,.0f} pipe {pipe[m]:>9,.0f}" for m in months_y))
        lines.append(f"   reste {year} signé {sum(signed[m] for m in months_y):,.0f} · pipe pondéré {sum(pipe[m] for m in months_y):,.0f}"
                     f" · {year + 1} signé {sum(v for m, v in signed.items() if M.year_of(m) == year + 1):,.0f}"
                     f" pipe {sum(v for m, v in pipe.items() if M.year_of(m) == year + 1):,.0f}")
    lines.append(f"Contrôles: {len(output.checks)} · Journal: {len(output.notes)}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spreadsheet", default=get_secret("FORECAST_SPREADSHEET_ID", ""))
    ap.add_argument("--init", action="store_true", help="build the tabs from the legacy workbook")
    ap.add_argument("--legacy", type=Path, help="exported .xlsx of the legacy workbook (with --init)")
    ap.add_argument("--last-closed", default="2026-09", help="last month closed by finance (with --init)")
    ap.add_argument("--delete-old-tabs", action="store_true", help="with --init: delete every tab the forecast does not use")
    ap.add_argument("--dry-run", action="store_true", help="compute and print, write nothing")
    ap.add_argument("--today", help="YYYY-MM-DD, for tests")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    warnings.filterwarnings("ignore")
    if not args.spreadsheet:
        ap.error("--spreadsheet (or FORECAST_SPREADSHEET_ID) is required")

    now = datetime.now(PARIS)
    today = date.fromisoformat(args.today) if args.today else now.date()
    from src.api.auth import FuriousAuth

    auth = FuriousAuth()
    log.info("Furious: projets")
    projects = fetch_projects(auth)
    log.info("Furious: devis en attente")
    pipe = fetch_pipe(auth, PROJECT_ROOT)
    log.info("Notion: sites et passages")
    sites = fetch_sites()
    sheet = None if (args.dry_run and args.init) else ForecastSheet(args.spreadsheet)

    if args.init:
        if not args.legacy:
            ap.error("--init needs --legacy")
        legacy = load_legacy(args.legacy, args.last_closed)
        finance, inputs, notes = seed_from_legacy(legacy, pipe, args.last_closed, projects)
        # Projects created after the newest one each pole of the workbook knows are added on their own.
        latest = []
        for pole in ("Travaux", "Conception"):
            numbers = {r.number for r in legacy.rows if r.pole == pole and r.number}
            known = projects[projects["id"].isin(numbers)]["created_at"].dropna()
            if not known.empty:
                latest.append(max(datetime.fromisoformat(str(d)[:10]).date() for d in known))
        since = min(latest) if latest else today
        first_year, last_year = M.year_of(args.last_closed), M.year_of(M.add(M.key(today.year, today.month), 17))
        objectives = {(pole, y): (legacy.objectives[pole][0] if y == first_year else 0.0, legacy.objectives[pole][1], {})
                      for pole in POLES for y in range(first_year, last_year + 1)}
        params = Params(args.last_closed, 2, since, legacy.kv, objectives)
        pl = {pole: {"report": legacy.pl_report[pole], args.last_closed: legacy.pl_september[pole]} for pole in POLES}
        # Real costs of the reported months: production ÷ the first half's real KV (accounts at the end of June).
        pl_costs = {pole: {"report": legacy.pl_report[pole] / legacy.s1_kv[pole]} for pole in POLES if legacy.s1_kv.get(pole)}
        for n in notes:
            log.info("reprise: %s", n)
        if args.dry_run:
            pl_columns = {"report": "B", **{m: None for m in actual_month_columns(first_year, last_year)}}
            state = SheetState(params, finance, pl, {}, inputs, {}, {}, pl_columns)
            output = assemble(state, projects, pipe, sites, today)
            output.notes = notes + output.notes
            print(summary(output, state))
            return 0
        sheet.initialize(params, pl, finance, inputs, first_year, last_year, args.delete_old_tabs, pl_costs)
        log.info("Onglets créés")

    state = sheet.read_state()
    fields = {pole: manual_fields(pole, []) for pole in POLES}
    inputs, notes = detect_typed(state, fields, normalize_typed)
    state.inputs = inputs
    output = assemble(state, projects, pipe, sites, today)
    output.notes = notes + output.notes
    print(summary(output, state))
    if args.dry_run:
        return 0
    sheet.write(output, state, now)
    log.info("Feuille mise à jour")
    return 0


def alert_failure(error: BaseException, started_at: datetime) -> None:
    """Email PIPELINE_ALERT_EMAIL (same address as the pipeline alerts) when a forecast run fails."""
    import html
    import traceback

    try:
        from config.settings import settings
        from src.integrations.email_sender import EmailSender

        body = (
            f"<p>La mise à jour de la prévision de production a échoué le {started_at:%d/%m/%Y à %H:%M}.</p>"
            f"<p><b>{html.escape(type(error).__name__)}</b> : {html.escape(str(error))}</p>"
            f"<pre>{html.escape(traceback.format_exc()[-4000:])}</pre>"
            "<p>Le Google Sheet garde sa dernière version. Relance : "
            "<code>venv/bin/python3 scripts/run_forecast.py --spreadsheet &lt;id&gt;</code></p>"
        )
        EmailSender()._send_email(
            settings.pipeline_alert_email,
            f"[Myrium] Prévision de production : mise à jour échouée ({started_at:%d/%m/%Y})",
            body,
        )
    except Exception:  # the alert must never hide the original error
        log.exception("Alert email failed")


if __name__ == "__main__":
    started = datetime.now(PARIS)
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001 - report every failure, then exit non-zero
        log.exception("Forecast run failed")
        if "--dry-run" not in sys.argv:
            alert_failure(exc, started)
        sys.exit(1)
