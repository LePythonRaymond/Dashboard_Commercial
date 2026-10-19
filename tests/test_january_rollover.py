"""
The two January breakdowns found in the 2026-09-28 audit.

1. With pandas >= 2.3, the row-wise probability recompute of the input overrides
   turned the all-empty ``date_effective_won`` column into objects, and the views'
   ``.dt.month`` crashed the whole run. That column is all empty while no cross-year
   avenant is injected, i.e. at the start of every year.
2. In January the previous month is December of the previous year: the daily pass
   would rewrite it without its avenants and without a column for its own year.
"""

import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.api.proposal_addons import merge_addons_into_proposals
from src.processing.cleaner import DataCleaner
from src.processing.manual_and_overrides import apply_input_overrides
from src.processing.manual_projects_store import ManualProjectsStore
from src.processing.overrides_store import OverridesStore
from src.processing.previous_year_signe import build_previous_year_won_month
from src.processing.views import ViewGenerator


def _won_frame():
    """Cleaned won devis, as the pipeline holds them in early January (no injected avenant)."""
    return pd.DataFrame({
        "id": ["1", "2", "3"],
        "amount": [1000.0, 2000.0, 3000.0],
        "probability": [80, 0, "abc"],
        "statut": ["Gagnés en cours"] * 3,
        "statut_clean": ["gagnés en cours"] * 3,
        "date": pd.to_datetime(["2027-01-04", "2027-01-05", "2026-12-15"]),
        "date_effective_won": pd.to_datetime([None, None, None]),  # Furious never fills signature_date
    })


def test_an_input_override_keeps_the_date_columns_and_the_views_run(tmp_path):
    store = OverridesStore(tmp_path / "overrides.json")
    store.upsert("1", input_overrides={"amount": 1500})

    df = apply_input_overrides(_won_frame(), store)

    assert str(df["date_effective_won"].dtype).startswith("datetime64")
    won = ViewGenerator(reference_date=datetime(2027, 1, 15))._filter_won_month(df, target_month=1, target_year=2027)
    assert sorted(won["id"]) == ["1", "2"]
    assert df.loc[0, "amount"] == 1500


def test_an_input_override_recomputes_the_probability_factor(tmp_path):
    store = OverridesStore(tmp_path / "overrides.json")
    store.upsert("1", input_overrides={"probability": 80})

    df = apply_input_overrides(_won_frame(), store)

    # 0 and unreadable count as 50%, as in the cleaner
    assert list(df["probability_factor"]) == [0.8, 0.5, 0.5]
    assert list(df["probability_calc"]) == [80.0, 50.0, 50.0]


def _december_devis():
    """A 9,000 € CONCEPTION signed on 2026-12-10, plus its 3,000 € avenant of 2026-12-20."""
    proposals = pd.DataFrame([{
        "id": 500, "title": "Conception signée en décembre", "amount": 9000.0, "probability": 100,
        "date": "2026-12-10", "projet_start": "2026-12-10", "projet_stop": "", "created_at": "2026-12-01",
        "signature_date": "", "last_updated_at": "2026-12-10", "statut": "Gagnés en cours",
        "cf_bu": "CONCEPTION", "cf_typologie_de_devis": "Conception Paysage", "cf_typologie_myrium": "",
        "assigned_to": "clemence", "company_name": "Client",
    }])
    avenants = pd.DataFrame([{
        "id": 500, "id_system": 1, "amount": 3000.0, "status": "1", "title": "Avenant",
        "date": "2026-12-20", "probability": None, "signature_date": None, "created_at": "2026-12-20",
    }])
    return proposals, avenants


def test_december_is_rebuilt_in_january_with_its_avenant_and_its_year(tmp_path):
    proposals, avenants = _december_devis()

    view = build_previous_year_won_month(
        proposals, avenants, 2026, 12, set(),
        OverridesStore(tmp_path / "overrides.json"), ManualProjectsStore(tmp_path / "manual_projects.json"),
    )

    assert view.name == "Signé Decembre 2026"
    row = view.data.iloc[0]
    assert row["amount"] == 12000.0
    # Under 15k: one third a month, December 2026 then January and February 2027
    assert abs(row["Montant Total 2026"] - 4000.0) < 1e-6
    assert abs(row["Montant Total 2027"] - 8000.0) < 1e-6


def test_the_daily_pass_would_have_lost_both():
    """What the January run wrote before the fix, for the record."""
    proposals, avenants = _december_devis()
    merged, _ = merge_addons_into_proposals(proposals, avenants, target_year=2027)
    view_generator = ViewGenerator(reference_date=datetime(2027, 1, 15))
    df = view_generator.revenue_engine.process(DataCleaner().clean(merged))

    view = view_generator.generate_won_for_month(df, 2026, 12)

    row = view.data.iloc[0]
    assert row["amount"] == 9000.0  # avenant dropped
    assert "Montant Total 2026" not in view.data.columns  # December production moved into January 2027
    assert row["Montant Total Q1_2027"] == 9000.0
