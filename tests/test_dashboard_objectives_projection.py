"""
Unit tests for dashboard objective projection helpers.

Tests projection math: remaining months (excl. Aug), to_produce_per_month,
and projection total = cumulative + average * remaining_count.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.dashboard.app import (
    get_remaining_months_excl_aug,
    get_months_range,
    compute_projection_and_objective,
)


def test_get_remaining_months_excl_aug():
    """Remaining months exclude August; count matches list length."""
    # March (3): Apr, May, Jun, Jul, Sep, Oct, Nov, Dec = 8
    lst, count = get_remaining_months_excl_aug(3)
    assert 8 not in lst
    assert len(lst) == 8
    assert count == 8
    assert lst == [4, 5, 6, 7, 9, 10, 11, 12]

    # July (7): remaining = Aug..Dec excluding Aug -> [9, 10, 11, 12] = 4 months
    lst, count = get_remaining_months_excl_aug(7)
    assert 8 not in lst
    assert lst == [9, 10, 11, 12]
    assert count == 4

    # December: no remaining
    lst, count = get_remaining_months_excl_aug(12)
    assert lst == []
    assert count == 0


def test_get_months_range():
    """Months from m0 to m_now inclusive."""
    assert get_months_range(1, 3) == [1, 2, 3]
    assert get_months_range(5, 5) == [5]
    assert get_months_range(3, 1) == []
    assert get_months_range(10, 12) == [10, 11, 12]


def test_compute_projection_to_produce_per_month():
    """to_produce_per_month = (objective - cumulative) / remaining_count when remaining_count > 0."""
    import pandas as pd
    from src.processing.objectives import OBJECTIVES

    year = 2026
    if year not in OBJECTIVES or "signe" not in OBJECTIVES[year]:
        return  # skip if no 2026 signe data

    # Empty df: cumulative 0, so to_produce = objective / remaining_count
    df = pd.DataFrame(columns=["source_sheet", "cf_bu", "Montant Total Q1_2026", "Montant Total Q2_2026", "Montant Total Q3_2026", "Montant Total Q4_2026", "Montant Total 2026"])
    rec = compute_projection_and_objective(
        df, year, start_month=1, m_now=3, dimension="bu", key="CONCEPTION",
        use_pur=False, use_pondere=False, metric_key="signe", has_signature_objective=True,
    )
    remaining_list, remaining_count = get_remaining_months_excl_aug(3)
    assert remaining_count == 8
    expected_to_produce = rec["objective"] / 8
    assert abs(rec["to_produce_per_month"] - expected_to_produce) < 1.0
    assert rec["cumulative_so_far"] == 0.0
    assert rec["projected_total"] == 0.0  # 0 + 0 * 8


def test_projection_math_cumulative_plus_average_times_remaining():
    """projected_total = cumulative_so_far + average_per_month * remaining_count."""
    import pandas as pd

    df = pd.DataFrame(columns=["source_sheet", "cf_bu", "signed_year", "Montant Total Q1_2026", "Montant Total Q2_2026", "Montant Total Q3_2026", "Montant Total Q4_2026", "Montant Total 2026"])
    rec = compute_projection_and_objective(
        df, 2026, start_month=1, m_now=2, dimension="bu", key="TRAVAUX",
        use_pur=False, use_pondere=False, metric_key="signe", has_signature_objective=True,
    )
    # cumulative 0, average 0 -> projected 0
    assert rec["projected_total"] == rec["cumulative_so_far"] + rec["average_per_month"] * rec["remaining_count"]
