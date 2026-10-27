"""
Maintenance contracts, month by month.

Each site of the Notion "Sites" base gives one row. Two numbers make it:

1. The value of a calendar year comes from the contract dates, with the rule of the Notion
   column "Total HT Cette année" (see ``year_value``). It never depends on the planning,
   so a duplicated, offered or missing passage cannot change it.
2. That value is split over the months in proportion to the site's passages in the planning
   (one planned passage = one passage done). Two passages on the same site and day count once.
   A year the planning does not cover yet (n8n generates it on 1 January) borrows the months
   of the latest planned year. A site with no planning at all is split equally.

Example: a contract worth 12 000 € a year with 4 passages in June and 1 in December
(5 in the year): June = 12 000 × 4/5 = 9 600 €, December = 2 400 €.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Dict, Iterable, List, Optional, Tuple

from . import months as M


@dataclass
class Site:
    page_id: str
    name: str
    annual: float
    start: Optional[date] = None
    end: Optional[date] = None
    lost: bool = False
    archived: bool = False
    furious_id: str = ""
    int_ext: str = ""
    passages: Dict[str, int] = field(default_factory=dict)  # month key -> passages that month
    owner: str = ""  # referent gardener
    client: str = ""
    frequency: str = ""
    reconduction: str = ""  # Notion "Reconduction" options, joined
    dates: List[str] = field(default_factory=list)  # ISO day of every passage (one per day)


def passages_in_year(site: Site, year: int, before: Optional[date] = None) -> int:
    """Planned passages of ``year`` inside the contract's months; with ``before``, only those dated earlier."""
    months = set(active_months(site, year))
    n = 0
    for day in site.dates:
        if int(day[:4]) != year or int(day[5:7]) not in months:
            continue
        if before is not None and date.fromisoformat(day) >= before:
            continue
        n += 1
    return n


def year_value(site: Site, year: int) -> float:
    """Contract value of ``year``: the rule of Notion's "Total HT Cette année".

    - lost (Reconduction "Perdu") and ended before the year: 0;
    - lost and ending in the year: the months up to the end
      (from the start month if the contract started that year, else from January);
    - started in the year, with no end or a later one: the months from the start;
    - started after the year: 0 (the Notion formula counts it in full; a forecast cannot);
    - otherwise the full annual amount (a contract that is not lost renews itself).
    """
    annual = float(site.annual or 0.0)
    if annual <= 0:
        return 0.0
    start, end = site.start, site.end
    if start is not None and start.year > year:
        return 0.0
    first = start.month if (start is not None and start.year == year) else 1
    if site.lost and end is not None and end.year < year:
        return 0.0
    if site.lost and end is not None and end.year == year:
        return max(0.0, annual * (end.month - first + 1) / 12)
    if start is not None and start.year == year and (end is None or end.year > year):
        return annual * (13 - start.month) / 12
    return annual


def active_months(site: Site, year: int) -> List[int]:
    """Month numbers of ``year`` in which the contract runs (consistent with ``year_value``)."""
    start, end = site.start, site.end
    if start is not None and start.year > year:
        return []
    first = start.month if (start is not None and start.year == year) else 1
    last = 12
    if site.lost and end is not None:
        if end.year < year:
            return []
        if end.year == year:
            last = end.month
    return list(range(first, last + 1))


def _template(site: Site) -> Dict[int, int]:
    """Passages per month number of the site's reference year: the year with the most passages.

    A year is generated in full (January to December) when someone clicks "Générer planning" or on
    1 January, so the busiest year is a complete one; a stray passage in January of the next year
    (a weekly schedule crossing the year) cannot become the reference.
    """
    per_year: Dict[int, int] = {}
    for k, n in site.passages.items():
        if n > 0:
            per_year[M.year_of(k)] = per_year.get(M.year_of(k), 0) + n
    if not per_year:
        return {}
    best = max(per_year.items(), key=lambda yn: (yn[1], yn[0]))[0]
    return {M.parse(k)[1]: n for k, n in site.passages.items() if M.year_of(k) == best and n > 0}


def monthly_values(site: Site, years: Iterable[int]) -> Dict[str, float]:
    """Value of every month of ``years``. The months of a year add up to ``year_value``.

    A year's own passages are used when its planning is nearly complete (at least 80 % of the
    reference year's passages over the contract months); otherwise the reference year's months.
    """
    out: Dict[str, float] = {}
    template = _template(site)
    for year in years:
        value = year_value(site, year)
        months = active_months(site, year)
        if value <= 0 or not months:
            continue
        own = {m: site.passages.get(M.key(year, m), 0) for m in months}
        reference = {m: template.get(m, 0) for m in months}
        counts = own if sum(own.values()) >= 0.8 * sum(reference.values()) and sum(own.values()) > 0 else reference
        total = sum(counts.values())
        if total == 0:
            counts = {m: 1 for m in months}
            total = len(months)
        for m, n in counts.items():
            if n:
                out[M.key(year, m)] = value * n / total
    return out


def _offered(props: dict) -> bool:
    """True when the passage is marked offered (checkbox "Offert", once MR Planning sends it to Notion)."""
    return bool((props.get("Offert") or {}).get("checkbox"))


def passage_dates_by_site(planning_pages: Iterable[dict]) -> Dict[str, List[str]]:
    """Days with a paying passage, per site (a site with two passages the same day counts that day once)."""
    days: Dict[str, set] = {}
    for page in planning_pages:
        props = page.get("properties", {})
        if _offered(props):
            continue
        day = ((props.get("Date") or {}).get("date") or {}).get("start")
        for r in (props.get("Site") or {}).get("relation", []):
            if day and r.get("id"):
                days.setdefault(r["id"].replace("-", ""), set()).add(day[:10])
    return {k: sorted(v) for k, v in days.items()}


def passages_by_site(planning_pages: Iterable[dict]) -> Dict[str, Dict[str, int]]:
    """Count passages per site and month from Notion "Planning" pages.

    A passage counts when it has a date and a site and is not marked offered; two passages of a site on the
    same day count once.
    """
    seen = set()
    counts: Dict[str, Dict[str, int]] = {}
    for page in planning_pages:
        props = page.get("properties", {})
        if _offered(props):
            continue
        day = ((props.get("Date") or {}).get("date") or {}).get("start")
        sites = [r.get("id") for r in (props.get("Site") or {}).get("relation", [])]
        if not day or not sites:
            continue
        for site_id in sites:
            k = (site_id.replace("-", ""), day[:10])
            if k in seen:
                continue
            seen.add(k)
            per_site = counts.setdefault(k[0], {})
            month = day[:7]
            per_site[month] = per_site.get(month, 0) + 1
    return counts


# --------------------------------------------------------------------------- contract status (Safe / À risque / Mort)

RISK_HORIZON_DAYS = 90  # an end date this close, not confirmed yet, is a risk


def contract_health(site: Site, today: date, typed: Optional[Dict[str, object]] = None) -> Tuple[str, str]:
    """Status of a maintenance contract and why: ("Safe" | "À risque" | "Mort" | "", reason).

    Only a person can say a contract is safe (confirmed for the next one to three years): that is the
    status typed in the sheet, with an optional "confirmed until" date. Without it the data can only
    point at risks:
    - Mort: "Perdu" in Notion's Reconduction, or the site archived.
    - À risque, with every reason found: end date passed (and still planned after it), no end date or a
      placeholder one (2040 and later, the "tacite 2050"), end within 3 months, a follow-up flag typed in
      Notion ("À surveiller", "à suivre", "Relancé en attente de réponse"), or simply "non confirmé".
    A typed status wins; a "Safe" whose confirmation date has passed goes back to "À risque".
    A site without an annual amount gets no status (it produces nothing in the forecast).
    """
    typed = typed or {}
    if site.lost or site.archived:
        auto, flags = "Mort", ["Perdu dans Notion" if site.lost else "Site archivé dans Notion"]
    elif site.annual <= 0:
        return "", "Pas de montant annuel dans Notion"
    else:
        auto, flags = "À risque", []
        end = site.end
        if end is None:
            flags.append("pas de date de fin")
        elif end.year >= 2040:
            flags.append(f"fin {end.year} : pas de date de fin réelle (tacite)")
        elif end < today:
            after = sum(1 for d in site.dates if d > end.isoformat())
            flags.append(f"fin dépassée le {end:%d/%m/%Y}" + (f", encore {after} passages au planning après" if after else ""))
        elif (end - today).days <= RISK_HORIZON_DAYS:
            flags.append(f"fin le {end:%d/%m/%Y}, dans moins de 3 mois")
        for flag in [f.strip() for f in site.reconduction.split(",")]:
            if flag and flag not in ("Reconduction Tacite", "Perdu"):
                flags.append(f"Notion : {flag}")
    status = str(typed.get("health") or "").strip()
    if status not in ("Safe", "À risque", "Mort"):
        return auto, " · ".join(flags) if flags else "non confirmé"
    until = _day(typed.get("confirmed_until"))
    if status == "Safe" and until is not None and until < today:
        return "À risque", " · ".join([f"confirmation expirée le {until:%d/%m/%Y}"] + flags)
    reason = "confirmé" + (f" jusqu'au {until:%d/%m/%Y}" if until else "")
    if flags and status == "Safe":
        reason += " (signaux : " + ", ".join(flags) + ")"
    return status, reason


def _day(value) -> Optional[date]:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None
