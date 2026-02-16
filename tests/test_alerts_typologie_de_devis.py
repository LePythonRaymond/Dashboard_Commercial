"""
Unit tests: alert dicts and TRAVAUX projection dicts include cf_typologie_de_devis.
"""

import pandas as pd
from datetime import datetime
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.processing.alerts import AlertsGenerator
from src.processing.travaux_projection import TravauxProjectionGenerator
from config.settings import STATUS_WAITING


def test_weird_alert_dicts_include_cf_typologie_de_devis():
    """Weird alert dicts include cf_typologie_de_devis from source row."""
    reference_date = datetime(2026, 1, 15)
    generator = AlertsGenerator(reference_date=reference_date)

    # One row: in snapshot (waiting), weird (missing projet_start, probability 0), with typologie
    df = pd.DataFrame([{
        "id": "dev-1",
        "title": "Test",
        "company_name": "Client",
        "amount": 1000,
        "statut": "en cours",
        "statut_clean": "en cours",
        "probability": 0,
        "projet_start": pd.NaT,
        "projet_stop": pd.NaT,
        "date": pd.Timestamp("2026-01-10"),
        "signature_date": None,
        "created_at": pd.Timestamp("2026-01-01"),
        "date_effective_won": pd.NaT,
        "sign_url": "",
        "assigned_to": "user",
        "alert_owner": "user",
        "cf_typologie_de_devis": "Conception Paysage",
    }])
    df["created_at"] = pd.to_datetime(df["created_at"])
    df["date_effective_won"] = pd.to_datetime(df["date_effective_won"])

    weird = generator.generate_weird_alerts(df)
    assert weird, "expected at least one owner group"
    alerts_list = list(weird.values())[0]
    assert len(alerts_list) == 1
    assert alerts_list[0].get("cf_typologie_de_devis") == "Conception Paysage"


def test_followup_alert_dicts_include_cf_typologie_de_devis():
    """Follow-up alert dicts include cf_typologie_de_devis from source row."""
    reference_date = datetime(2026, 1, 15)
    generator = AlertsGenerator(reference_date=reference_date)

    # One row: waiting, in follow-up window (date within window), with typologie
    df = pd.DataFrame([{
        "id": "dev-2",
        "title": "Test Follow-up",
        "company_name": "Client",
        "amount": 5000,
        "statut": "en cours",
        "statut_clean": "en cours",
        "probability": 50,
        "projet_start": pd.Timestamp("2026-01-20"),
        "projet_stop": pd.Timestamp("2026-06-01"),
        "date": pd.Timestamp("2026-01-10"),
        "signature_date": None,
        "created_at": pd.Timestamp("2026-01-01"),
        "date_effective_won": pd.NaT,
        "sign_url": "",
        "assigned_to": "user",
        "alert_owner": "user",
        "final_bu": "TRAVAUX",
        "cf_typologie_de_devis": "Travaux DV",
    }])
    df["created_at"] = pd.to_datetime(df["created_at"])
    df["date_effective_won"] = pd.to_datetime(df["date_effective_won"])

    followup = generator.generate_followup_alerts(df)
    assert followup, "expected at least one owner group"
    alerts_list = list(followup.values())[0]
    assert len(alerts_list) == 1
    assert alerts_list[0].get("cf_typologie_de_devis") == "Travaux DV"


def test_followup_alert_dicts_include_cf_typologie_de_devis_when_empty():
    """Follow-up alert dicts include cf_typologie_de_devis key even when empty."""
    reference_date = datetime(2026, 1, 15)
    generator = AlertsGenerator(reference_date=reference_date)

    df = pd.DataFrame([{
        "id": "dev-3",
        "title": "No Typo",
        "company_name": "Client",
        "amount": 2000,
        "statut": "en cours",
        "statut_clean": "en cours",
        "probability": 30,
        "projet_start": pd.Timestamp("2026-01-20"),
        "projet_stop": pd.Timestamp("2026-06-01"),
        "date": pd.Timestamp("2026-01-10"),
        "signature_date": None,
        "created_at": pd.Timestamp("2026-01-01"),
        "date_effective_won": pd.NaT,
        "sign_url": "",
        "assigned_to": "user",
        "alert_owner": "user",
        "final_bu": "TRAVAUX",
        "cf_typologie_de_devis": "",
    }])
    df["created_at"] = pd.to_datetime(df["created_at"])
    df["date_effective_won"] = pd.to_datetime(df["date_effective_won"])

    followup = generator.generate_followup_alerts(df)
    assert followup
    alerts_list = list(followup.values())[0]
    assert len(alerts_list) == 1
    assert "cf_typologie_de_devis" in alerts_list[0]
    assert alerts_list[0]["cf_typologie_de_devis"] == ""


def test_travaux_projection_proposal_dicts_include_cf_typologie_de_devis():
    """TRAVAUX projection proposal dicts include cf_typologie_de_devis from source row."""
    reference_date = datetime(2026, 1, 15)
    generator = TravauxProjectionGenerator(reference_date=reference_date)

    # One row: TRAVAUX, waiting, probability >= 25, projet_start within 365 days
    df = pd.DataFrame([{
        "id": "proj-1",
        "title": "Chantier",
        "company_name": "Client",
        "amount": 100000,
        "assigned_to": "user",
        "date": pd.Timestamp("2025-12-01"),
        "projet_start": pd.Timestamp("2026-08-01"),
        "probability": 40,
        "final_bu": "TRAVAUX",
        "statut_clean": "en cours",
        "cf_typologie_de_devis": "Travaux Conception",
    }])

    proposals = generator.generate(df)
    assert len(proposals) == 1
    assert proposals[0].get("cf_typologie_de_devis") == "Travaux Conception"


def test_travaux_projection_proposal_dicts_include_cf_typologie_de_devis_when_empty():
    """TRAVAUX projection proposal dicts include cf_typologie_de_devis key even when empty."""
    reference_date = datetime(2026, 1, 15)
    generator = TravauxProjectionGenerator(reference_date=reference_date)

    df = pd.DataFrame([{
        "id": "proj-2",
        "title": "Chantier",
        "company_name": "Client",
        "amount": 80000,
        "assigned_to": "user",
        "date": pd.Timestamp("2025-12-01"),
        "projet_start": pd.Timestamp("2026-07-01"),
        "probability": 50,
        "final_bu": "TRAVAUX",
        "statut_clean": "en cours",
        "cf_typologie_de_devis": "",
    }])

    proposals = generator.generate(df)
    assert len(proposals) == 1
    assert "cf_typologie_de_devis" in proposals[0]
    assert proposals[0]["cf_typologie_de_devis"] == ""
