"""
The summaries under each view are written in one call per table.

They used to be written one call per row (about 19 calls per view, 15 views a run),
which spent the per-minute write quota and cost three or four 65-second 429 pauses
on every daily run. The cells must stay exactly the same: the dashboard stops
reading a tab at the "Résumé par BU" row.
"""

import sys
from pathlib import Path

import pandas as pd

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.integrations.google_sheets import GoogleSheetsClient
from tests.test_refresh_request import FakeWorksheet

SUMMARY = [
    {"cf_bu": bu, "amount": amount,
     "Montant Total 2026": amount * 0.6, "Montant Pondéré 2026": amount * 0.3,
     "Montant Total 2027": amount * 0.4, "Montant Pondéré 2027": amount * 0.2,
     "Montant Total 2028": 0.0, "Montant Pondéré 2028": 0.0}
    for bu, amount in [("CONCEPTION", 120000.0), ("TRAVAUX", 300000.0), ("MAINTENANCE", 80000.0), ("TOTAL", 500000.0)]
]


def _old_write_summary(client, worksheet, summary_data, title, start_row):
    """The row-by-row writer as it was before (values computed by the same code)."""
    worksheet.update(range_name=f"A{start_row}", values=[[title]])
    current_row = start_row + 1
    summary_df = pd.DataFrame(summary_data)
    headers = summary_df.columns.tolist()
    amount_cols = [c for c in summary_df.columns if 'amount' in str(c).lower() or 'montant' in str(c).lower()]
    data_rows = []
    for _, row in summary_df.iterrows():
        values = []
        for col_name, val in zip(summary_df.columns, row.values):
            if col_name in amount_cols:
                values.append(float(val) if pd.notna(val) and val != '' else 0)
            else:
                values.append(str(val) if pd.notna(val) and val != '' else '')
        data_rows.append(values)
    new_headers, new_data_rows, separator_indices = client._insert_year_separators(headers, data_rows)
    worksheet.update(range_name=f"A{current_row}", values=[new_headers])
    current_row += 1
    for row_values in new_data_rows:
        worksheet.update(range_name=f"A{current_row}", values=[row_values])
        current_row += 1
    return current_row + 1, separator_indices


def test_one_call_and_the_same_cells_as_row_by_row():
    client = GoogleSheetsClient()
    old, new = FakeWorksheet(), FakeWorksheet()

    old_result = _old_write_summary(client, old, SUMMARY, "Résumé par BU", 31)
    new_result = client.write_summary(new, SUMMARY, "Résumé par BU", 31)

    assert new.updates == ["A31"]
    assert len(old.updates) == 6
    assert new.cells == old.cells
    assert new_result == old_result
    # Title, header with year separators, 4 rows; next block starts after a blank row
    assert new.cells[(31, 1)] == "Résumé par BU"
    assert new.cells[(32, 1)] == "cf_bu" and new.cells[(36, 1)] == "TOTAL"
    assert new_result[0] == 38 and new_result[1]


def test_an_empty_summary_writes_nothing():
    worksheet = FakeWorksheet()
    assert GoogleSheetsClient().write_summary(worksheet, [], "Résumé par BU", 10) == (10, [])
    assert worksheet.updates == []
