"""Maintenance contracts month by month (src/forecast/maintenance.py)."""

from datetime import date

import pytest

from src.forecast.maintenance import Site, active_months, monthly_values, passages_by_site, passages_in_year, year_value


def test_year_value_matches_the_notion_column_on_real_sites():
    # Values Notion computed in June 2026 (copy of the base pasted in Arthur's workbook).
    stanwell = Site("a", "Stanwell", 800, date(2026, 6, 22), date(2027, 6, 22))
    assert year_value(stanwell, 2026) == pytest.approx(466.6667, abs=1e-3)
    degre = Site("b", "Degré Celsius", 1513.4, date(2026, 10, 8), date(2027, 3, 31))
    assert year_value(degre, 2026) == pytest.approx(378.35)
    savills = Site("c", "HQ Savills", 3090, date(2024, 11, 15), date(2026, 1, 22), lost=True)
    assert year_value(savills, 2026) == pytest.approx(257.5)
    felicite = Site("d", "La Félicité", 650.9, date(2026, 5, 4), date(2026, 12, 31))
    assert year_value(felicite, 2026) == pytest.approx(650.9)  # the column counts it in full


def test_future_start_counts_nothing_in_earlier_years():
    lafi = Site("e", "Lafi", 3325, date(2027, 2, 1), date(2027, 2, 1))
    assert year_value(lafi, 2026) == 0
    # Start and end in the same year, not lost: the Notion column counts the full amount.
    assert year_value(lafi, 2027) == pytest.approx(3325)
    assert active_months(lafi, 2027) == list(range(2, 13))


def test_lost_site_stops_at_its_end():
    site = Site("f", "x", 1200, date(2025, 1, 1), date(2026, 3, 15), lost=True)
    assert active_months(site, 2026) == [1, 2, 3]
    assert monthly_values(site, [2027]) == {}


def test_split_follows_passages_and_keeps_the_year_value():
    site = Site("g", "x", 12000, date(2024, 1, 1), None, passages={"2026-06": 4, "2026-12": 1})
    values = monthly_values(site, [2026])
    assert values == {"2026-06": pytest.approx(9600), "2026-12": pytest.approx(2400)}


def test_unplanned_year_borrows_the_latest_planned_months():
    site = Site("h", "x", 1200, date(2024, 1, 1), None, passages={"2026-03": 1, "2026-09": 3})
    values = monthly_values(site, [2027])
    assert values == {"2027-03": pytest.approx(300), "2027-09": pytest.approx(900)}


def test_no_planning_at_all_splits_equally_over_contract_months():
    site = Site("i", "x", 1200, date(2026, 10, 1), None)
    values = monthly_values(site, [2026])
    assert values == {"2026-10": pytest.approx(100), "2026-11": pytest.approx(100), "2026-12": pytest.approx(100)}


def test_passages_counted_once_per_site_and_day():
    pages = [
        {"properties": {"Date": {"date": {"start": "2026-10-05"}}, "Site": {"relation": [{"id": "s-1"}]}}},
        {"properties": {"Date": {"date": {"start": "2026-10-05"}}, "Site": {"relation": [{"id": "s-1"}]}}},
        {"properties": {"Date": {"date": {"start": "2026-10-12"}}, "Site": {"relation": [{"id": "s-1"}]}}},
        {"properties": {"Date": {"date": None}, "Site": {"relation": [{"id": "s-1"}]}}},
        {"properties": {"Date": {"date": {"start": "2026-10-12"}}, "Site": {"relation": []}}},
    ]
    assert passages_by_site(pages) == {"s1": {"2026-10": 2}}


def test_a_stray_passage_next_january_does_not_take_the_whole_year():
    passages = {f"2026-{m:02d}": 4 for m in range(1, 13)}
    passages["2027-01"] = 1  # weekly schedule crossing the year
    site = Site("j", "x", 12000, date(2024, 1, 1), None, passages=passages)
    values = monthly_values(site, [2027])
    assert len(values) == 12 and values["2027-01"] == pytest.approx(1000)


def test_a_complete_next_year_planning_is_used():
    passages = {f"2026-{m:02d}": 1 for m in range(1, 13)}
    passages.update({"2027-06": 6, "2027-07": 6})  # 2027 already generated, summer only
    site = Site("k", "x", 1200, date(2024, 1, 1), None, passages=passages)
    assert monthly_values(site, [2027]) == {"2027-06": pytest.approx(600), "2027-07": pytest.approx(600)}


def test_passages_planned_and_done_count_only_contract_months():
    site = Site("l", "x", 1200, date(2026, 3, 1), None,
                dates=["2026-01-12", "2026-03-09", "2026-06-15", "2026-10-05", "2026-10-30", "2027-01-04"])
    assert passages_in_year(site, 2026) == 4  # January is before the contract
    assert passages_in_year(site, 2026, before=date(2026, 10, 26)) == 3


def test_offered_passages_are_skipped_once_notion_carries_the_mark():
    pages = [
        {"properties": {"Date": {"date": {"start": "2026-11-02"}}, "Site": {"relation": [{"id": "s-2"}]}, "Offert": {"checkbox": True}}},
        {"properties": {"Date": {"date": {"start": "2026-11-09"}}, "Site": {"relation": [{"id": "s-2"}]}, "Offert": {"checkbox": False}}},
    ]
    assert passages_by_site(pages) == {"s2": {"2026-11": 1}}
