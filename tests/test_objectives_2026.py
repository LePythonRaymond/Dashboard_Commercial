"""
Unit tests for 2026 objectives: 11-month distribution, production (signe), and signature.
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
    objective_for_year,
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
    """Test that 2026 Signé (production / Réalisé) typologie objectives match expected values."""
    # CONCEPTION
    assert objective_for_year(2026, "signe", "typologie", "Conception DV") == 50000
    assert objective_for_year(2026, "signe", "typologie", "Conception Concours") == 100000
    assert objective_for_year(2026, "signe", "typologie", "Conception Paysage") == 700000

    # TRAVAUX
    assert objective_for_year(2026, "signe", "typologie", "Travaux DV") == 1300000
    assert objective_for_year(2026, "signe", "typologie", "Travaux Conception") == 800000
    assert objective_for_year(2026, "signe", "typologie", "Travaux Direct") == 1700000

    # MAINTENANCE
    assert objective_for_year(2026, "signe", "typologie", "Maintenance Entretien") == 1250000
    assert objective_for_year(2026, "signe", "typologie", "Maintenance TS") == 300000
    assert objective_for_year(2026, "signe", "typologie", "Maintenance Animation") == 50000


def test_2026_august_zero_july_double():
    """Test that August is 0 and July is normal for all 2026 objectives."""
    metrics_to_check = ["signe", "signature"] if "signature" in OBJECTIVES[2026] else ["signe"]
    for metric in metrics_to_check:
        for dimension in ["bu", "typologie"]:
            for key in OBJECTIVES[2026][metric][dimension].keys():
                months = OBJECTIVES[2026][metric][dimension][key]
                if sum(months) == 0:
                    continue  # Skip zero objectives

                # Find normal month (use a month that's not July or August)
                normal_month = months[0] if months[0] != months[6] else months[1]

                assert months[7] == 0.0, f"{metric}/{dimension}/{key}: August should be 0, got {months[7]}"
                assert months[6] == normal_month, f"{metric}/{dimension}/{key}: July should be normal ({normal_month}), got {months[6]}"


def test_2026_has_signature_metric():
    """Test that 2026 has both signe (production) and signature (objectif signature) metrics."""
    assert 2026 in OBJECTIVES
    assert "signe" in OBJECTIVES[2026]
    assert "signature" in OBJECTIVES[2026]
    for dimension in ["bu", "typologie"]:
        assert set(OBJECTIVES[2026]["signature"][dimension].keys()) == set(
            OBJECTIVES[2026]["signe"][dimension].keys()
        ), f"2026 signature and signe should have same {dimension} keys"


def test_2026_bu_totals():
    """Test that 2026 production (signe) BU totals match expected values."""
    assert objective_for_year(2026, "signe", "bu", "CONCEPTION") == 850000
    assert objective_for_year(2026, "signe", "bu", "TRAVAUX") == 4100000
    assert objective_for_year(2026, "signe", "bu", "MAINTENANCE") == 1300000


def test_2026_signature_bu_totals():
    """Test that 2026 signature (Objectif Signature) BU totals match expected values."""
    assert objective_for_year(2026, "signature", "bu", "CONCEPTION") == 822745
    assert objective_for_year(2026, "signature", "bu", "TRAVAUX") == 4920663
    assert objective_for_year(2026, "signature", "bu", "MAINTENANCE") == 459800


def test_2026_signature_conception_typologie_prorate():
    """Test that CONCEPTION signature typologie objectives prorate to BU total 822745."""
    cv = objective_for_year(2026, "signature", "typologie", "Conception DV")
    cp = objective_for_year(2026, "signature", "typologie", "Conception Paysage")
    cc = objective_for_year(2026, "signature", "typologie", "Conception Concours")
    total = round(cv + cp + cc, 2)
    assert total == 822745, f"Conception DV+Paysage+Concours should sum to 822745, got {total}"


if __name__ == "__main__":
    test_11_month_distribution()
    test_2026_signe_typologie_values()
    test_2026_august_zero_july_double()
    test_2026_has_signature_metric()
    test_2026_bu_totals()
    test_2026_signature_bu_totals()
    test_2026_signature_conception_typologie_prorate()
    print("✓ All 2026 objectives tests passed!")
