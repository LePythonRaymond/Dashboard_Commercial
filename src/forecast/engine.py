"""
Spreading engine of the production forecast.

The sheet shows, for every project or devis, one amount per month. Two rules produce them.

1. The default shape (``default_weights``) comes from the commercial dashboard's rules
   (Myrium ``RevenueEngine``), so both tools agree on any item nobody has touched:
   - Travaux: equal parts over the calendar months between the Furious start and end;
   - Conception: tiers by amount (1/3 a month for 3 months under 15 k€, 60 % then 40 %
     around a 6-month pause up to 30 k€, 40 % then 60 % above);
   - Maintenance devis: equal parts over 12 months.
   A pending devis whose start has passed is moved to the first open month and keeps its
   length: production cannot land in a month that is already over.

2. The redistribution (``distribute``) keeps every month somebody typed ("mois saisi").
   What remains to produce, minus the typed months, goes to the other open months in
   proportion to the default shape.

   Example: 370 000 € remain on October, November and December (equal shape). November is
   typed at 0. October and December each receive 370 000 × 1/2 = 185 000 €.

The JavaScript twin in ``apps_script/Prevision.gs`` implements ``distribute`` line for line;
``tests/forecast_vectors.json`` is checked against both.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional

import pandas as pd

from src.processing.revenue_engine import RevenueEngine

from . import months as M

EPS = 0.005  # half a cent: below this, an amount counts as zero

_ENGINE = RevenueEngine(years_to_track=[2000])  # only its spreading helpers are used


@dataclass
class Distribution:
    """Result of ``distribute`` for one row."""

    values: Dict[str, float]  # one amount per grid month
    beyond: float = 0.0  # placed after the last grid month (outside the 18-month window)
    unplaced: float = 0.0  # > 0: no open month could take it; < 0: typed months exceed what remains
    fallback: bool = False  # True when the default shape had no open month left


def distribute(
    remaining: float,
    weights: Mapping[str, float],
    pins: Mapping[str, float],
    grid: List[str],
    open_from: str,
    fallback_months: int = 2,
) -> Distribution:
    """Place ``remaining`` on the grid months.

    - ``pins``: typed months, kept as typed (only those inside the grid count).
    - Grid months before ``open_from`` are past months: they keep their typed value or 0.
    - The rest (remaining minus typed months) is shared among the open, untyped months in
      proportion to ``weights``; weights after the grid feed ``beyond``.
    - When no open untyped month has a weight (dates in the past, for instance), the rest is
      shared equally over the first ``fallback_months`` open untyped grid months.
    """
    grid = list(grid)
    if not grid:
        raise ValueError("empty grid")
    grid_set = set(grid)
    last_index = M.index(grid[-1])
    open_index = M.index(open_from)

    typed = {m: float(v) for m, v in pins.items() if m in grid_set and v is not None}
    values = {m: typed.get(m, 0.0) for m in grid}
    result = Distribution(values=values)

    to_place = float(remaining or 0.0) - sum(typed.values())
    if to_place < -EPS:
        result.unplaced = to_place
        return result
    if to_place <= EPS:
        return result

    shares: Dict[str, float] = {}
    for month, weight in weights.items():
        if not weight or weight <= 0:
            continue
        i = M.index(month)
        if i < open_index or month in typed:
            continue
        if month in grid_set or i > last_index:
            shares[month] = shares.get(month, 0.0) + float(weight)
    total = sum(shares.values())

    if total <= 0:
        free = [m for m in grid if M.index(m) >= open_index and m not in typed]
        chosen = free[: max(1, int(fallback_months))]
        if not chosen:
            result.unplaced = to_place
            return result
        shares = {m: 1.0 for m in chosen}
        total = float(len(chosen))
        result.fallback = True

    for month in sorted(shares):
        part = to_place * shares[month] / total
        if month in grid_set:
            values[month] += part
        else:
            result.beyond += part
    return result


def _timestamp(value) -> pd.Timestamp:
    if value is None or value == "":
        return pd.NaT
    return pd.to_datetime(value, errors="coerce")


def myrium_allocation(
    bu: str,
    amount: float,
    start=None,
    stop=None,
    proposal_date=None,
) -> Dict[str, float]:
    """Monthly split of ``amount`` by the dashboard's rules (dates rules 1 to 3 included).

    Returns {} when no date at all is known. No clamping to a window: months are kept as computed.
    """
    amount = float(amount or 0.0)
    if amount <= 0:
        return {}
    bu = (bu or "TRAVAUX").upper()
    start_ts, stop_ts, date_ts = _timestamp(start), _timestamp(stop), _timestamp(proposal_date)
    if pd.isna(date_ts):
        # The dashboard's date rules need the devis date as soon as one date is missing.
        # A Furious project has no devis date: its start (else its end) stands in for it.
        date_ts = start_ts if not pd.isna(start_ts) else stop_ts
    row = pd.Series(
        {
            "final_bu": bu,
            "projet_start": start_ts,
            "projet_stop": stop_ts,
            "date": date_ts,
        }
    )
    eff_start, eff_stop, _, _ = _ENGINE._compute_effective_dates(row)
    if pd.isna(eff_start):
        return {}
    if bu == "CONCEPTION":
        allocations = _ENGINE._spread_conception(amount, eff_start)
    else:
        if pd.isna(eff_stop) or eff_stop < eff_start:
            eff_stop = eff_start
        if bu == "MAINTENANCE":
            allocations = _ENGINE._spread_maintenance(amount, eff_start, eff_stop)
        else:
            allocations = _ENGINE._spread_travaux(amount, eff_start, eff_stop)
    out: Dict[str, float] = {}
    for a in allocations:
        k = M.key(a.year, a.month)
        out[k] = out.get(k, 0.0) + a.amount
    return out


def default_weights(
    bu: str,
    amount: float,
    start=None,
    stop=None,
    proposal_date=None,
    *,
    pending: bool,
    open_from: str,
    start_override: Optional[str] = None,
    stop_override: Optional[str] = None,
    pending_from: Optional[str] = None,
) -> Dict[str, float]:
    """Month weights of one item before anybody types a month.

    - Myrium's split of the amount (``myrium_allocation``).
    - A pending devis starting before ``pending_from`` (default ``open_from``) is shifted to start
      there: a devis not signed yet cannot produce before it is signed, so the forecast uses the
      month after the current one.
    - ``start_override`` (a month key) moves the whole schedule so that it starts there.
    - ``stop_override`` replaces the shape by equal parts from the start to that month.
    """
    alloc = myrium_allocation(bu, amount, start, stop, proposal_date)
    if not alloc and not (start_override or stop_override):
        return {}

    if alloc:
        first = min(alloc)
        target = None
        if start_override:
            target = start_override
        elif pending and M.index(first) < M.index(pending_from or open_from):
            target = pending_from or open_from
        if target is not None:
            shift = M.diff(first, target)
            alloc = {M.add(m, shift): v for m, v in alloc.items()}

    if stop_override:
        if start_override:
            begin = start_override
        elif alloc:
            begin = max(min(alloc), open_from, key=M.index)
        else:
            begin = open_from
        end = stop_override if M.index(stop_override) >= M.index(begin) else begin
        return {m: 1.0 for m in M.span(begin, end)}

    if not alloc:  # start override only, and no dates at all: one month at the start
        return {start_override: 1.0}
    return alloc


def effective_probability(signed: bool, factor: Optional[float], override: Optional[float]) -> float:
    """Probability used for the "probable" line.

    Signed items count fully. A typed probability wins. Otherwise Furious's, where 0 or empty
    counts as 50 % (the dashboard's rule: 0 usually means "not filled in").
    """
    if signed:
        return 1.0
    if override is not None:
        return max(0.0, min(1.0, float(override)))
    if factor is None or pd.isna(factor) or float(factor) <= 0:
        return 0.5
    return max(0.0, min(1.0, float(factor)))
