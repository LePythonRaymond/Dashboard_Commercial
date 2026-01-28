"""
Unit tests for objectives management email HTML generation.

We validate that the email includes:
- production-year wording
- 11-month period label (e.g., Juil+Août)
- carryover formatting "(dont ... années précéd.)"
"""

from datetime import datetime
import pandas as pd


def test_objectives_email_contains_carryover_and_period_label():
    from src.integrations.email_sender import EmailSender

    sender = EmailSender(test_mode=True)
    production_year = 2026
    reference_date = datetime(2026, 8, 15)  # August -> accounting period should be "Juil+Août"

    # Provide minimal fallback dataframes (main generator may fallback when it can't load sheets).
    df_envoye = pd.DataFrame(
        [
            {
                "source_sheet": "Envoyé Août 2026",
                "signed_year": 2026,
                "cf_bu": "TRAVAUX",
                "cf_typologie_de_devis": "Travaux DV",
                f"Montant Total {production_year}": 10000.0,
                f"Montant Total Q3_{production_year}": 10000.0,
                f"Montant Pondéré {production_year}": 5000.0,
                f"Montant Pondéré Q3_{production_year}": 5000.0,
            }
        ]
    )
    df_signe = df_envoye.copy()
    df_signe["source_sheet"] = "Signé Août 2026"

    html = sender._generate_objectives_management_html(reference_date, production_year, df_envoye, df_signe)

    assert f"Production {production_year}" in html
    assert "Juil+Août" in html
    assert "années précéd." in html
