"""
Unit tests for TRAVAUX projection 365-day rolling window on projet_start.
"""

import pandas as pd
from datetime import datetime, timedelta
import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.processing.travaux_projection import TravauxProjectionGenerator
from config.settings import TRAVAUX_PROJECTION_START_WINDOW


def test_travaux_projection_includes_projet_start_within_365_days():
    """Test that proposals with projet_start within rolling 365 days are included."""
    reference_date = datetime(2026, 1, 15)
    generator = TravauxProjectionGenerator(reference_date=reference_date)

    # Test case: projet_start 200 days in the future (within 365-day window)
    row = pd.Series({
        'final_bu': 'TRAVAUX',
        'statut_clean': 'en cours',
        'probability': 60,
        'projet_start': pd.Timestamp(2026, 8, 1),  # ~200 days from reference
        'date': pd.Timestamp(2025, 12, 1),  # Old date (should be ignored)
        'id': 'test-1',
        'title': 'Test Project',
        'company_name': 'Test Co',
        'amount': 50000,
        'assigned_to': 'test.user'
    })

    assert generator._matches_criteria(row), "Should pass: projet_start within 365-day window"


def test_travaux_projection_excludes_projet_start_beyond_365_days():
    """Test that proposals with projet_start beyond 365 days are excluded."""
    reference_date = datetime(2026, 1, 15)
    generator = TravauxProjectionGenerator(reference_date=reference_date)

    # Test case: projet_start 400 days in the future (beyond 365-day window)
    row = pd.Series({
        'final_bu': 'TRAVAUX',
        'statut_clean': 'en cours',
        'probability': 60,
        'projet_start': pd.Timestamp(2027, 2, 20),  # ~400 days from reference
        'date': pd.Timestamp(2026, 1, 1),  # Recent date (should be ignored)
        'id': 'test-2',
        'title': 'Test Project',
        'company_name': 'Test Co',
        'amount': 50000,
        'assigned_to': 'test.user'
    })

    assert not generator._matches_criteria(row), "Should fail: projet_start beyond 365-day window"


def test_travaux_projection_excludes_missing_both_dates():
    """Test that proposals with both date and projet_start missing are excluded."""
    reference_date = datetime(2026, 1, 15)
    generator = TravauxProjectionGenerator(reference_date=reference_date)

    # Test case: both date and projet_start missing
    row = pd.Series({
        'final_bu': 'TRAVAUX',
        'statut_clean': 'en cours',
        'probability': 60,
        'projet_start': pd.NaT,  # Missing
        'date': pd.NaT,  # Missing
        'id': 'test-3',
        'title': 'Test Project',
        'company_name': 'Test Co',
        'amount': 50000,
        'assigned_to': 'test.user'
    })

    assert not generator._matches_criteria(row), "Should fail: both date and projet_start missing"


def test_travaux_projection_uses_date_or_projet_start():
    """Test that the 'date' field OR 'projet_start' can be used for eligibility (OR logic)."""
    reference_date = datetime(2026, 1, 15)
    generator = TravauxProjectionGenerator(reference_date=reference_date)

    # Test case: date is within range but projet_start is missing -> should pass (OR logic)
    row = pd.Series({
        'final_bu': 'TRAVAUX',
        'statut_clean': 'en cours',
        'probability': 60,
        'projet_start': pd.NaT,  # Missing
        'date': pd.Timestamp(2026, 2, 1),  # Within 365 days
        'id': 'test-4',
        'title': 'Test Project',
        'company_name': 'Test Co',
        'amount': 50000,
        'assigned_to': 'test.user'
    })

    assert generator._matches_criteria(row), "Should pass: date field within window (OR logic)"

    # Test case: projet_start is within range but date is missing -> should pass (OR logic)
    row2 = pd.Series({
        'final_bu': 'TRAVAUX',
        'statut_clean': 'en cours',
        'probability': 60,
        'projet_start': pd.Timestamp(2026, 8, 1),  # Within 365 days
        'date': pd.NaT,  # Missing
        'id': 'test-4b',
        'title': 'Test Project',
        'company_name': 'Test Co',
        'amount': 50000,
        'assigned_to': 'test.user'
    })

    assert generator._matches_criteria(row2), "Should pass: projet_start within window (OR logic)"

    # Test case: both date and projet_start are missing -> should fail
    row3 = pd.Series({
        'final_bu': 'TRAVAUX',
        'statut_clean': 'en cours',
        'probability': 60,
        'projet_start': pd.NaT,  # Missing
        'date': pd.NaT,  # Missing
        'id': 'test-4c',
        'title': 'Test Project',
        'company_name': 'Test Co',
        'amount': 50000,
        'assigned_to': 'test.user'
    })

    assert not generator._matches_criteria(row3), "Should fail: both date and projet_start missing"


def test_travaux_projection_excludes_wrong_bu():
    """Test that non-TRAVAUX proposals are excluded."""
    reference_date = datetime(2026, 1, 15)
    generator = TravauxProjectionGenerator(reference_date=reference_date)

    row = pd.Series({
        'final_bu': 'MAINTENANCE',  # Wrong BU
        'statut_clean': 'en cours',
        'probability': 60,
        'projet_start': pd.Timestamp(2026, 8, 1),  # Within window
        'id': 'test-5',
        'title': 'Test Project',
        'company_name': 'Test Co',
        'amount': 50000,
        'assigned_to': 'test.user'
    })

    assert not generator._matches_criteria(row), "Should fail: wrong BU"


def test_travaux_projection_excludes_low_probability():
    """Test that proposals with probability < threshold are excluded."""
    from config.settings import TRAVAUX_PROJECTION_PROBABILITY_THRESHOLD
    reference_date = datetime(2026, 1, 15)
    generator = TravauxProjectionGenerator(reference_date=reference_date)

    row = pd.Series({
        'final_bu': 'TRAVAUX',
        'statut_clean': 'en cours',
        'probability': TRAVAUX_PROJECTION_PROBABILITY_THRESHOLD - 1,  # Below threshold
        'projet_start': pd.Timestamp(2026, 8, 1),  # Within window
        'id': 'test-6',
        'title': 'Test Project',
        'company_name': 'Test Co',
        'amount': 50000,
        'assigned_to': 'test.user'
    })

    assert not generator._matches_criteria(row), "Should fail: probability too low"


def test_travaux_projection_excludes_non_waiting_status():
    """Test that proposals with non-WAITING status are excluded."""
    reference_date = datetime(2026, 1, 15)
    generator = TravauxProjectionGenerator(reference_date=reference_date)

    row = pd.Series({
        'final_bu': 'TRAVAUX',
        'statut_clean': 'signé',  # Not WAITING
        'probability': 60,
        'projet_start': pd.Timestamp(2026, 8, 1),  # Within window
        'id': 'test-7',
        'title': 'Test Project',
        'company_name': 'Test Co',
        'amount': 50000,
        'assigned_to': 'test.user'
    })

    assert not generator._matches_criteria(row), "Should fail: status not WAITING"


def test_travaux_projection_boundary_today():
    """Test that projet_start exactly on today is included."""
    reference_date = datetime(2026, 1, 15)
    generator = TravauxProjectionGenerator(reference_date=reference_date)

    row = pd.Series({
        'final_bu': 'TRAVAUX',
        'statut_clean': 'en cours',
        'probability': 60,
        'projet_start': pd.Timestamp(2026, 1, 15),  # Exactly today
        'id': 'test-8',
        'title': 'Test Project',
        'company_name': 'Test Co',
        'amount': 50000,
        'assigned_to': 'test.user'
    })

    assert generator._matches_criteria(row), "Should pass: projet_start exactly on today"


def test_travaux_projection_boundary_365_days():
    """Test that projet_start exactly 365 days in the future is included."""
    reference_date = datetime(2026, 1, 15)
    generator = TravauxProjectionGenerator(reference_date=reference_date)

    # Calculate exactly 365 days from reference
    window_end = reference_date + timedelta(days=TRAVAUX_PROJECTION_START_WINDOW)

    row = pd.Series({
        'final_bu': 'TRAVAUX',
        'statut_clean': 'en cours',
        'probability': 60,
        'projet_start': pd.Timestamp(window_end),  # Exactly 365 days
        'id': 'test-9',
        'title': 'Test Project',
        'company_name': 'Test Co',
        'amount': 50000,
        'assigned_to': 'test.user'
    })

    assert generator._matches_criteria(row), "Should pass: projet_start exactly at 365-day boundary"


def test_travaux_projection_or_logic_date_within_projet_start_beyond():
    """Test OR logic: date within window but projet_start beyond -> should pass."""
    reference_date = datetime(2026, 1, 15)
    generator = TravauxProjectionGenerator(reference_date=reference_date)

    row = pd.Series({
        'final_bu': 'TRAVAUX',
        'statut_clean': 'en cours',
        'probability': 60,
        'date': pd.Timestamp(2026, 2, 1),  # Within 365-day window
        'projet_start': pd.Timestamp(2027, 6, 1),  # Beyond 365-day window
        'id': 'test-10',
        'title': 'Test Project',
        'company_name': 'Test Co',
        'amount': 50000,
        'assigned_to': 'test.user'
    })

    assert generator._matches_criteria(row), "Should pass: date within window (OR logic)"


def test_travaux_projection_or_logic_projet_start_within_date_beyond():
    """Test OR logic: projet_start within window but date beyond -> should pass."""
    reference_date = datetime(2026, 1, 15)
    generator = TravauxProjectionGenerator(reference_date=reference_date)

    row = pd.Series({
        'final_bu': 'TRAVAUX',
        'statut_clean': 'en cours',
        'probability': 60,
        'date': pd.Timestamp(2027, 6, 1),  # Beyond 365-day window
        'projet_start': pd.Timestamp(2026, 8, 1),  # Within 365-day window
        'id': 'test-11',
        'title': 'Test Project',
        'company_name': 'Test Co',
        'amount': 50000,
        'assigned_to': 'test.user'
    })

    assert generator._matches_criteria(row), "Should pass: projet_start within window (OR logic)"


if __name__ == "__main__":
    test_travaux_projection_includes_projet_start_within_365_days()
    test_travaux_projection_excludes_projet_start_beyond_365_days()
    test_travaux_projection_excludes_missing_projet_start()
    test_travaux_projection_ignores_date_field()
    test_travaux_projection_excludes_wrong_bu()
    test_travaux_projection_excludes_low_probability()
    test_travaux_projection_excludes_non_waiting_status()
    test_travaux_projection_boundary_today()
    test_travaux_projection_boundary_365_days()
    print("✓ All TRAVAUX projection window tests passed!")
