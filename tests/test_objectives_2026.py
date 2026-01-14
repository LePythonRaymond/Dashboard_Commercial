"""
Unit tests for 2026 objectives: 11-month distribution and Envoyé = Signé.
"""

import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.processing.objectives import (
    OBJECTIVES,
    generate_11_month_distribution,
    objective_for_month,
    objective_for_year
)


def test_11_month_distribution():
    """Test that 11-month distribution has August=0 and July=normal (July+August are one accounting period)."""
    # Test with annual total
    months = generate_11_month_distribution(annual_total=110000)
    normal_month = 110000 / 11.0  # 10000

    assert months[6] == normal_month, f"July should be normal ({normal_month}), got {months[6]}"
    assert months[7] == 0.0, f"August should be 0, got {months[7]}"

    # Check all other months are normal
    for i in range(12):
        if i != 6 and i != 7:
            assert months[i] == normal_month, f"Month {i+1} should be {normal_month}, got {months[i]}"

    # Test with monthly amount (TS)
    months_ts = generate_11_month_distribution(monthly_amount=137500)
    assert months_ts[6] == 137500, f"TS July should be 137500, got {months_ts[6]}"
    assert months_ts[7] == 0.0, f"TS August should be 0, got {months_ts[7]}"
    for i in range(12):
        if i != 6 and i != 7:
            assert months_ts[i] == 137500, f"TS Month {i+1} should be 137500, got {months_ts[i]}"


def test_2026_signe_typologie_values():
    """Test that 2026 Signé typologie objectives match expected values."""
    # Check CONCEPTION
    assert objective_for_year(2026, "signe", "typologie", "DV") == 50000, "DV annual should be 50000"
    assert objective_for_year(2026, "signe", "typologie", "Concours") == 100000, "Concours annual should be 100000"
    assert objective_for_year(2026, "signe", "typologie", "Paysage") == 650000, "Paysage annual should be 650000"

    # Check TRAVAUX
    assert objective_for_year(2026, "signe", "typologie", "DV(Travaux)") == 1000000, "DV(Travaux) annual should be 1000000"
    assert objective_for_year(2026, "signe", "typologie", "Travaux conception") == 500000, "Travaux conception annual should be 500000"
    assert objective_for_year(2026, "signe", "typologie", "Travaux Vincent") == 1500000, "Travaux Vincent annual should be 1500000"

    # Check MAINTENANCE
    assert objective_for_year(2026, "signe", "typologie", "Entretien") == 495000, "Entretien annual should be 495000"
    assert objective_for_year(2026, "signe", "typologie", "TS") == 137500, "TS annual should be 137500"
    assert objective_for_year(2026, "signe", "typologie", "Animation") == 50000, "Animation annual should be 50000"


def test_2026_august_zero_july_double():
    """Test that August is 0 and July is normal for all 2026 objectives."""
    for metric in ["signe", "envoye"]:
        for dimension in ["bu", "typologie"]:
            for key in OBJECTIVES[2026][metric][dimension].keys():
                months = OBJECTIVES[2026][metric][dimension][key]
                if sum(months) == 0:
                    continue  # Skip zero objectives

                # Find normal month (use a month that's not July or August)
                normal_month = months[0] if months[0] != months[6] else months[1]

                assert months[7] == 0.0, f"{metric}/{dimension}/{key}: August should be 0, got {months[7]}"
                assert months[6] == normal_month, f"{metric}/{dimension}/{key}: July should be normal ({normal_month}), got {months[6]}"


def test_2026_envoye_equals_signe():
    """Test that Envoyé objectives equal Signé objectives for 2026."""
    for dimension in ["bu", "typologie"]:
        for key in OBJECTIVES[2026]['signe'][dimension].keys():
            signe_months = OBJECTIVES[2026]['signe'][dimension][key]
            envoye_months = OBJECTIVES[2026]['envoye'][dimension][key]

            assert signe_months == envoye_months, \
                f"Envoyé should equal Signé for {dimension}/{key}, but got different values"


def test_2026_bu_totals():
    """Test that BU totals match expected values (sum of typologies)."""
    # CONCEPTION = DV + Concours + Paysage = 50k + 100k + 650k = 800k
    assert objective_for_year(2026, "signe", "bu", "CONCEPTION") == 800000, \
        "CONCEPTION BU should be 800000"

    # TRAVAUX = DV(Travaux) + Travaux conception + Travaux Vincent = 1M + 500k + 1.5M = 3M
    assert objective_for_year(2026, "signe", "bu", "TRAVAUX") == 3000000, \
        "TRAVAUX BU should be 3000000"

    # MAINTENANCE = Entretien + TS + Animation = 495k + 137.5k + 50k = 682.5k
    assert objective_for_year(2026, "signe", "bu", "MAINTENANCE") == 682500, \
        "MAINTENANCE BU should be 682500"


if __name__ == "__main__":
    test_11_month_distribution()
    test_2026_signe_typologie_values()
    test_2026_august_zero_july_double()
    test_2026_envoye_equals_signe()
    test_2026_bu_totals()
    print("✓ All 2026 objectives tests passed!")
