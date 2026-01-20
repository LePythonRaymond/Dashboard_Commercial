"""
Tests for production-month coherence and carryover distribution logic used in Objectifs (dashboard).

We import the helper from the dashboard module and validate that:
- Production-month amounts sum correctly to quarter amounts (Jan+Feb+Mar = Q1)
- Carryover is evenly distributed across quarter months
- Pure signature calculations work correctly
"""

import pandas as pd
import pytest


def test_production_month_coherence_q1():
    """Test that Jan + Feb + Mar = Q1 for production-month calculations."""
    from src.dashboard.app import calculate_production_month_with_carryover

    production_year = 2026
    df = pd.DataFrame(
        [
            {
                "signed_year": 2025,
                "cf_bu": "TRAVAUX",
                f"Montant Total Q1_{production_year}": 90000.0,
            },
            {
                "signed_year": 2026,
                "cf_bu": "TRAVAUX",
                f"Montant Total Q1_{production_year}": 10000.0,
            },
        ]
    )

    # Calculate each month
    jan_total, jan_prev = calculate_production_month_with_carryover(
        df, production_year, 1, "bu", "TRAVAUX", False
    )
    feb_total, feb_prev = calculate_production_month_with_carryover(
        df, production_year, 2, "bu", "TRAVAUX", False
    )
    mar_total, mar_prev = calculate_production_month_with_carryover(
        df, production_year, 3, "bu", "TRAVAUX", False
    )

    # Each month should be Q1 / 3
    # Total Q1 = 90k (prev) + 10k (current) = 100k
    # Each month = 100k / 3 = 33,333.33...
    expected_month = 100000.0 / 3.0
    assert jan_total == pytest.approx(expected_month, abs=0.01)
    assert feb_total == pytest.approx(expected_month, abs=0.01)
    assert mar_total == pytest.approx(expected_month, abs=0.01)

    # Sum of months should equal Q1 total
    quarter_total = jan_total + feb_total + mar_total
    assert quarter_total == pytest.approx(100000.0, abs=0.01)

    # Carryover: 90k / 3 = 30k per month
    expected_carryover = 90000.0 / 3.0
    assert jan_prev == pytest.approx(expected_carryover, abs=0.01)
    assert feb_prev == pytest.approx(expected_carryover, abs=0.01)
    assert mar_prev == pytest.approx(expected_carryover, abs=0.01)


def test_production_period_juil_aout():
    """Test that Juil+Août period = 2/3 of Q3."""
    from src.dashboard.app import calculate_production_period_with_carryover

    production_year = 2026
    df = pd.DataFrame(
        [
            {
                "signed_year": 2025,
                "cf_bu": "CONCEPTION",
                f"Montant Total Q3_{production_year}": 60000.0,
            },
        ]
    )

    # Period 6 = Juil+Août (months 7, 8)
    period_total, period_prev = calculate_production_period_with_carryover(
        df, production_year, 6, "bu", "CONCEPTION", False
    )

    # Q3 = 60k, so each month = 60k / 3 = 20k
    # Juil+Août = 2 months = 2 * 20k = 40k
    expected_period = 60000.0 * 2.0 / 3.0
    assert period_total == pytest.approx(expected_period, abs=0.01)
    assert period_prev == pytest.approx(expected_period, abs=0.01)


def test_pure_signature_month():
    """Test pure signature calculation for a signing month."""
    from src.dashboard.app import calculate_pure_signature_for_month

    signed_year = 2026
    df = pd.DataFrame(
        [
            {
                "signed_year": 2026,
                "source_sheet": "Signé Janvier 2026",
                "cf_bu": "TRAVAUX",
                "amount": 50000.0,
                "amount_pondere": 30000.0,
            },
            {
                "signed_year": 2026,
                "source_sheet": "Signé Janvier 2026",
                "cf_bu": "TRAVAUX",
                "amount": 30000.0,
                "amount_pondere": 20000.0,
            },
            {
                "signed_year": 2025,  # Should be filtered out
                "source_sheet": "Signé Janvier 2025",
                "cf_bu": "TRAVAUX",
                "amount": 100000.0,
            },
        ]
    )

    brut, pondere = calculate_pure_signature_for_month(
        df, signed_year, 1, "bu", "TRAVAUX", use_pondere=True
    )

    # Should only include 2026 rows
    assert brut == pytest.approx(80000.0, abs=0.01)  # 50k + 30k
    assert pondere == pytest.approx(50000.0, abs=0.01)  # 30k + 20k


def test_pure_signature_quarter():
    """Test pure signature calculation for a signing quarter."""
    from src.dashboard.app import calculate_pure_signature_for_quarter

    signed_year = 2026
    df = pd.DataFrame(
        [
            {
                "signed_year": 2026,
                "source_sheet": "Signé Janvier 2026",
                "cf_bu": "TRAVAUX",
                "amount": 10000.0,
            },
            {
                "signed_year": 2026,
                "source_sheet": "Signé Fevrier 2026",  # Note: MONTH_MAP uses "Fevrier" (no accent)
                "cf_bu": "TRAVAUX",
                "amount": 20000.0,
            },
            {
                "signed_year": 2026,
                "source_sheet": "Signé Mars 2026",
                "cf_bu": "TRAVAUX",
                "amount": 30000.0,
            },
        ]
    )

    brut, pondere = calculate_pure_signature_for_quarter(
        df, signed_year, "Q1", "bu", "TRAVAUX", use_pondere=False
    )

    assert brut == pytest.approx(60000.0, abs=0.01)  # 10k + 20k + 30k
    assert pondere == pytest.approx(0.0, abs=0.01)  # use_pondere=False


def test_pure_signature_year():
    """Test pure signature calculation for a signing year."""
    from src.dashboard.app import calculate_pure_signature_for_year

    signed_year = 2026
    df = pd.DataFrame(
        [
            {
                "signed_year": 2026,
                "source_sheet": "Signé Janvier 2026",
                "cf_bu": "CONCEPTION",
                "amount": 50000.0,
            },
            {
                "signed_year": 2026,
                "source_sheet": "Signé Juin 2026",
                "cf_bu": "CONCEPTION",
                "amount": 30000.0,
            },
            {
                "signed_year": 2025,  # Should be filtered out
                "source_sheet": "Signé Décembre 2025",
                "cf_bu": "CONCEPTION",
                "amount": 100000.0,
            },
        ]
    )

    brut, pondere = calculate_pure_signature_for_year(
        df, signed_year, "bu", "CONCEPTION", use_pondere=False
    )

    # Should only include 2026 rows
    assert brut == pytest.approx(80000.0, abs=0.01)  # 50k + 30k
    assert pondere == pytest.approx(0.0, abs=0.01)  # use_pondere=False
