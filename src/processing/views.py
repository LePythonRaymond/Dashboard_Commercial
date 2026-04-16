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
from .typologie_allocation import allocate_typologie_for_row


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

    def _filter_won_month(
        self,
        df: pd.DataFrame,
        already_captured_ids: set = None,
        target_month: int = None,
        target_year: int = None,
    ) -> pd.DataFrame:
        """
        Filter for Won Month view: Status WON and (signature_date OR date OR last_updated_at
        is in the target month).

        The last_updated_at check catches proposals that became WON after their date month ended.
        already_captured_ids deduplicates against proposals already in other months' Signé sheets.

        Args:
            df: Processed DataFrame
            already_captured_ids: Set of proposal IDs already captured in other Signé sheets
            target_month: Month to filter for (defaults to current_month)
            target_year: Year to filter for (defaults to current_year)

        Returns:
            Filtered DataFrame
        """
        month = target_month or self.current_month
        year = target_year or self.current_year
        already_captured_ids = already_captured_ids or set()

        mask_status = df['statut_clean'].isin(STATUS_WON)

        # Date condition: signature_date is target month OR proposal date is target month
        mask_signature = (
            (df['date_effective_won'].dt.month == month) &
            (df['date_effective_won'].dt.year == year)
        )
        mask_date = (
            (df['date'].dt.month == month) &
            (df['date'].dt.year == year)
        )

        # Catch proposals whose status changed to WON recently (last_updated_at in target month)
        mask_updated = pd.Series(False, index=df.index)
        if 'last_updated_at' in df.columns:
            mask_updated = (
                (df['last_updated_at'].dt.month == month) &
                (df['last_updated_at'].dt.year == year)
            )

        mask_time = mask_signature | mask_date | mask_updated
        df_won = df[mask_status & mask_time].copy()

        # Dedup: exclude proposals already captured in other months' Signé sheets
        if already_captured_ids and 'id' in df_won.columns:
            df_won = df_won[~df_won['id'].astype(str).isin(already_captured_ids)]

        return df_won

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

        If title contains 'TS' (case-insensitive) and current typology is NOT 'Maintenance TS',
        override to 'Maintenance TS' for reporting purposes. This merges title-based TS detection
        into the typology Maintenance TS category without double counting.

        Args:
            row: DataFrame row with 'title' and 'cf_typologie_de_devis' columns

        Returns:
            Reporting typology string (original or 'Maintenance TS' if title-based override applies)
        """
        typologie = str(row.get('cf_typologie_de_devis', '')).strip()
        title = str(row.get('title', '')).strip()

        # Check if title contains TS (case-insensitive)
        title_has_ts = 'TS' in title.upper() if title else False

        # Apply override: if title has TS and typology is not already Maintenance TS (or TS), set to Maintenance TS
        typologie_upper = typologie.upper()
        if title_has_ts and typologie_upper != 'MAINTENANCE TS' and typologie_upper != 'TS' and 'TS' not in typologie_upper:
            return 'Maintenance TS'

        return typologie

    def _create_split_summary(
        self,
        df: pd.DataFrame,
        group_col: str,
        use_weighted: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Create summary aggregated by a column, with split handling.

        For cf_bu: one category per row (no split).
        For cf_typologie_de_devis: uses primary typologie only (allocate_typologie_for_row);
        each row contributes to exactly one typologie bucket. No comma-split or double-counting.

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

        agg_data: Dict[str, Dict[str, float]] = {}

        if group_col == 'cf_typologie_de_devis':
            # Typologie summary: primary-only allocation (one category per row)
            for _, row in df.iterrows():
                tags, primary = allocate_typologie_for_row(row)
                if not primary:
                    continue
                if primary not in agg_data:
                    agg_data[primary] = {c: 0.0 for c in cols_to_sum}
                for c in cols_to_sum:
                    if c in row.index:
                        agg_data[primary][c] += row[c]
        else:
            # BU or other: one category per row (no split)
            for _, row in df.iterrows():
                raw_group = str(row[group_col])
                cat = raw_group.strip()
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

    def generate_won_for_month(
        self,
        df: pd.DataFrame,
        year: int,
        month: int,
        already_captured_ids: set = None,
    ) -> ViewResult:
        """
        Generate a Signé view for a specific month (not necessarily current).

        Args:
            df: Fully processed DataFrame
            year: Target year
            month: Target month (1-12)
            already_captured_ids: IDs already in other months' Signé sheets

        Returns:
            ViewResult for the target month's Signé
        """
        month_str = MONTH_MAP.get(month, "Unknown")
        name = f"Signé {month_str} {year}"
        df_won = self._filter_won_month(
            df,
            already_captured_ids=already_captured_ids,
            target_month=month,
            target_year=year,
        )
        return self._create_view_result(name, df_won, use_weighted=False)

    def generate(
        self,
        df: pd.DataFrame,
        already_captured_ids: set = None,
    ) -> ViewsOutput:
        """
        Generate all three views from processed DataFrame.

        Args:
            df: Fully processed DataFrame (cleaned + revenue engine applied)
            already_captured_ids: Set of proposal IDs already in other Signé sheets (for dedup)

        Returns:
            ViewsOutput containing all views with summaries
        """
        # Generate filtered DataFrames
        df_snapshot = self._filter_snapshot(df)
        df_sent = self._filter_sent_month(df)
        df_won = self._filter_won_month(df, already_captured_ids=already_captured_ids)

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
        mask_updated = pd.Series(False, index=df.index)
        if 'last_updated_at' in df.columns:
            mask_updated = (
                (df['last_updated_at'].dt.month == self.current_month) &
                (df['last_updated_at'].dt.year == self.current_year)
            )
        mask_won = mask_status & (mask_signature | mask_date | mask_updated)

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
