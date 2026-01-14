"""
View Generator Module

Generates the three main views (Snapshot, Sent, Won) with summaries
for output to Google Sheets.
"""

import re
import pandas as pd
from typing import Dict, List, Any, Tuple
from datetime import datetime
from dataclasses import dataclass, field

from config.settings import STATUS_WON, STATUS_WAITING, MONTH_MAP
from .revenue_engine import RevenueEngine


@dataclass
class ViewResult:
    """Container for a view's data and metadata."""
    name: str
    data: pd.DataFrame
    summary_by_bu: List[Dict]
    summary_by_type: List[Dict]
    ts_total: float


@dataclass
class ViewsOutput:
    """Container for all generated views."""
    snapshot: ViewResult
    sent_month: ViewResult
    won_month: ViewResult
    sheet_names: Dict[str, str]
    counts: Dict[str, int]


class ViewGenerator:
    """
    Generates the three main data views for Google Sheets output.

    Views:
    1. Snapshot ("État au {DD-MM-YYYY}"): All proposals currently waiting
    2. Sent Month ("Envoyé {Month} {Year}"): Proposals created this month + waiting
    3. Won Month ("Signé {Month} {Year}"): Won proposals for current month
    """

    def __init__(self, reference_date: datetime = None):
        """
        Initialize the view generator.

        Args:
            reference_date: Date to use for month/year calculations.
                          Defaults to current date.
        """
        self.today = reference_date or datetime.now()
        self.current_year = self.today.year
        self.current_month = self.today.month
        self.month_str = MONTH_MAP.get(self.current_month, "Unknown")

        # Generate sheet names
        self.name_snapshot = f"État au {self.today.strftime('%d-%m-%Y')}"
        self.name_sent = f"Envoyé {self.month_str} {self.current_year}"
        self.name_won = f"Signé {self.month_str} {self.current_year}"

        # Get financial columns for summary
        # IMPORTANT: Summary columns must follow the view's reference year (the sheet year),
        # not the machine's current year. This matters for backfills (e.g. "Mars 2025")
        # where we still want summaries to include 2025.
        self.revenue_engine = RevenueEngine(
            years_to_track=[self.current_year, self.current_year + 1, self.current_year + 2, self.current_year + 3]
        )
        self.financial_cols = self.revenue_engine.get_financial_columns()

    def _filter_snapshot(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Filter for Snapshot view: All proposals with status in WAITING.

        Args:
            df: Processed DataFrame

        Returns:
            Filtered DataFrame
        """
        mask = df['statut_clean'].isin(STATUS_WAITING)
        return df[mask].copy()

    def _filter_sent_month(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Filter for Sent Month view: Created this month AND status WAITING.

        Args:
            df: Processed DataFrame

        Returns:
            Filtered DataFrame
        """
        mask = (
            (df['created_at'].dt.month == self.current_month) &
            (df['created_at'].dt.year == self.current_year) &
            (df['statut_clean'].isin(STATUS_WAITING))
        )
        return df[mask].copy()

    def _filter_won_month(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Filter for Won Month view: Status WON and (signature_date OR date is current month).

        This handles cases where signature_date might be missing in CRM.

        Args:
            df: Processed DataFrame

        Returns:
            Filtered DataFrame
        """
        mask_status = df['statut_clean'].isin(STATUS_WON)

        # Date condition: signature_date is current month OR proposal date is current month
        mask_signature = (
            (df['date_effective_won'].dt.month == self.current_month) &
            (df['date_effective_won'].dt.year == self.current_year)
        )
        mask_date = (
            (df['date'].dt.month == self.current_month) &
            (df['date'].dt.year == self.current_year)
        )

        mask_time = mask_signature | mask_date

        return df[mask_status & mask_time].copy()

    def _calculate_ts_total(self, df: pd.DataFrame) -> float:
        """
        Calculate total amount for TS (Travaux Spéciaux) projects.

        Args:
            df: Filtered DataFrame

        Returns:
            Sum of amounts for projects with "TS" in title
        """
        if df.empty:
            return 0.0

        mask_ts = df['title'].str.contains("TS", case=False, na=False)
        return df.loc[mask_ts, 'amount'].sum()

    def _get_reporting_typologie(self, row: pd.Series) -> str:
        """
        Get the reporting typology for a row, applying TS title override rule.

        If title contains 'TS' (case-insensitive) and current typology is NOT 'TS',
        override to 'TS' for reporting purposes. This merges title-based TS detection
        into the typology TS category without double counting.

        Args:
            row: DataFrame row with 'title' and 'cf_typologie_de_devis' columns

        Returns:
            Reporting typology string (original or 'TS' if title-based override applies)
        """
        typologie = str(row.get('cf_typologie_de_devis', '')).strip()
        title = str(row.get('title', '')).strip()

        # Check if title contains TS (case-insensitive)
        title_has_ts = 'TS' in title.upper() if title else False

        # Apply override: if title has TS and typology is not already TS, set to TS
        if title_has_ts and typologie.upper() != 'TS':
            return 'TS'

        return typologie

    def _create_split_summary(
        self,
        df: pd.DataFrame,
        group_col: str,
        use_weighted: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Create summary aggregated by a column, with split handling.

        Handles cases where a value like "DV, PAYSAGE" should be split
        and added to both "DV" and "PAYSAGE" totals.

        For typology summaries, applies TS title override rule: title-based TS
        detection is merged into typology 'TS' category.

        Args:
            df: Filtered DataFrame
            group_col: Column to group by (e.g., 'cf_bu', 'cf_typologie_de_devis')
            use_weighted: If True, use weighted amounts; else use total amounts

        Returns:
            List of summary dictionaries
        """
        if df.empty or group_col not in df.columns:
            return []

        # Determine which financial columns to sum
        if use_weighted:
            cols_to_sum = ['amount'] + [c for c in self.financial_cols if 'Pondéré' in c and c in df.columns]
        else:
            cols_to_sum = ['amount'] + [c for c in self.financial_cols if 'Total' in c and c in df.columns]

        # Aggregate with split handling
        agg_data: Dict[str, Dict[str, float]] = {}

        for _, row in df.iterrows():
            # For typology summaries, apply reporting typology override (TS title rule)
            if group_col == 'cf_typologie_de_devis':
                raw_group = self._get_reporting_typologie(row)
            else:
                raw_group = str(row[group_col])

            # Split by comma or space
            categories = re.split(r'[ ,]+', raw_group)

            for cat in categories:
                cat = cat.strip()
                if not cat or cat.lower() == 'nan':
                    continue

                if cat not in agg_data:
                    agg_data[cat] = {c: 0.0 for c in cols_to_sum}

                for c in cols_to_sum:
                    if c in row.index:
                        agg_data[cat][c] += row[c]

        # Convert to list format
        output_list = []
        for cat_name, sums in sorted(agg_data.items()):
            row_out = {group_col: cat_name}
            row_out.update(sums)
            output_list.append(row_out)

        return output_list

    def _create_view_result(
        self,
        name: str,
        df: pd.DataFrame,
        use_weighted: bool = True
    ) -> ViewResult:
        """
        Create a ViewResult with summaries.

        Args:
            name: View/sheet name
            df: Filtered DataFrame for this view
            use_weighted: Whether to use weighted amounts in summaries

        Returns:
            ViewResult with data and summaries
        """
        return ViewResult(
            name=name,
            data=df,
            summary_by_bu=self._create_split_summary(df, 'cf_bu', use_weighted),
            summary_by_type=self._create_split_summary(df, 'cf_typologie_de_devis', use_weighted),
            ts_total=0.0  # Deprecated: TS now merged into typology summary, set to 0 for backward compatibility
        )

    def generate(self, df: pd.DataFrame) -> ViewsOutput:
        """
        Generate all three views from processed DataFrame.

        Args:
            df: Fully processed DataFrame (cleaned + revenue engine applied)

        Returns:
            ViewsOutput containing all views with summaries
        """
        # Generate filtered DataFrames
        df_snapshot = self._filter_snapshot(df)
        df_sent = self._filter_sent_month(df)
        df_won = self._filter_won_month(df)

        # Create view results (Won uses non-weighted since deals are closed)
        snapshot = self._create_view_result(self.name_snapshot, df_snapshot, use_weighted=True)
        sent = self._create_view_result(self.name_sent, df_sent, use_weighted=True)
        won = self._create_view_result(self.name_won, df_won, use_weighted=False)

        return ViewsOutput(
            snapshot=snapshot,
            sent_month=sent,
            won_month=won,
            sheet_names={
                "snapshot": self.name_snapshot,
                "sent": self.name_sent,
                "won": self.name_won
            },
            counts={
                self.name_snapshot: len(df_snapshot),
                self.name_sent: len(df_sent),
                self.name_won: len(df_won)
            }
        )

    def get_combined_mask(self, df: pd.DataFrame) -> pd.Series:
        """
        Get mask for all proposals that appear in any of the three views.

        Useful for alert generation (only flag proposals in active views).

        Args:
            df: Processed DataFrame

        Returns:
            Boolean Series mask
        """
        mask_snapshot = df['statut_clean'].isin(STATUS_WAITING)

        mask_sent = (
            (df['created_at'].dt.month == self.current_month) &
            (df['created_at'].dt.year == self.current_year) &
            (df['statut_clean'].isin(STATUS_WAITING))
        )

        mask_status = df['statut_clean'].isin(STATUS_WON)
        mask_signature = (
            (df['date_effective_won'].dt.month == self.current_month) &
            (df['date_effective_won'].dt.year == self.current_year)
        )
        mask_date = (
            (df['date'].dt.month == self.current_month) &
            (df['date'].dt.year == self.current_year)
        )
        mask_won = mask_status & (mask_signature | mask_date)

        return mask_snapshot | mask_sent | mask_won


def generate_views(df: pd.DataFrame, reference_date: datetime = None) -> ViewsOutput:
    """
    Convenience function to generate all views.

    Args:
        df: Processed proposals DataFrame
        reference_date: Optional reference date for calculations

    Returns:
        ViewsOutput with all views and summaries
    """
    generator = ViewGenerator(reference_date)
    return generator.generate(df)
