"""
Unit tests for RevenueEngine date handling (Rules 1-4).

These tests ensure revenue spreading remains consistent even when CRM date fields
are missing or inconsistent (a common source of production-year reconciliation issues).

Rules tested:
- Rule 1: Only start missing
- Rule 2: Only end missing
- Rule 3: Both dates missing
- Rule 4: Clamping allocations outside tracked window
"""

import sys
from pathlib import Path

import pandas as pd

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.processing.revenue_engine import RevenueEngine


def test_conception_does_not_require_projet_stop():
    """CONCEPTION works with start-only (no rule needed)."""
    engine = RevenueEngine(years_to_track=[2025, 2026, 2027, 2028])
    row = pd.Series(
        {
            "amount": 12000.0,
            "probability_factor": 1.0,
            "final_bu": "CONCEPTION",
            "projet_start": pd.Timestamp("2025-01-01"),
            "projet_stop": pd.NaT,  # missing in CRM
            "date": pd.Timestamp("2025-01-01"),
        }
    )

    res = engine.calculate_revenue(row)
    assert res["Montant Total 2025"] == 12000.0
    assert res["Montant Total 2026"] == 0.0
    assert res["Montant Total 2027"] == 0.0
    # All 3 months are in Q1
    assert res["Montant Total Q1_2025"] == 12000.0
    assert res["dates_rule_applied"] == False  # CONCEPTION doesn't need end


def test_travaux_partial_months_does_not_lose_amount():
    """
    Regression: day-of-month iteration can skip the last calendar month when stop.day < start.day.
    Example: 2025-10-20 -> 2025-12-19 must allocate across Oct/Nov/Dec (3 months) = 100%.
    """
    engine = RevenueEngine(years_to_track=[2025, 2026, 2027, 2028])
    row = pd.Series(
        {
            "amount": 5500.0,
            "probability_factor": 0.5,
            "final_bu": "TRAVAUX",
            "projet_start": pd.Timestamp("2025-10-20"),
            "projet_stop": pd.Timestamp("2025-12-19"),
            "date": pd.Timestamp("2025-10-31"),
        }
    )

    res = engine.calculate_revenue(row)
    assert abs(res["Montant Total 2025"] - 5500.0) < 1e-6


def test_rule1_start_missing_maintenance():
    """Rule 1: MAINTENANCE with only start missing -> use end - 11 months."""
    engine = RevenueEngine(years_to_track=[2025, 2026, 2027, 2028])
    row = pd.Series(
        {
            "amount": 12000.0,
            "probability_factor": 1.0,
            "final_bu": "MAINTENANCE",
            "projet_start": pd.NaT,  # missing
            "projet_stop": pd.Timestamp("2025-12-31"),
            "date": pd.Timestamp("2025-01-01"),
        }
    )

    res = engine.calculate_revenue(row)
    # Should span 12 months (end - 11 months to end)
    # Effective start = 2025-01-31, end = 2025-12-31
    assert res["Montant Total 2025"] == 12000.0
    assert res["dates_rule_applied"] == True
    assert "rule1_start_missing_maintenance" in res["dates_rule"]


def test_rule1_start_missing_travaux():
    """Rule 1: TRAVAUX with only start missing -> use date to end (even spread)."""
    engine = RevenueEngine(years_to_track=[2025, 2026, 2027, 2028])
    row = pd.Series(
        {
            "amount": 6000.0,
            "probability_factor": 1.0,
            "final_bu": "TRAVAUX",
            "projet_start": pd.NaT,  # missing
            "projet_stop": pd.Timestamp("2025-06-30"),
            "date": pd.Timestamp("2025-01-01"),
        }
    )

    res = engine.calculate_revenue(row)
    # Should span 6 months (Jan to Jun) = 1000€/month
    assert res["Montant Total 2025"] == 6000.0
    assert res["dates_rule_applied"] == True
    assert "rule1_start_missing_travaux" in res["dates_rule"]


def test_rule2_end_missing_maintenance():
    """Rule 2: MAINTENANCE with only end missing -> start + 11 months."""
    engine = RevenueEngine(years_to_track=[2025, 2026, 2027, 2028])
    row = pd.Series(
        {
            "amount": 12000.0,
            "probability_factor": 1.0,
            "final_bu": "MAINTENANCE",
            "projet_start": pd.Timestamp("2025-01-01"),
            "projet_stop": pd.NaT,  # missing
            "date": pd.Timestamp("2025-01-01"),
        }
    )

    res = engine.calculate_revenue(row)
    # Should span 12 months (Jan to Dec) = 1000€/month
    assert res["Montant Total 2025"] == 12000.0
    assert res["dates_rule_applied"] == True
    assert "rule2_end_missing_maintenance" in res["dates_rule"]


def test_rule2_end_missing_travaux():
    """Rule 2: TRAVAUX with only end missing -> start + 5 months."""
    engine = RevenueEngine(years_to_track=[2025, 2026, 2027, 2028])
    row = pd.Series(
        {
            "amount": 6000.0,
            "probability_factor": 1.0,
            "final_bu": "TRAVAUX",
            "projet_start": pd.Timestamp("2025-01-01"),
            "projet_stop": pd.NaT,  # missing
            "date": pd.Timestamp("2025-01-01"),
        }
    )

    res = engine.calculate_revenue(row)
    # Should span 6 months (Jan to Jun) = 1000€/month
    assert res["Montant Total 2025"] == 6000.0
    assert res["dates_rule_applied"] == True
    assert "rule2_end_missing_travaux" in res["dates_rule"]


def test_rule3_both_missing_maintenance():
    """Rule 3: MAINTENANCE with both missing -> date + 11 months."""
    engine = RevenueEngine(years_to_track=[2025, 2026, 2027, 2028])
    row = pd.Series(
        {
            "amount": 12000.0,
            "probability_factor": 1.0,
            "final_bu": "MAINTENANCE",
            "projet_start": pd.NaT,  # missing
            "projet_stop": pd.NaT,  # missing
            "date": pd.Timestamp("2025-01-01"),
        }
    )

    res = engine.calculate_revenue(row)
    # Should span 12 months (Jan to Dec) = 1000€/month
    assert res["Montant Total 2025"] == 12000.0
    assert res["dates_rule_applied"] == True
    assert "rule3_both_missing_maintenance" in res["dates_rule"]


def test_rule3_both_missing_travaux():
    """Rule 3: TRAVAUX with both missing -> date + 5 months."""
    engine = RevenueEngine(years_to_track=[2025, 2026, 2027, 2028])
    row = pd.Series(
        {
            "amount": 6000.0,
            "probability_factor": 1.0,
            "final_bu": "TRAVAUX",
            "projet_start": pd.NaT,  # missing
            "projet_stop": pd.NaT,  # missing
            "date": pd.Timestamp("2025-01-01"),
        }
    )

    res = engine.calculate_revenue(row)
    # Should span 6 months (Jan to Jun) = 1000€/month
    assert res["Montant Total 2025"] == 6000.0
    assert res["dates_rule_applied"] == True
    assert "rule3_both_missing_travaux" in res["dates_rule"]


def test_rule4_clamp_before_window():
    """Rule 4: Allocations before tracked window -> clamp to first month."""
    engine = RevenueEngine(years_to_track=[2025, 2026, 2027, 2028])
    row = pd.Series(
        {
            "amount": 12000.0,
            "probability_factor": 1.0,
            "final_bu": "MAINTENANCE",
            "projet_start": pd.Timestamp("2024-06-01"),  # Before window
            "projet_stop": pd.Timestamp("2024-12-31"),  # Before window
            "date": pd.Timestamp("2024-01-01"),
        }
    )

    res = engine.calculate_revenue(row)
    # All 7 months (Jun-Dec 2024) should be clamped to Jan 2025
    # 12000 / 7 = ~1714.29 per month, all in Jan 2025
    assert res["Montant Total 2025"] > 0.0
    assert res["Montant Total Q1_2025"] > 0.0
    # Should not have any allocation in 2024 (not tracked)
    assert res.get("Montant Total 2024", 0.0) == 0.0


def test_rule4_clamp_after_window():
    """Rule 4: Allocations after tracked window -> clamp to last month."""
    engine = RevenueEngine(years_to_track=[2025, 2026, 2027, 2028])
    row = pd.Series(
        {
            "amount": 12000.0,
            "probability_factor": 1.0,
            "final_bu": "MAINTENANCE",
            "projet_start": pd.Timestamp("2028-06-01"),  # Within window
            "projet_stop": pd.Timestamp("2030-12-31"),  # After window
            "date": pd.Timestamp("2028-01-01"),
        }
    )

    res = engine.calculate_revenue(row)
    # Allocations from 2029-2030 should be clamped to Dec 2028
    assert res["Montant Total 2028"] > 0.0
    assert res["Montant Total Q4_2028"] > 0.0
    # Should not have any allocation in 2029+ (not tracked)
    assert res.get("Montant Total 2029", 0.0) == 0.0
    assert res.get("Montant Total 2030", 0.0) == 0.0
