"""
Tests for carryover distribution logic used in Objectifs (dashboard).

We import the helper from the dashboard module and validate that previous-year
production-quarter amounts are evenly distributed across the quarter's months.
"""

import pandas as pd


def test_carryover_distribution_q1_even_split():
    # Import here to avoid any import-time side effects during collection
    from src.dashboard.app import calculate_production_period_with_carryover_distribution

    production_year = 2026

    # One carryover row (signed in 2025) producing 90k in Q1 2026
    # One current-year row (signed in Jan 2026) producing 10k in 2026
    df = pd.DataFrame(
        [
            {
                "signed_year": 2025,
                "source_sheet": "Signé Décembre 2025",
                "cf_bu": "TRAVAUX",
                "cf_typologie_de_devis": "DV(Travaux)",
                f"Montant Total Q1_{production_year}": 90000.0,
                f"Montant Total {production_year}": 90000.0,
            },
            {
                "signed_year": 2026,
                "source_sheet": "Signé Janvier 2026",
                "cf_bu": "TRAVAUX",
                "cf_typologie_de_devis": "DV(Travaux)",
                f"Montant Total Q1_{production_year}": 10000.0,
                f"Montant Total {production_year}": 10000.0,
            },
        ]
    )

    # Accounting period index 0 == Janvier
    total, carryover = calculate_production_period_with_carryover_distribution(
        df=df,
        production_year=production_year,
        period_idx=0,
        dimension="bu",
        key="TRAVAUX",
        use_pondere=False,
    )

    # Carryover should be evenly split across Jan/Feb/Mar -> 90k / 3 = 30k in Jan
    assert round(carryover, 2) == 30000.0

    # Total = current-year Jan row (10k) + carryover Jan share (30k)
    assert round(total, 2) == 40000.0
