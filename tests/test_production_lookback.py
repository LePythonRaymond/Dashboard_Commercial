"""
Production-year aggregation must search every signing year whose sheets can
carry that production year.

A sheet written in year Y holds production columns Y..Y+REVENUE_YEARS_AHEAD. A
CONCEPTION above 30k lasts 30 months, so a devis signed in November 2025 still
produces in 2028: "À produire 2028" must read the 2025 sheets. The dashboard and
the objectives email used to stop at Y-2 and missed it.
"""

import sys
from pathlib import Path

import pandas as pd

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.processing.revenue_engine import RevenueEngine


def _conception_signed_nov_2025() -> dict:
    """A 34 850€ CONCEPTION signed and started in November 2025, as the 2025 pipeline wrote it."""
    row = {
        "id": "1",
        "title": "Conception signée en 2025",
        "cf_bu": "CONCEPTION",
        "final_bu": "CONCEPTION",
        "amount": 34850.0,
        "probability": 100.0,
        "date": "2025-11-14",
        "projet_start": "2025-11-14",
        "source_sheet": "Signé Novembre 2025",
    }
    row.update(RevenueEngine(years_to_track=[2025, 2026, 2027, 2028]).process_single_row(row))
    return row


def test_engine_spreads_a_2025_conception_into_2028():
    row = _conception_signed_nov_2025()
    # Phase 2 runs May 2027 to April 2028: 4 months x 34850 x 60% / 12
    assert abs(row["Montant Total 2028"] - 6970.0) < 1e-6


def test_dashboard_production_data_reads_signing_year_minus_3(monkeypatch):
    import src.dashboard.app as app

    sheets = {2025: pd.DataFrame([_conception_signed_nov_2025()])}
    monkeypatch.setattr(app, "load_year_data", lambda year, sheet_type: sheets.get(year, pd.DataFrame()))

    df = app.load_aggregated_production_data(2028, "Signé")

    assert len(df) == 1
    assert int(df["signed_year"].iloc[0]) == 2025
    assert abs(float(df["Montant Total 2028"].sum()) - 6970.0) < 1e-6


def test_objectives_email_production_data_reads_signing_year_minus_3(monkeypatch):
    import src.integrations.google_sheets as google_sheets
    from src.integrations.email_sender import EmailSender

    sheets = {2025: pd.DataFrame([_conception_signed_nov_2025()])}

    class FakeSheetsClient:
        def get_worksheets_by_pattern(self, pattern, view_type=None, year=None):
            return ["Signé Novembre 2025"] if year in sheets else []

        def read_worksheet(self, name, view_type=None, year=None):
            return sheets[year]

    monkeypatch.setattr(google_sheets, "GoogleSheetsClient", FakeSheetsClient)

    df = EmailSender(test_mode=True)._load_aggregated_production_data_for_objectives(
        2028, view_type="signe", pattern="Signé"
    )

    assert len(df) == 1
    assert int(df["signed_year"].iloc[0]) == 2025
    assert abs(float(df["Montant Total 2028"].sum()) - 6970.0) < 1e-6
