"""Tests for the merge layer (apply_input_overrides, inject_manual_projects, apply_quarter_overrides)."""

from datetime import datetime

import pandas as pd
import pytest

from src.processing.manual_and_overrides import (
    apply_input_overrides,
    apply_quarter_overrides,
    inject_manual_projects,
    make_manual_row,
)
from src.processing.manual_projects_store import ManualProject, ManualProjectsStore
from src.processing.overrides_store import OverridesStore
from src.processing.revenue_engine import RevenueEngine
from src.processing.views import ViewGenerator


CURRENT_YEAR = datetime.now().year


def _build_proposal_df():
    return pd.DataFrame(
        [
            {
                "id": "12345",
                "title": "Refonte parvis",
                "amount": 12000.0,
                "probability": 50,
                "probability_calc": 50.0,
                "probability_factor": 0.5,
                "date": pd.Timestamp(f"{CURRENT_YEAR}-02-15"),
                "projet_start": pd.Timestamp(f"{CURRENT_YEAR}-03-01"),
                "projet_stop": pd.Timestamp(f"{CURRENT_YEAR}-08-31"),
                "final_bu": "TRAVAUX",
                "cf_bu": "TRAVAUX",
                "cf_typologie_de_devis": "Travaux Direct",
                "statut": "envoyée(s) en attente de réponse",
                "statut_clean": "envoyée(s) en attente de réponse",
            }
        ]
    )


# ---------------------------------------------------------------------------
# apply_input_overrides
# ---------------------------------------------------------------------------


def test_apply_input_overrides_mutates_amount_and_dates(tmp_path):
    df = _build_proposal_df()
    store = OverridesStore(tmp_path / "overrides.json")
    store.upsert(
        "12345",
        input_overrides={
            "amount": 30000.0,
            "probability": 80,
            "projet_start": f"{CURRENT_YEAR}-04-01",
        },
    )

    result = apply_input_overrides(df, store)
    row = result.iloc[0]
    assert row["amount"] == 30000.0
    assert row["probability"] == 80
    assert row["projet_start"] == pd.Timestamp(f"{CURRENT_YEAR}-04-01")
    assert row["probability_factor"] == pytest.approx(0.8)


def test_apply_input_overrides_no_op_for_empty_store(tmp_path):
    df = _build_proposal_df()
    store = OverridesStore(tmp_path / "overrides.json")
    result = apply_input_overrides(df, store)
    pd.testing.assert_frame_equal(result, df)


def test_apply_input_overrides_ignores_unknown_columns(tmp_path):
    df = _build_proposal_df()
    store = OverridesStore(tmp_path / "overrides.json")
    store.upsert("12345", input_overrides={"foo_bar": 12, "amount": 99999})
    result = apply_input_overrides(df, store)
    assert "foo_bar" not in result.columns
    assert result.iloc[0]["amount"] == 99999


# ---------------------------------------------------------------------------
# inject_manual_projects
# ---------------------------------------------------------------------------


def test_inject_manual_projects_appends_row_with_engine_columns(tmp_path):
    df = _build_proposal_df()
    engine = RevenueEngine()
    df = engine.process(df)
    store = ManualProjectsStore(tmp_path / "manual.json")
    store.add(
        title="Manual project",
        company_name="Client X",
        amount=12000,
        probability=80,
        date=f"{CURRENT_YEAR}-02-01",
        projet_start=f"{CURRENT_YEAR}-03-01",
        projet_stop=f"{CURRENT_YEAR}-05-31",
        cf_bu="TRAVAUX",
        cf_typologie_de_devis="Travaux Direct",
    )

    result = inject_manual_projects(df, store, engine)
    assert len(result) == 2
    manual_row = result[result["id"].astype(str).str.startswith("MAN-")].iloc[0]
    total_year = manual_row[f"Montant Total {CURRENT_YEAR}"]
    assert total_year == pytest.approx(12000.0)
    # Travaux multi-month: spread across 3 months → some Q1, some Q2.
    q1 = manual_row[f"Montant Total Q1_{CURRENT_YEAR}"]
    q2 = manual_row[f"Montant Total Q2_{CURRENT_YEAR}"]
    assert q1 + q2 == pytest.approx(12000.0)


def test_inject_manual_projects_no_op_when_store_empty(tmp_path):
    df = _build_proposal_df()
    engine = RevenueEngine()
    df = engine.process(df)
    store = ManualProjectsStore(tmp_path / "manual.json")
    result = inject_manual_projects(df, store, engine)
    pd.testing.assert_frame_equal(result, df)


# ---------------------------------------------------------------------------
# make_manual_row — won vs waiting
# ---------------------------------------------------------------------------


def _manual(**overrides) -> ManualProject:
    base = dict(
        manual_id="MAN-2026-0001",
        title="Oral deal",
        company_name="Axa",
        amount=120000,
        probability=100,
        date=f"{CURRENT_YEAR}-06-01",
        projet_start=f"{CURRENT_YEAR}-07-01",
        projet_stop=f"{CURRENT_YEAR}-09-30",
        cf_bu="CONCEPTION",
        cf_typologie_de_devis="Conception Paysage",
    )
    base.update(overrides)
    return ManualProject(**base)


def test_make_manual_row_won_sets_effective_won_date():
    row = make_manual_row(
        _manual(statut="gagné", signature_date=f"{CURRENT_YEAR}-06-02"),
        RevenueEngine(),
    )
    assert row["statut_clean"] == "gagné"
    assert row["date_effective_won"] == pd.Timestamp(f"{CURRENT_YEAR}-06-02")
    assert row["signature_date"] == pd.Timestamp(f"{CURRENT_YEAR}-06-02")


def test_make_manual_row_waiting_has_no_effective_won_date():
    row = make_manual_row(_manual(statut="en cours"), RevenueEngine())
    assert row["statut_clean"] == "en cours"
    assert pd.isna(row["date_effective_won"])


def test_won_manual_routes_into_signe_view_only():
    """A won manual must land in the Signé (won) view, never snapshot/sent."""
    engine = RevenueEngine()
    base_df = pd.DataFrame(
        [
            {
                "id": "111",
                "title": "Furious waiting",
                "company_name": "C",
                "amount": 1000.0,
                "probability": 50,
                "probability_calc": 50.0,
                "probability_factor": 0.5,
                "date": pd.Timestamp(f"{CURRENT_YEAR}-06-01"),
                "projet_start": pd.Timestamp(f"{CURRENT_YEAR}-06-01"),
                "projet_stop": pd.Timestamp(f"{CURRENT_YEAR}-06-30"),
                "final_bu": "MAINTENANCE",
                "cf_bu": "MAINTENANCE",
                "cf_typologie_de_devis": "Maintenance Entretien",
                "statut": "en cours",
                "statut_clean": "en cours",
                "created_at": pd.Timestamp(f"{CURRENT_YEAR}-06-01"),
                "signature_date": pd.NaT,
                "date_effective_won": pd.NaT,
            }
        ]
    )
    base = engine.process(base_df)

    class _SingleManualStore:
        def all(self):
            return [
                _manual(
                    manual_id="MAN-9999-0001",
                    statut="gagné",
                    signature_date=datetime.now().strftime("%Y-%m-%d"),
                )
            ]

        def count(self):
            return 1

    df = inject_manual_projects(base, _SingleManualStore(), engine)
    views = ViewGenerator().generate(df)

    def _ids(view):
        return view.data["id"].astype(str).tolist()

    assert "MAN-9999-0001" in _ids(views.won_month)
    assert "MAN-9999-0001" not in _ids(views.snapshot)
    assert "MAN-9999-0001" not in _ids(views.sent_month)


# ---------------------------------------------------------------------------
# apply_quarter_overrides
# ---------------------------------------------------------------------------


def test_apply_quarter_overrides_replaces_cell_and_recomputes_year(tmp_path):
    df = _build_proposal_df()
    engine = RevenueEngine()
    df = engine.process(df)

    store = OverridesStore(tmp_path / "overrides.json")
    store.upsert(
        "12345",
        quarter_overrides={
            f"Montant Total Q1_{CURRENT_YEAR}": 300.0,
            f"Montant Total Q2_{CURRENT_YEAR}": 0.0,
            f"Montant Total Q3_{CURRENT_YEAR}": 0.0,
            f"Montant Total Q4_{CURRENT_YEAR}": 0.0,
        },
    )

    result = apply_quarter_overrides(df, store, engine.years_to_track)
    row = result.iloc[0]
    assert row[f"Montant Total Q1_{CURRENT_YEAR}"] == 300.0
    assert row[f"Montant Total {CURRENT_YEAR}"] == 300.0
    # Pondéré recomputed from probability=50 → factor 0.5.
    assert row[f"Montant Pondéré Q1_{CURRENT_YEAR}"] == pytest.approx(150.0)
    assert row[f"Montant Pondéré {CURRENT_YEAR}"] == pytest.approx(150.0)


def test_apply_quarter_overrides_syncs_amount_with_breakdown(tmp_path):
    df = _build_proposal_df()
    engine = RevenueEngine()
    df = engine.process(df)

    # Split the deal across two years with a grand total (300 + 700 = 1000)
    # different from the engine's natural 12000 → amount should follow.
    store = OverridesStore(tmp_path / "overrides.json")
    next_year = CURRENT_YEAR + 1
    store.upsert(
        "12345",
        quarter_overrides={
            f"Montant Total Q1_{CURRENT_YEAR}": 300.0,
            f"Montant Total Q2_{CURRENT_YEAR}": 0.0,
            f"Montant Total Q3_{CURRENT_YEAR}": 0.0,
            f"Montant Total Q4_{CURRENT_YEAR}": 0.0,
            f"Montant Total Q1_{next_year}": 700.0,
        },
    )

    result = apply_quarter_overrides(df, store, engine.years_to_track)
    row = result.iloc[0]
    assert row[f"Montant Total {CURRENT_YEAR}"] == pytest.approx(300.0)
    assert row[f"Montant Total {next_year}"] == pytest.approx(700.0)
    # amount tracks the sum of all year totals (300 + 700).
    assert row["amount"] == pytest.approx(1000.0)


def test_apply_quarter_overrides_no_op_when_store_empty(tmp_path):
    df = _build_proposal_df()
    engine = RevenueEngine()
    df = engine.process(df)
    store = OverridesStore(tmp_path / "overrides.json")
    result = apply_quarter_overrides(df, store, engine.years_to_track)
    pd.testing.assert_frame_equal(result, df)


def test_apply_quarter_overrides_ignores_unknown_year(tmp_path):
    df = _build_proposal_df()
    engine = RevenueEngine()
    df = engine.process(df)

    # Year far outside the engine's tracked window (no Montant Total 1999 col).
    store = OverridesStore(tmp_path / "overrides.json")
    store.upsert(
        "12345",
        quarter_overrides={"Montant Total Q1_1999": 999.0},
    )
    result = apply_quarter_overrides(df, store, engine.years_to_track)
    # The override col is created on the fly but the year isn't recomputed
    # because Montant Total 1999 doesn't exist.
    assert result.iloc[0]["Montant Total Q1_1999"] == 999.0


# ---------------------------------------------------------------------------
# RevenueEngine.process_single_row
# ---------------------------------------------------------------------------


def test_process_single_row_returns_financial_columns(tmp_path):
    engine = RevenueEngine()
    cols = engine.process_single_row(
        {
            "amount": 10000,
            "probability": 60,
            "date": f"{CURRENT_YEAR}-01-15",
            "projet_start": f"{CURRENT_YEAR}-02-01",
            "projet_stop": f"{CURRENT_YEAR}-04-30",
            "final_bu": "TRAVAUX",
        }
    )
    assert cols[f"Montant Total {CURRENT_YEAR}"] == pytest.approx(10000.0)
    # 60% probability → pondéré = 60% of 10000.
    assert cols[f"Montant Pondéré {CURRENT_YEAR}"] == pytest.approx(6000.0)
