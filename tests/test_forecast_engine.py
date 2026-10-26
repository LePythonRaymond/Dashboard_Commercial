"""Tests of the forecast spreading engine (src/forecast/engine.py) and month keys."""

import json
from pathlib import Path

import pytest

from src.forecast import months as M
from src.forecast.engine import (
    default_weights,
    distribute,
    effective_probability,
    myrium_allocation,
)

VECTORS = json.loads((Path(__file__).parent / "forecast_vectors.json").read_text())["cases"]


@pytest.mark.parametrize("case", VECTORS, ids=[c["name"] for c in VECTORS])
def test_distribute_vectors(case):
    got = distribute(
        case["remaining"], case["weights"], case["pins"], case["grid"], case["open_from"], case["fallback_months"]
    )
    exp = case["expect"]
    assert set(got.values) == set(exp["values"])
    for month, value in exp["values"].items():
        assert got.values[month] == pytest.approx(value, abs=1e-3), month
    assert got.beyond == pytest.approx(exp["beyond"], abs=1e-3)
    assert got.unplaced == pytest.approx(exp["unplaced"], abs=1e-3)
    assert got.fallback is exp["fallback"]


def test_distribute_conserves_the_amount_when_it_fits():
    weights = {"2026-10": 3, "2026-11": 1, "2027-06": 2}
    got = distribute(100000, weights, {"2026-11": 10000}, ["2026-10", "2026-11", "2026-12"], "2026-10")
    assert sum(got.values.values()) + got.beyond == pytest.approx(100000)
    assert got.values["2026-11"] == 10000  # typed month untouched


def test_months_helpers():
    assert M.add("2026-11", 3) == "2027-02"
    assert M.add("2027-01", -1) == "2026-12"
    assert M.diff("2026-10", "2028-03") == 17
    assert M.span("2026-11", "2027-01") == ["2026-11", "2026-12", "2027-01"]
    assert M.span("2027-01", "2026-11") == []
    assert M.of("2026-10-08") == "2026-10"
    assert M.of(None) is None and M.of("NaT") is None
    assert M.label("2026-08") == "août 2026"
    with pytest.raises(ValueError):
        M.parse("2026-13")


def test_travaux_allocation_is_the_dashboard_rule():
    # Farman, devis 252703: 410 000 € dated 1 Sept to 30 Oct in Furious -> 205 000 € twice
    assert myrium_allocation("TRAVAUX", 410000, "2026-09-01", "2026-10-30") == {
        "2026-09": 205000.0,
        "2026-10": 205000.0,
    }


def test_conception_allocation_tiers():
    alloc = myrium_allocation("CONCEPTION", 20000, "2026-10-15", None)
    assert alloc["2026-10"] == pytest.approx(2000)
    assert "2027-04" not in alloc  # 6-month pause
    assert alloc["2027-10"] == pytest.approx(20000 * 0.4 / 6)
    assert sum(alloc.values()) == pytest.approx(20000)


def test_pending_devis_moved_out_of_closed_months():
    # Maintenance devis dated January 2026, still pending in October: 12 months from October
    w = default_weights("MAINTENANCE", 12000, "2026-01-01", "2026-12-31", pending=True, open_from="2026-10")
    assert min(w) == "2026-10" and max(w) == "2027-09" and len(w) == 12


def test_signed_project_keeps_its_dates():
    w = default_weights("TRAVAUX", 300000, "2026-06-01", "2026-12-31", pending=False, open_from="2026-10")
    assert min(w) == "2026-06"  # past months are dropped later, by distribute()
    got = distribute(120000, w, {}, M.span("2026-10", "2027-03"), "2026-10")
    assert got.values["2026-10"] == pytest.approx(40000)
    assert got.values["2027-01"] == 0


def test_overrides():
    start_moved = default_weights(
        "CONCEPTION", 10000, "2026-01-10", None, pending=True, open_from="2026-10", start_override="2027-02"
    )
    assert sorted(start_moved) == ["2027-02", "2027-03", "2027-04"]
    end_set = default_weights(
        "TRAVAUX", 50000, "2026-03-01", "2026-04-30", pending=False, open_from="2026-10", stop_override="2027-01"
    )
    assert sorted(end_set) == ["2026-10", "2026-11", "2026-12", "2027-01"]


def test_effective_probability():
    assert effective_probability(True, 0.1, None) == 1.0
    assert effective_probability(False, 0.0, None) == 0.5  # 0 % counts as 50 %, like the dashboard
    assert effective_probability(False, 0.25, None) == 0.25
    assert effective_probability(False, 0.25, 0.8) == 0.8


def test_a_pending_devis_starts_next_month_at_the_earliest():
    # On 26 October a devis not signed yet cannot produce in October: its schedule starts in November.
    w = default_weights("TRAVAUX", 30000, "2026-09-01", "2026-11-30", pending=True, open_from="2026-10",
                        pending_from="2026-11")
    assert sorted(w) == ["2026-11", "2026-12", "2027-01"]
    signed = default_weights("TRAVAUX", 30000, "2026-09-01", "2026-11-30", pending=False, open_from="2026-10",
                             pending_from="2026-11")
    assert min(signed) == "2026-09"  # a signed project keeps its dates


def test_a_contract_shows_its_own_value_in_a_past_month_not_closed_yet():
    from src.forecast.model import Row, compute_row

    grid = M.span("2026-09", "2027-02")  # August closed, today in October
    contract = Row(ref="S1", pole="Maintenance", category="Signé", kind="Contrat", label="x", ca=12000,
                   fixed={"2026-09": 1000.0, "2026-10": 1000.0, "2026-11": 0.0, "2026-12": 2000.0})
    shown = compute_row(contract, {}, grid, "2026-10")
    assert shown.months["2026-09"] == 1000 and shown.months["2026-12"] == 2000
    assert shown.remaining == 4000 and shown.gap == 0
    assert shown.pinned == []  # not typed: no yellow
    # A typed September wins and the open months keep their own values.
    typed = compute_row(contract, {"2026-09": 400.0}, grid, "2026-10")
    assert typed.months["2026-09"] == 400 and typed.months["2026-10"] == pytest.approx(1000 + 600 / 3)
    assert typed.pinned == ["2026-09"]


def test_a_project_shows_only_typed_values_in_a_past_month_not_closed_yet():
    from src.forecast.model import Row, compute_row

    grid = M.span("2026-09", "2027-02")
    project = Row(ref="P1", pole="Travaux", category="Signé", kind="Projet", label="x", ca=90000, produced=30000,
                  start="2026-09", stop="2026-11", rule="TRAVAUX")
    shown = compute_row(project, {"2026-09": 20000.0}, grid, "2026-10")
    assert shown.months["2026-09"] == 20000
    assert shown.months["2026-10"] == pytest.approx(20000) and shown.months["2026-11"] == pytest.approx(20000)
