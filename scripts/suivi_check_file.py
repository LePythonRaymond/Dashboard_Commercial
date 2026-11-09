"""
Compare an accounting « Suivi production » workbook with Furious, project by project. Read only.

    python scripts/suivi_check_file.py --xlsx "Suivi production - 092026 vASL.xlsx" --closing 2026-09-30

It answers one question: can the server rebuild the accountant's numbers from the Furious API alone?
For every project of the tab « Suivi par projet » it compares, with Furious read up to the closing day:

- invoicing: P (before the year), Q (the year), R (total) against issued invoices by invoice date;
- purchases: AA (rows marked « Utiliser ») and the rows pasted without a mark, against purchases by date;
- time: AB against the cost of the time entries (cjm_sprint);
- budget: U (« Achats prévus ») against the project's cost lines, V (« Temps budgété ») against the
  budget lines.

Rows are also matched one by one where the workbook keeps the Furious id (purchases: « ID achat »).
``--cache DIR`` keeps the Furious reads as JSON, so a second run takes seconds.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import warnings
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.suivi.furious import day, fetch_sources, num  # noqa: E402

log = logging.getLogger("suivi")
TOL = 1.0  # euros: below this, two amounts are equal


def col(letter: str) -> int:
    from openpyxl.utils import column_index_from_string

    return column_index_from_string(letter) - 1


def cell(row, letter):
    i = col(letter)
    return row[i] if i < len(row) else None


def as_int(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def read_workbook(path: Path) -> dict:
    """The per-project table and the raw purchase rows of the workbook (values, not formulas)."""
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    projects = {}
    for r in wb["Suivi par projet"].iter_rows(min_row=7, values_only=True):
        p = as_int(cell(r, "F"))
        if p is None:
            continue
        projects[p] = {k: cell(r, k) for k in ("G", "H", "I", "E", "P", "Q", "R", "T", "U", "V", "AA", "AB", "Z", "X")}
    purchases = []
    for i, r in enumerate(wb["2)export_achats"].iter_rows(min_row=2, values_only=True), start=2):
        purchases.append({"row": i, "flag": r[0], "id": as_int(r[3]), "project": as_int(r[4]), "amount": num(r[13]),
                          "date": r[21].date().isoformat() if isinstance(r[21], datetime) else "", "etat": r[22],
                          "validation": r[23]})
    return {"projects": projects, "purchases": purchases}


def read_furious(cache: Path | None) -> dict:
    if cache and (cache / "furious.json").exists():
        return json.loads((cache / "furious.json").read_text())
    from src.api.auth import FuriousAuth
    from src.forecast.sources import fetch_projects

    auth = FuriousAuth()
    data = fetch_sources(auth)
    data["projects"] = fetch_projects(auth)[["id", "title", "total_amount", "total_cost", "cf_bu"]].to_dict("records")
    if cache:
        cache.mkdir(parents=True, exist_ok=True)
        (cache / "furious.json").write_text(json.dumps(data))
    return data


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--xlsx", type=Path, required=True)
    ap.add_argument("--closing", required=True, help="last day of the closed month, YYYY-MM-DD")
    ap.add_argument("--cache", type=Path)
    ap.add_argument("--csv", type=Path, help="write the per-project comparison here")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    warnings.filterwarnings("ignore")
    closing = args.closing
    year_start = f"{closing[:4]}-01-01"

    wbk = read_workbook(args.xlsx)
    fx = read_furious(args.cache)
    print(f"Furious: {', '.join(f'{k} {len(v)}' for k, v in fx.items())}")
    files = wbk["projects"]

    # --- invoices: issued rows by project, split before / during the year
    inv = defaultdict(lambda: [0.0, 0.0])
    for r in fx["invoices"]:
        d = day(r.get("invoice_date"))
        if str(r.get("statut")) == "0" or not d or d > closing:
            continue
        inv[as_int(r.get("project_id"))][0 if d < year_start else 1] += num(r.get("amount_ht"))
    # --- purchases: by project and by date rule, with the status
    statuses = Counter(str(r.get("statut")) for r in fx["purchases"])
    pur = defaultdict(lambda: defaultdict(float))
    api_purchase = {as_int(r.get("id")): r for r in fx["purchases"]}
    for r in fx["purchases"]:
        p = as_int(r.get("project_id"))
        for rule in ("document_date", "purchase_date", "created_at"):
            d = day(r.get(rule))
            if d and d <= closing:
                pur[p][f"{rule}|{r.get('statut')}"] += num(r.get("cost"))
    unflagged = defaultdict(float)
    for r in wbk["purchases"]:
        if r["flag"] in (None, "") and r["project"] in files:
            unflagged[r["project"]] += r["amount"]
    # --- time: cost of the entries up to the closing day, and up to the end of the previous month
    prev_end = (date.fromisoformat(closing).replace(day=1) - __import__("datetime").timedelta(days=1)).isoformat()
    tim = defaultdict(lambda: [0.0, 0.0, 0.0])
    for r in fx["time"]:
        d = day(r.get("start_date"))
        p = as_int(r.get("project_id"))
        if d and d <= closing:
            tim[p][0] += num(r.get("cjm_sprint"))
            tim[p][2] += num(r.get("time"))
        if d and d <= prev_end:
            tim[p][1] += num(r.get("cjm_sprint"))
    # --- budgets
    bud = defaultdict(lambda: defaultdict(float))
    units = Counter()
    for r in fx["budgets"]:
        p = as_int(r.get("project_id"))
        u = str(r.get("unit_type"))
        units[u] += 1
        bud[p][f"qty×cjm|{u}"] += num(r.get("quantity")) * num(r.get("cjm"))
        bud[p][f"qty×250|{u}"] += num(r.get("quantity")) * 250
        bud[p][f"amount|{u}"] += num(r.get("amount"))
    proj = {as_int(r["id"]): r for r in fx["projects"]}

    # --- compare
    out = []
    stats = Counter()
    for p, f in sorted(files.items()):
        hist, year = inv.get(p, [0.0, 0.0])
        fP, fQ, fR = num(f["P"]), num(f["Q"]), num(f["R"])
        cand = pur.get(p, {})
        validated = {rule: sum(v for k, v in cand.items() if k.startswith(rule + "|")) for rule in ("document_date", "purchase_date", "created_at")}
        file_pur = num(f["AA"]) + unflagged.get(p, 0.0)
        t_close, t_prev, t_days = tim.get(p, [0.0, 0.0, 0.0])
        row = {
            "projet": p, "libelle": str(f["G"])[:40], "bu": f["H"],
            "fac_hist_file": round(fP, 2), "fac_hist_api": round(hist, 2),
            "fac_annee_file": round(fQ, 2), "fac_annee_api": round(year, 2),
            "achats_file_AA": round(num(f["AA"]), 2), "achats_file_sans_marque": round(unflagged.get(p, 0.0), 2),
            **{f"achats_api_{k}": round(v, 2) for k, v in validated.items()},
            "temps_file_AB": round(num(f["AB"]), 2), "temps_api_cloture": round(t_close, 2), "temps_api_mois_precedent": round(t_prev, 2),
            "achats_prevus_file_U": round(num(f["U"]), 2), "achats_prevus_api_total_cost": round(num((proj.get(p) or {}).get("total_cost")), 2),
            "temps_budgete_file_V": round(num(f["V"]), 2),
            **{f"budget_{k}": round(v, 2) for k, v in sorted(bud.get(p, {}).items()) if "jours" in k},
            "ca_file_T": round(num(f["T"]), 2), "ca_api": round(num((proj.get(p) or {}).get("total_amount")), 2),
        }
        out.append(row)
        stats["fac_hist ok"] += abs(fP - hist) <= TOL
        stats["fac_annee ok"] += abs(fQ - year) <= TOL
        for rule, v in validated.items():
            stats[f"achats AA+sans marque = API {rule}"] += abs(file_pur - v) <= TOL
            stats[f"achats AA = API {rule}"] += abs(num(f["AA"]) - v) <= TOL
        stats["temps = API au jour de clôture"] += abs(num(f["AB"]) - t_close) <= TOL
        stats["temps = API fin du mois précédent"] += abs(num(f["AB"]) - t_prev) <= TOL
        stats["achats prévus = total_cost"] += abs(num(f["U"]) - row["achats_prevus_api_total_cost"]) <= TOL
        stats["CA = total_amount"] += abs(num(f["T"]) - row["ca_api"]) <= TOL
        for k, v in bud.get(p, {}).items():
            stats[f"temps budgété = {k}"] += abs(num(f["V"]) - v) <= TOL
    n = len(files)
    print(f"\nProjets du fichier : {n}")
    for k, v in sorted(stats.items(), key=lambda kv: -kv[1]):
        if v >= n * 0.2 or k.startswith(("fac", "temps =", "achats AA", "CA", "achats prévus")):
            print(f"  {k:<55} {v:>4} / {n}")
    print("  statuts achats API:", dict(statuts=dict(statuses)), "| unités budget:", dict(units))

    # --- purchases row by row (the workbook keeps the Furious id)
    m = Counter()
    gaps = []
    for r in wbk["purchases"]:
        a = api_purchase.get(r["id"])
        if a is None:
            m["ligne du fichier absente de Furious"] += 1
            continue
        m["trouvée"] += 1
        same = abs(num(a.get("cost")) - r["amount"]) <= 0.01
        m["même montant"] += same
        m["même date (document_date)"] += r["date"] == day(a.get("document_date"))
        if not same and len(gaps) < 5:
            gaps.append((r["row"], r["id"], r["amount"], a.get("cost"), r["etat"], a.get("statut")))
        m[f"etat {r['etat']} / validation {r['validation']} -> statut {a.get('statut')}"] += 1
    print("\nAchats ligne à ligne :", {k: v for k, v in m.items() if not k.startswith("etat")})
    for k, v in sorted(((k, v) for k, v in m.items() if k.startswith("etat")), key=lambda kv: -kv[1])[:12]:
        print(f"  {k}: {v}")
    if gaps:
        print("  écarts de montant (ligne, id, fichier, API, etat, statut):", gaps)

    # --- totals of the year by BU
    tot = defaultdict(lambda: defaultdict(float))
    for row in out:
        for k in ("fac_annee_file", "fac_annee_api", "temps_file_AB", "temps_api_cloture", "temps_api_mois_precedent", "achats_file_AA",
                  "achats_file_sans_marque", "achats_api_document_date"):
            tot[row["bu"]][k] += row[k]
    print("\nTotaux par BU :")
    for bu, t in tot.items():
        print(f"  {bu}: " + " | ".join(f"{k} {v:,.0f}" for k, v in t.items()))

    if args.csv:
        with args.csv.open("w", newline="") as fh:
            keys = sorted({k for r in out for k in r}, key=lambda k: (k not in out[0], list(out[0]).index(k) if k in out[0] else 0, k))
            w = csv.DictWriter(fh, fieldnames=keys)
            w.writeheader()
            w.writerows(out)
        print(f"\nDétail par projet : {args.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
