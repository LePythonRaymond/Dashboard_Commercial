"""
Unit tests for owner-specific follow-up window overrides (365 days for Vincent/Adélaïde in Notion).
"""

import pandas as pd
from datetime import datetime, timedelta
import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.processing.alerts import AlertsGenerator
from config.settings import ALERT_FOLLOWUP_DAYS_FORWARD


def test_default_window_excludes_far_future():
    """Test that default 60-day window excludes proposals far in the future."""
    reference_date = datetime(2026, 1, 15)
    generator = AlertsGenerator(reference_date=reference_date)

    # Calculate window end (60 days)
    window_end = reference_date + timedelta(days=ALERT_FOLLOWUP_DAYS_FORWARD)

    # Test case: Both date and projet_start beyond 60-day window
    # date is after window_end so it doesn't pass forward check
    # projet_start is 200 days in future, also beyond window
    row = pd.Series({
        'final_bu': 'TRAVAUX',
        'date': pd.Timestamp(2026, 4, 1),  # After 60-day window (but within backward window)
        'projet_start': pd.Timestamp(2026, 8, 1),  # 200 days in future
        'statut_clean': 'en cours',
        'alert_owner': 'regular.user'
    })

    assert not generator._needs_followup(row), "Should fail: default 60-day window excludes far future"


def test_vincent_365_day_window_includes_far_future():
    """Test that Vincent gets 365-day window and includes proposals far in the future."""
    reference_date = datetime(2026, 1, 15)
    generator = AlertsGenerator(
        reference_date=reference_date,
        followup_days_forward_by_owner={'vincent.delavarende': 365}
    )

    # Test case: projet_start 200 days in the future (within 365-day window for Vincent)
    # date is after 60-day window but projet_start is within 365-day window
    row = pd.Series({
        'final_bu': 'TRAVAUX',
        'date': pd.Timestamp(2026, 4, 1),  # After 60-day window (but within backward window)
        'projet_start': pd.Timestamp(2026, 8, 1),  # 200 days in future (within 365-day window)
        'statut_clean': 'en cours',
        'alert_owner': 'vincent.delavarende'
    })

    assert generator._needs_followup(row), "Should pass: Vincent's 365-day window includes far future"


def test_adelaide_365_day_window_includes_far_future():
    """Test that Adélaïde gets 365-day window and includes proposals far in the future."""
    reference_date = datetime(2026, 1, 15)
    generator = AlertsGenerator(
        reference_date=reference_date,
        followup_days_forward_by_owner={'adelaide.patureau': 365}
    )

    # Test case: projet_start 200 days in the future (within 365-day window for Adélaïde)
    # date is after 60-day window but projet_start is within 365-day window
    row = pd.Series({
        'final_bu': 'TRAVAUX',
        'date': pd.Timestamp(2026, 4, 1),  # After 60-day window (but within backward window)
        'projet_start': pd.Timestamp(2026, 8, 1),  # 200 days in future (within 365-day window)
        'statut_clean': 'en cours',
        'alert_owner': 'adelaide.patureau'
    })

    assert generator._needs_followup(row), "Should pass: Adélaïde's 365-day window includes far future"


def test_regular_user_still_uses_default_window():
    """Test that regular users (not Vincent/Adélaïde) still use default 60-day window."""
    reference_date = datetime(2026, 1, 15)
    generator = AlertsGenerator(
        reference_date=reference_date,
        followup_days_forward_by_owner={'vincent.delavarende': 365, 'adelaide.patureau': 365}
    )

    # Test case: same far-future proposal but with regular owner
    # Both date and projet_start beyond 60-day window
    row = pd.Series({
        'final_bu': 'TRAVAUX',
        'date': pd.Timestamp(2026, 4, 1),  # After 60-day window (but within backward window)
        'projet_start': pd.Timestamp(2026, 8, 1),  # 200 days in future
        'statut_clean': 'en cours',
        'alert_owner': 'regular.user'  # Not in override dict
    })

    assert not generator._needs_followup(row), "Should fail: regular user still uses default 60-day window"


def test_vincent_excludes_beyond_365_days():
    """Test that even Vincent's 365-day window excludes proposals beyond 365 days."""
    reference_date = datetime(2026, 1, 15)
    generator = AlertsGenerator(
        reference_date=reference_date,
        followup_days_forward_by_owner={'vincent.delavarende': 365}
    )

    # Test case: Both date and projet_start beyond 365-day window
    # date is after 365-day window so it doesn't pass forward check
    # projet_start is 400 days in future, also beyond window
    row = pd.Series({
        'final_bu': 'TRAVAUX',
        'date': pd.Timestamp(2027, 2, 1),  # After 365-day window (but within backward window)
        'projet_start': pd.Timestamp(2027, 2, 20),  # 400 days in future
        'statut_clean': 'en cours',
        'alert_owner': 'vincent.delavarende'
    })

    assert not generator._needs_followup(row), "Should fail: even 365-day window excludes beyond 365 days"


def test_conception_uses_date_field_with_owner_override():
    """Test that CONCEPTION proposals use date field, and owner override still applies."""
    reference_date = datetime(2026, 1, 15)
    generator = AlertsGenerator(
        reference_date=reference_date,
        followup_days_forward_by_owner={'vincent.delavarende': 365}
    )

    # Test case: CONCEPTION with date 200 days in future (within 365-day window for Vincent)
    row = pd.Series({
        'final_bu': 'CONCEPTION',
        'date': pd.Timestamp(2026, 8, 1),  # 200 days in future (CONCEPTION uses date)
        'projet_start': pd.Timestamp(2027, 1, 1),  # Far future (ignored for CONCEPTION)
        'statut_clean': 'en cours',
        'alert_owner': 'vincent.delavarende'
    })

    assert generator._needs_followup(row), "Should pass: CONCEPTION uses date field, Vincent's 365-day window applies"


def test_backward_window_still_applies():
    """Test that backward window check (1st of previous month) still applies regardless of owner override."""
    reference_date = datetime(2026, 1, 15)
    generator = AlertsGenerator(
        reference_date=reference_date,
        followup_days_forward_by_owner={'vincent.delavarende': 365}
    )

    # Calculate backward window start (1st of previous month)
    first_of_month = reference_date.replace(day=1)
    prev_month_end = first_of_month - timedelta(days=1)
    window_start = prev_month_end.replace(day=1)

    # Test case: date before backward window start (should fail regardless of forward window)
    row = pd.Series({
        'final_bu': 'TRAVAUX',
        'date': window_start - timedelta(days=10),  # Before backward window
        'projet_start': pd.Timestamp(2026, 8, 1),  # Within 365-day forward window
        'statut_clean': 'en cours',
        'alert_owner': 'vincent.delavarende'
    })

    assert not generator._needs_followup(row), "Should fail: backward window check still applies"


if __name__ == "__main__":
    test_default_window_excludes_far_future()
    test_vincent_365_day_window_includes_far_future()
    test_adelaide_365_day_window_includes_far_future()
    test_regular_user_still_uses_default_window()
    test_vincent_excludes_beyond_365_days()
    test_conception_uses_date_field_with_owner_override()
    test_backward_window_still_applies()
    print("✓ All owner-specific follow-up window tests passed!")
