"""
Tests for 11-month accounting period logic and objectives distribution.
"""

import pytest
from src.processing.objectives import (
    generate_11_month_distribution,
    get_accounting_period_for_month,
    get_accounting_period_label,
    get_months_for_accounting_period,
    count_unique_accounting_periods,
    objective_for_quarter,
    ACCOUNTING_PERIODS
)


def test_generate_11_month_distribution_annual_total():
    """Test that 11-month distribution sums to annual total."""
    annual_total = 110000.0
    months = generate_11_month_distribution(annual_total=annual_total)

    # Should have 12 values
    assert len(months) == 12

    # Sum should equal annual total
    assert sum(months) == pytest.approx(annual_total, rel=1e-9)

    # Each month (except August) should be annual_total / 11
    normal_month = annual_total / 11.0
    for i, val in enumerate(months):
        if i == 6:  # July
            assert val == pytest.approx(normal_month, rel=1e-9)
        elif i == 7:  # August
            assert val == pytest.approx(0.0, rel=1e-9)
        else:
            assert val == pytest.approx(normal_month, rel=1e-9)


def test_generate_11_month_distribution_monthly_amount():
    """Test 11-month distribution with fixed monthly amount."""
    monthly_amount = 10000.0
    months = generate_11_month_distribution(monthly_amount=monthly_amount)

    assert len(months) == 12
    assert months[6] == pytest.approx(monthly_amount, rel=1e-9)  # July = monthly_amount (not doubled)
    assert months[7] == pytest.approx(0.0, rel=1e-9)  # August = 0

    # All other months = monthly_amount
    for i in range(12):
        if i == 7:  # August
            continue
        assert months[i] == pytest.approx(monthly_amount, rel=1e-9)


def test_accounting_period_mapping():
    """Test that month numbers map to correct accounting periods."""
    # July and August should both map to period 6 (Juil+Août)
    assert get_accounting_period_for_month(7) == 6
    assert get_accounting_period_for_month(8) == 6

    # Other months should map to unique periods
    assert get_accounting_period_for_month(1) == 0  # Janvier
    assert get_accounting_period_for_month(6) == 5   # Juin
    assert get_accounting_period_for_month(9) == 7   # Septembre
    assert get_accounting_period_for_month(12) == 10  # Décembre


def test_accounting_period_labels():
    """Test accounting period labels."""
    assert get_accounting_period_label(6) == "Juil+Août"
    assert get_accounting_period_label(0) == "Janvier"
    assert get_accounting_period_label(10) == "Décembre"


def test_months_for_accounting_period():
    """Test that accounting periods return correct month numbers."""
    # Period 6 (Juil+Août) should return [7, 8]
    months = get_months_for_accounting_period(6)
    assert set(months) == {7, 8}

    # Other periods should return single months
    assert get_months_for_accounting_period(0) == [1]  # Janvier
    assert get_months_for_accounting_period(5) == [6]  # Juin
    assert get_months_for_accounting_period(7) == [9]  # Septembre


def test_count_unique_accounting_periods():
    """Test counting unique accounting periods from month numbers."""
    # July and August should count as one period
    months = [1, 2, 7, 8, 9]
    assert count_unique_accounting_periods(months) == 4  # Jan, Fév, Juil+Août, Sep

    # All 12 months should count as 11 periods
    all_months = list(range(1, 13))
    assert count_unique_accounting_periods(all_months) == 11


def test_quarter_objective_with_11_month_accounting():
    """Test that Q3 objective correctly accounts for July+August merged period."""
    year = 2026
    metric = "signe"
    dimension = "bu"
    key = "CONCEPTION"

    # Q3 should include July, August (0), and September
    # With 11-month accounting: July = annual/11, August = 0, September = annual/11
    # So Q3 = 2 * (annual/11) = 2/11 of annual

    # Get monthly objectives
    from src.processing.objectives import objective_for_month
    july_obj = objective_for_month(year, metric, dimension, key, 7)
    aug_obj = objective_for_month(year, metric, dimension, key, 8)
    sep_obj = objective_for_month(year, metric, dimension, key, 9)

    # August should be 0
    assert aug_obj == pytest.approx(0.0, rel=1e-9)

    # July and September should be equal (both = annual/11)
    assert july_obj == pytest.approx(sep_obj, rel=1e-9)

    # Q3 objective should equal July + August (0) + September
    q3_obj = objective_for_quarter(year, metric, dimension, key, "Q3")
    # Note: objective_for_quarter() rounds to 2 decimals to avoid float drift.
    expected_q3 = round(july_obj + aug_obj + sep_obj, 2)
    assert q3_obj == pytest.approx(expected_q3, abs=0.01)
    assert q3_obj == pytest.approx(round(2 * july_obj, 2), abs=0.01)  # Since Aug = 0


def test_all_accounting_periods_defined():
    """Test that all 11 accounting periods are defined."""
    assert len(ACCOUNTING_PERIODS) == 11
    assert "Juil+Août" in ACCOUNTING_PERIODS
    assert ACCOUNTING_PERIODS[6] == "Juil+Août"
