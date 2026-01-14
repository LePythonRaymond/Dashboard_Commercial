"""
Unit tests for commercial follow-up alerts with OR rule for TRAVAUX/MAINTENANCE.
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


def test_followup_travaux_or_rule():
    """Test that TRAVAUX proposals pass if either date OR projet_start is within window."""
    # Set reference date to a known date
    reference_date = datetime(2026, 1, 15)
    generator = AlertsGenerator(reference_date=reference_date)

    # Calculate window end
    window_end = reference_date + timedelta(days=ALERT_FOLLOWUP_DAYS_FORWARD)
    window_start = (reference_date.replace(day=1) - timedelta(days=1)).replace(day=1)

    # Test case 1: date within window, projet_start outside -> should pass
    row1 = pd.Series({
        'final_bu': 'TRAVAUX',
        'date': pd.Timestamp(2026, 1, 10),  # Within window
        'projet_start': pd.Timestamp(2026, 4, 1),  # Outside window (beyond window_end)
        'statut_clean': 'waiting'
    })
    assert generator._needs_followup(row1), "Should pass: date within window"

    # Test case 2: date outside window, projet_start within -> should pass
    row2 = pd.Series({
        'final_bu': 'TRAVAUX',
        'date': pd.Timestamp(2025, 11, 1),  # Before window_start
        'projet_start': pd.Timestamp(2026, 1, 20),  # Within window
        'statut_clean': 'waiting'
    })
    # Note: This will fail backward check (date < window_start), so let's use a date after window_start
    row2b = pd.Series({
        'final_bu': 'TRAVAUX',
        'date': pd.Timestamp(2026, 3, 20),  # After window_end
        'projet_start': pd.Timestamp(2026, 1, 20),  # Within window
        'statut_clean': 'waiting'
    })
    assert generator._needs_followup(row2b), "Should pass: projet_start within window"

    # Test case 3: both outside window -> should fail
    row3 = pd.Series({
        'final_bu': 'TRAVAUX',
        'date': pd.Timestamp(2026, 3, 20),  # After window_end
        'projet_start': pd.Timestamp(2026, 4, 1),  # After window_end
        'statut_clean': 'waiting'
    })
    assert not generator._needs_followup(row3), "Should fail: both outside window"

    # Test case 4: both within window -> should pass
    row4 = pd.Series({
        'final_bu': 'TRAVAUX',
        'date': pd.Timestamp(2026, 1, 10),  # Within window
        'projet_start': pd.Timestamp(2026, 1, 20),  # Within window
        'statut_clean': 'waiting'
    })
    assert generator._needs_followup(row4), "Should pass: both within window"

    # Test case 5: CONCEPTION unchanged (only uses date)
    row5 = pd.Series({
        'final_bu': 'CONCEPTION',
        'date': pd.Timestamp(2026, 1, 10),  # Within window
        'projet_start': pd.Timestamp(2026, 4, 1),  # Outside window (should be ignored)
        'statut_clean': 'waiting'
    })
    assert generator._needs_followup(row5), "CONCEPTION should pass: date within window"

    row6 = pd.Series({
        'final_bu': 'CONCEPTION',
        'date': pd.Timestamp(2026, 3, 20),  # Outside window
        'projet_start': pd.Timestamp(2026, 1, 20),  # Within window (should be ignored)
        'statut_clean': 'waiting'
    })
    assert not generator._needs_followup(row6), "CONCEPTION should fail: date outside window"


def test_followup_maintenance_or_rule():
    """Test that MAINTENANCE proposals also use OR rule."""
    reference_date = datetime(2026, 1, 15)
    generator = AlertsGenerator(reference_date=reference_date)

    # Test: date outside, projet_start within -> should pass
    row = pd.Series({
        'final_bu': 'MAINTENANCE',
        'date': pd.Timestamp(2026, 3, 20),  # After window_end
        'projet_start': pd.Timestamp(2026, 1, 20),  # Within window
        'statut_clean': 'waiting'
    })
    assert generator._needs_followup(row), "MAINTENANCE should pass: projet_start within window"


if __name__ == "__main__":
    test_followup_travaux_or_rule()
    test_followup_maintenance_or_rule()
    print("✓ All follow-up OR rule tests passed!")
