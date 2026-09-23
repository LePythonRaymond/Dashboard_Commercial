"""Tests for the Budget {Y} workbook builder."""

from datetime import date
from io import BytesIO

import pandas as pd
import pytest

from src.integrations.budget_export import (
    LEGEND_TEXT_TEMPLATE,
    _compute_bu_amounts,
    _sum_production_by_bu,
    drop_stale_sent_carryover,
    build_budget_workbook,
)


@pytest.fixture
def sample_df() -> pd.DataFrame:
    """
    Minimal processed-style DataFrame with one row per (BU, status) combination
    plus two extras to test signature-year filtering on the Maintenance sheet.

    Columns chosen to match what `read_worksheet` returns + what RevenueEngine adds.
    """
    return pd.DataFrame([
        # CONCEPTION won (Signé) — contributes to signes & envoyes
        {
            "id_devis": "C-W-1", "title": "Concept Won 1",
            "statut_clean": "gagné", "final_bu": "CONCEPTION", "amount": 100.0,
            "Montant Total 2026": 90.0, "Montant Pondéré 2026": 81.0,
            "signature_date": "2026-03-15", "date": "2026-03-15", "projet_start": "2026-04-01",
        },
        # CONCEPTION waiting (Envoyé) — contributes to potentiels & envoyes
        {
            "id_devis": "C-WT-1", "title": "Concept Waiting 1",
            "statut_clean": "en cours", "final_bu": "CONCEPTION", "amount": 200.0,
            "Montant Total 2026": 180.0, "Montant Pondéré 2026": 90.0,
            "signature_date": None, "date": "2026-02-01", "projet_start": "2026-05-01",
        },
        # TRAVAUX won
        {
            "id_devis": "T-W-1", "title": "Travaux Won 1",
            "statut_clean": "signé", "final_bu": "TRAVAUX", "amount": 1000.0,
            "Montant Total 2026": 950.0, "Montant Pondéré 2026": 950.0,
            "signature_date": "2026-01-10", "date": "2026-01-10", "projet_start": "2026-02-01",
        },
        # TRAVAUX waiting
        {
            "id_devis": "T-WT-1", "title": "Travaux Waiting 1",
            "statut_clean": "envoyée(s) en attente de réponse",
            "final_bu": "TRAVAUX", "amount": 500.0,
            "Montant Total 2026": 500.0, "Montant Pondéré 2026": 250.0,
            "signature_date": None, "date": "2026-04-01", "projet_start": "2026-05-01",
        },
        # MAINTENANCE won this year — entry on Maintenance sheet
        {
            "id_devis": "M-W-1", "title": "Maintenance Won 1",
            "statut_clean": "gagné", "final_bu": "MAINTENANCE", "amount": 7088.10,
            "Montant Total 2026": 7088 / 12 * 10, "Montant Pondéré 2026": 7088 / 12 * 10,
            "signature_date": "2026-03-20", "date": "2026-03-20", "projet_start": "2026-03-15",
        },
        # MAINTENANCE waiting
        {
            "id_devis": "M-WT-1", "title": "Maintenance Waiting 1",
            "statut_clean": "brief", "final_bu": "MAINTENANCE", "amount": 2000.0,
            "Montant Total 2026": 2000.0, "Montant Pondéré 2026": 1000.0,
            "signature_date": None, "date": "2026-06-01", "projet_start": "2026-07-01",
        },
        # MAINTENANCE won but in PREVIOUS year — must be excluded from maintenance entries
        {
            "id_devis": "M-W-OLD", "title": "Maintenance Won Last Year",
            "statut_clean": "gagné", "final_bu": "MAINTENANCE", "amount": 4444.0,
            "Montant Total 2026": 0.0, "Montant Pondéré 2026": 0.0,
            "signature_date": "2025-11-01", "date": "2025-11-01", "projet_start": "2025-12-01",
        },
    ])


# ---------------------------------------------------------------------------
# _compute_bu_amounts
# ---------------------------------------------------------------------------

def test_compute_bu_amounts_signes_uses_won_montant_total(sample_df):
    out = _compute_bu_amounts(sample_df, 2026, 'CONCEPTION')
    assert out['signes'] == pytest.approx(90.0)


def test_compute_bu_amounts_potentiels_uses_waiting_montant_pondere(sample_df):
    out = _compute_bu_amounts(sample_df, 2026, 'CONCEPTION')
    assert out['potentiels'] == pytest.approx(90.0)


def test_compute_bu_amounts_envoyes_is_waiting_pipe_only(sample_df):
    out = _compute_bu_amounts(sample_df, 2026, 'CONCEPTION')
    # Sent pipe only (waiting), raw Montant Total 2026 — won is NOT mixed in.
    assert out['envoyes'] == pytest.approx(180.0)


def test_compute_bu_amounts_maintenance_excludes_other_bus(sample_df):
    out = _compute_bu_amounts(sample_df, 2026, 'MAINTENANCE')
    # Won this year: 7088/12*10 ; Won previous year: 0 in Montant Total 2026
    expected_signes = 7088 / 12 * 10
    assert out['signes'] == pytest.approx(expected_signes)
    assert out['potentiels'] == pytest.approx(1000.0)
    # Envoyés = waiting pipe only (won not mixed in)
    assert out['envoyes'] == pytest.approx(2000.0)


def test_compute_bu_amounts_handles_empty_df():
    out = _compute_bu_amounts(pd.DataFrame(), 2026, 'TRAVAUX')
    assert out == {"signes": 0.0, "potentiels": 0.0, "envoyes": 0.0}


# ---------------------------------------------------------------------------
# _sum_production_by_bu (production-year aggregation, no status filter)
# ---------------------------------------------------------------------------

@pytest.fixture
def production_df() -> pd.DataFrame:
    """
    Production-aggregated-style frame: rows from several signing years that all
    produce in 2026. No status column — Signé sheets are implicitly all won.
    """
    return pd.DataFrame([
        {"cf_bu": "CONCEPTION", "Montant Total 2026": 100.0, "Montant Pondéré 2026": 100.0, "signed_year": 2024},
        {"cf_bu": "CONCEPTION", "Montant Total 2026": 250.0, "Montant Pondéré 2026": 250.0, "signed_year": 2025},
        {"cf_bu": "CONCEPTION", "Montant Total 2026": 50.0,  "Montant Pondéré 2026": 50.0,  "signed_year": 2026},
        {"cf_bu": "TRAVAUX",    "Montant Total 2026": 900.0, "Montant Pondéré 2026": 900.0, "signed_year": 2025},
    ])


def test_sum_production_by_bu_aggregates_across_signing_years(production_df):
    # 100 (2024) + 250 (2025) + 50 (2026) — the carryover cascade
    assert _sum_production_by_bu(production_df, 2026, "CONCEPTION") == pytest.approx(400.0)


def test_sum_production_by_bu_isolates_bu(production_df):
    assert _sum_production_by_bu(production_df, 2026, "TRAVAUX") == pytest.approx(900.0)


def test_sum_production_by_bu_handles_empty():
    assert _sum_production_by_bu(pd.DataFrame(), 2026, "CONCEPTION") == 0.0


# ---------------------------------------------------------------------------
# drop_stale_sent_carryover (prune dead prior-year sent proposals)
# ---------------------------------------------------------------------------

@pytest.fixture
def sent_carryover_df() -> pd.DataFrame:
    return pd.DataFrame([
        # prior-year sent, start already overdue → STALE, dropped
        {"id_devis": "old-overdue", "signed_year": 2025, "projet_start": "2026-02-01"},
        # prior-year sent, start still in the future → kept
        {"id_devis": "old-future", "signed_year": 2025, "projet_start": "2026-11-01"},
        # current-year sent, overdue start → kept (benefit of the doubt)
        {"id_devis": "cur-overdue", "signed_year": 2026, "projet_start": "2026-02-01"},
        # prior-year sent, no start date → kept (cannot judge)
        {"id_devis": "old-nostart", "signed_year": 2025, "projet_start": None},
    ])


def test_drop_stale_sent_carryover_removes_only_overdue_prior_year(sent_carryover_df):
    out = drop_stale_sent_carryover(sent_carryover_df, 2026, date(2026, 6, 1))
    kept = set(out["id_devis"])
    assert kept == {"old-future", "cur-overdue", "old-nostart"}


def test_drop_stale_sent_carryover_passthrough_when_columns_missing():
    df = pd.DataFrame([{"id_devis": "x", "Montant Total 2026": 10.0}])
    out = drop_stale_sent_carryover(df, 2026, date(2026, 6, 1))
    assert len(out) == 1


# ---------------------------------------------------------------------------
# build_budget_workbook (integration)
# ---------------------------------------------------------------------------

def test_build_budget_workbook_produces_single_sheet_with_expected_headers(sample_df):
    import openpyxl

    blob = build_budget_workbook(
        year=2026,
        df_processed=sample_df,
        portefeuille_debut_annee=996697.45,
        portefeuille_running=1043709.16,
        today=date(2026, 3, 16),
    )
    assert isinstance(blob, (bytes, bytearray))
    assert len(blob) > 0

    wb = openpyxl.load_workbook(BytesIO(blob), data_only=False)
    # Maintenance tab removed — only the main projection sheet remains.
    assert wb.sheetnames == ["Budget 2026 avec légende"]

    ws1 = wb["Budget 2026 avec légende"]
    # Légende block — rich text with bold terms; flatten to plain text to assert.
    assert ws1["D11"].value == "Légende"
    legend_plain = str(ws1["D12"].value or "")
    assert "Devis Signés" in legend_plain
    assert LEGEND_TEXT_TEMPLATE.format(year=2026).split("\n")[0] in legend_plain

    # Date stamp
    assert ws1["D16"].value == "Au 16/03/2026"

    # BU header band
    assert ws1["D17"].value == "Projection 2026"
    assert ws1["E17"].value == "CONCEPTION"
    assert ws1["H17"].value == "TRAVAUX"
    assert ws1["K17"].value == "MAINTENANCE"
    assert ws1["N17"].value == "TOTAL"

    # Subheaders
    assert ws1["E18"].value == "Devis Signés"
    assert ws1["F18"].value == "Devis Potentiels"
    assert ws1["G18"].value == "Devis Envoyés"
    assert ws1["K18"].value == "Nouveaux contrats 2026"

    # Numeric values come from sample_df aggregation
    assert ws1["E19"].value == pytest.approx(90.0)   # CONCEPTION signes (won)
    assert ws1["F19"].value == pytest.approx(90.0)   # CONCEPTION potentiels (waiting, weighted)
    assert ws1["G19"].value == pytest.approx(180.0)  # CONCEPTION envoyes (waiting, raw)

    # Formulas in row 20 / row 22 / row 25
    assert ws1["E20"].value == "=E19+F19"
    assert ws1["L22"].value == "=L19+L21"
    assert ws1["E25"].value == "=E23-E20"
    assert ws1["N25"].value == "=E25+H25+K25"

    # Portefeuille values
    assert ws1["L21"].value == pytest.approx(1043709.16)


def test_build_budget_workbook_accepts_precomputed_bu_totals():
    import openpyxl

    bu_totals = {
        "CONCEPTION": {"signes": 400.0, "potentiels": 10.0, "envoyes": 20.0},
        "TRAVAUX": {"signes": 900.0, "potentiels": 30.0, "envoyes": 40.0},
        "MAINTENANCE": {"signes": 111.0, "potentiels": 5.0, "envoyes": 6.0},
    }
    blob = build_budget_workbook(
        year=2026,
        portefeuille_debut_annee=996697.45,
        portefeuille_running=1160616.0,
        today=date(2026, 6, 1),
        bu_totals=bu_totals,
    )
    wb = openpyxl.load_workbook(BytesIO(blob), data_only=False)
    assert wb.sheetnames == ["Budget 2026 avec légende"]
    ws1 = wb["Budget 2026 avec légende"]
    # Row 19 reflects the precomputed (production-aggregated) values verbatim.
    assert ws1["E19"].value == pytest.approx(400.0)
    assert ws1["H19"].value == pytest.approx(900.0)
    assert ws1["K19"].value == pytest.approx(111.0)
    assert ws1["L21"].value == pytest.approx(1160616.0)


def test_build_budget_workbook_handles_missing_portefeuille(sample_df):
    import openpyxl

    blob = build_budget_workbook(
        year=2027,  # no entretien début for 2027
        df_processed=sample_df,
        portefeuille_debut_annee=None,
        portefeuille_running=None,
        today=date(2027, 1, 5),
    )
    wb = openpyxl.load_workbook(BytesIO(blob), data_only=False)
    ws1 = wb["Budget 2027 avec légende"]
    # Empty portefeuille cell, but layout still intact
    assert ws1["L21"].value in (None, "")
    assert ws1["L22"].value == "=L19+L21"
