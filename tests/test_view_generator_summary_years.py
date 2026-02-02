"""
Regression tests for ViewGenerator summary year selection.

Problem: ViewGenerator used to instantiate RevenueEngine() with default years based on
datetime.now().year, which made BU/Typologie summaries start at the machine's current year
(e.g. 2026) even when generating a backfill sheet for 2025.
"""

import sys
from pathlib import Path
from datetime import datetime

import pandas as pd

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.processing.views import ViewGenerator


def test_view_generator_summary_includes_reference_year():
    vg = ViewGenerator(reference_date=datetime(2025, 3, 15))

    df = pd.DataFrame(
        [
            {
                "cf_bu": "CONCEPTION",
                "cf_typologie_de_devis": "Conception DV",
                "amount": 100.0,
                "Montant Total 2025": 10.0,
                "Montant Total Q1_2025": 10.0,
                "Montant Total 2026": 0.0,
                "Montant Total Q1_2026": 0.0,
            }
        ]
    )

    summary = vg._create_split_summary(df, "cf_bu", use_weighted=False)
    assert summary, "Expected non-empty BU summary"
    assert "Montant Total 2025" in summary[0], "BU summary must include reference year totals"


def test_typologie_summary_uses_primary_only():
    """Typologie summary uses primary typologie only; multi-tag row contributes to one bucket."""
    from datetime import datetime
    vg = ViewGenerator(reference_date=datetime(2025, 3, 15))

    # Row with two tags: Conception Paysage, Maintenance Animation -> primary is Conception Paysage
    df = pd.DataFrame([
        {
            "cf_bu": "CONCEPTION",
            "cf_typologie_de_devis": "Conception Paysage, Maintenance Animation",
            "amount": 100.0,
            "Montant Total 2025": 100.0,
        },
        {
            "cf_bu": "CONCEPTION",
            "cf_typologie_de_devis": "Conception DV",
            "amount": 50.0,
            "Montant Total 2025": 50.0,
        },
    ])

    summary = vg._create_split_summary(df, "cf_typologie_de_devis", use_weighted=False)
    by_type = {row["cf_typologie_de_devis"]: row["amount"] for row in summary}

    # Primary for first row is Conception Paysage (Animation demoted), so 100 goes there only
    assert by_type.get("Conception Paysage") == 100.0
    assert by_type.get("Conception DV") == 50.0
    # Maintenance Animation should not get the 100 (no double-count)
    assert by_type.get("Maintenance Animation", 0) == 0.0
