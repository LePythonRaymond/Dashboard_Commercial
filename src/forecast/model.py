"""
One row of a pole tab, and how its numbers are computed.

A row has defaults (what Furious, Notion or the finance gave) and overrides (what somebody
typed in the sheet: a month, a probability, a KV...). ``compute_row`` turns both into the
values shown. The same logic runs in the Apps Script for the edited row
(``apps_script/Prevision.gs``), so a change shows at once, and here every night.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import months as M
from .engine import EPS, default_weights, distribute, effective_probability

# Field ids of the editable cells (the hidden row 3 of each pole tab carries them).
EDITABLE_FIELDS = ("cat", "label", "ca", "proba", "mai", "start", "stop", "kv", "cost", "comment")


@dataclass
class Row:
    ref: str  # visible identity: P1081 (project), D252703 (devis), S12 (site), T1357 (TS), M3 (manual)
    pole: str  # "Travaux", "Conception", "Maintenance"
    category: str  # "Signé" or "Pipe"
    kind: str  # "Projet", "Devis", "Contrat", "TS", "Animation", "Manuel"
    label: str
    owner: str = ""
    typologie: str = ""
    ca: float = 0.0
    proba: Optional[float] = None  # Furious probability factor (0 to 1), pipe only
    mai: float = 0.0  # part of a chantier that belongs to maintenance
    produced: float = 0.0  # cumulative production at the last close (finance)
    start: Optional[str] = None  # month keys
    stop: Optional[str] = None
    proposal_date: Optional[str] = None
    rule: str = "TRAVAUX"  # dashboard rule used for the default shape
    kv_default: float = 1.5
    fixed: Optional[Dict[str, float]] = None  # contracts and TS: the month values themselves
    comment: str = ""
    alerts: List[str] = field(default_factory=list)
    closed: bool = False  # finance closed the project: nothing left to produce
    proposal_id: str = ""  # Furious devis that became this project
    extra: Dict[str, object] = field(default_factory=dict)  # display-only columns (maintenance portfolio)
    inactive: bool = False  # contract with nothing left to produce: listed under the active ones


@dataclass
class Shown:
    """Values displayed for a row after overrides and redistribution."""

    category: str
    label: str
    ca: float
    proba: float
    mai: float
    remaining: float
    start: Optional[str]
    stop: Optional[str]
    kv: float
    cost: float
    months: Dict[str, float]
    beyond: float
    gap: float
    pinned: List[str]  # month keys typed by hand
    overridden: List[str]  # other fields typed by hand
    alerts: List[str]
    comment: str
    weights: Dict[str, float]


def _num(value) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def compute_row(
    row: Row,
    overrides: Dict[str, object],
    grid: List[str],
    open_from: str,
    fallback_months: int = 2,
    pending_from: Optional[str] = None,
) -> Shown:
    """Apply the typed values of ``overrides`` (field id -> value) to ``row`` and spread it."""
    o = dict(overrides or {})
    alerts = list(row.alerts)
    manual = row.kind == "Manuel"

    category = row.category
    if manual and o.get("cat") in ("Signé", "Pipe"):
        category = str(o["cat"])
    label = str(o.get("label") or row.label) if manual else row.label
    ca = _num(o.get("ca")) if manual and _num(o.get("ca")) is not None else row.ca
    mai = _num(o.get("mai")) if _num(o.get("mai")) is not None else row.mai
    signed = category == "Signé"
    proba = effective_probability(signed, row.proba, _num(o.get("proba")))
    if not signed and row.proba is not None and row.proba <= 0 and _num(o.get("proba")) is None:
        alerts.append("Proba 0 % dans Furious, comptée 50 %")

    start_o = M.of(o.get("start")) if o.get("start") else None
    stop_o = M.of(o.get("stop")) if o.get("stop") else None
    pins = {k: float(v) for k, v in o.items() if M.is_key(k) and _num(v) is not None}

    if row.fixed is not None:  # contracts and TS: their months are given; dates cannot be typed on them
        weights = {m: v for m, v in row.fixed.items() if v > 0}
        remaining = sum(v for m, v in row.fixed.items() if M.index(m) >= M.index(open_from))
    else:
        weights = default_weights(  # the shape comes from the source data; typed values only move amounts and dates
            row.rule,
            row.ca if row.ca > 0 else 1.0,
            row.start,
            row.stop,
            row.proposal_date,
            pending=row.kind == "Devis",
            open_from=open_from,
            start_override=start_o,
            stop_override=stop_o,
            pending_from=pending_from,
        )
        if row.closed:
            remaining = 0.0
        elif row.kind == "Projet":
            remaining = ca - mai - row.produced
            if remaining < -EPS:
                if remaining <= -1:
                    alerts.append(f"Réalisé supérieur au CA de {-remaining:,.0f} €".replace(",", " "))
                remaining = 0.0
        elif row.kind == "Devis":
            remaining = ca - mai
        elif manual and _num(o.get("ca")) is None:
            remaining = sum(pins.values())
        else:
            remaining = ca - mai

    if abs(remaining) < EPS:
        remaining = 0.0
    dist = distribute(remaining, weights, pins, grid, open_from, fallback_months)
    if dist.fallback:
        alerts.append(f"Dates dépassées : reste réparti sur {fallback_months} mois")
    if dist.unplaced <= -1:
        alerts.append(f"Saisies supérieures au reste de {-dist.unplaced:,.0f} €".replace(",", " "))
    elif dist.unplaced >= 1:
        alerts.append(f"{dist.unplaced:,.0f} € sans mois ouvert disponible".replace(",", " "))

    kv_default = row.kv_default if row.kv_default > 0 else 1.0
    cost_o, kv_o = _num(o.get("cost")), _num(o.get("kv"))
    base_amount = max(ca - mai, 0.0)
    if cost_o is not None and cost_o > 0:
        kv = base_amount / cost_o if base_amount > 0 else kv_default
        cost = cost_o
    else:
        kv = kv_o if kv_o is not None and kv_o > 0 else kv_default
        cost = base_amount / kv

    shown_start = start_o or row.start
    shown_stop = stop_o or row.stop
    gap = remaining - sum(dist.values.values()) - dist.beyond
    overridden = [f for f in EDITABLE_FIELDS if f in o and f != "comment" and o.get(f) not in (None, "")]
    return Shown(
        category=category,
        label=label,
        ca=ca,
        proba=proba,
        mai=mai,
        remaining=remaining,
        start=shown_start,
        stop=shown_stop,
        kv=kv,
        cost=cost,
        months=dist.values,
        beyond=dist.beyond,
        gap=gap if abs(gap) > EPS else 0.0,
        pinned=sorted(k for k in pins if k in set(grid)),
        overridden=overridden,
        alerts=alerts,
        comment=str(o.get("comment", row.comment) or ""),
        weights=weights,
    )
