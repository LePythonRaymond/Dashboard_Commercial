"""
Revenue Spreading Engine

Implements the complex revenue allocation logic across time periods
based on Business Unit type and project duration.
"""

import pandas as pd
import numpy as np
from typing import List, Tuple, Dict, Any
from datetime import datetime
from dataclasses import dataclass

from config.settings import CONCEPTION_THRESHOLD_LOW, CONCEPTION_THRESHOLD_HIGH


@dataclass
class RevenueAllocation:
    """Represents a monthly revenue allocation."""
    year: int
    month: int
    amount: float


class RevenueEngine:
    """
    Calculates revenue spreading across time periods.

    Implements different spreading rules based on Business Unit:
    - MAINTENANCE: Spread evenly over project duration
    - TRAVAUX: 100% on start if < 1 month, else spread evenly
    - CONCEPTION: Complex phasing based on amount thresholds
    """

    def __init__(self, years_to_track: List[int] = None):
        """
        Initialize the revenue engine.

        Args:
            years_to_track: List of years to calculate revenue for.
                           Defaults to current year, Y+1, Y+2, Y+3.
                           For backfills, should be [Y, Y+1, Y+2, Y+3] to track up to +3 years.
        """
        if years_to_track is None:
            current_year = datetime.now().year
            self.years_to_track = [current_year, current_year + 1, current_year + 2, current_year + 3]
        else:
            self.years_to_track = sorted(years_to_track)

    def get_financial_columns(self) -> List[str]:
        """
        Get list of all financial columns that will be created.

        Returns:
            List of column names for annual and quarterly totals
        """
        columns = []
        for year in self.years_to_track:
            columns.append(f'Montant Total {year}')
            columns.append(f'Montant Pondéré {year}')
            for quarter in range(1, 5):
                columns.append(f'Montant Total Q{quarter}_{year}')
                columns.append(f'Montant Pondéré Q{quarter}_{year}')
        return columns

    @staticmethod
    def get_quarter(month: int) -> int:
        """
        Get quarter number from month.

        Args:
            month: Month number (1-12)

        Returns:
            Quarter number (1-4)
        """
        return (month - 1) // 3 + 1

    @staticmethod
    def _iter_calendar_month_starts(start: pd.Timestamp, stop: pd.Timestamp) -> List[pd.Timestamp]:
        """
        Build a list of month-start timestamps covering the inclusive range of
        calendar months between start and stop.

        Important: We intentionally ignore day-of-month when iterating months.
        A project from 2025-10-20 to 2025-12-19 should allocate across Oct/Nov/Dec
        (3 months). Using `current = start; current += DateOffset(months=1)` would
        skip the last month whenever stop.day < start.day, causing amount loss.
        """
        if pd.isna(start) or pd.isna(stop):
            return []
        # Normalize to first day of each month to make iteration stable
        start_month = pd.Timestamp(start.year, start.month, 1)
        stop_month = pd.Timestamp(stop.year, stop.month, 1)
        if stop_month < start_month:
            return []
        return list(pd.date_range(start=start_month, end=stop_month, freq="MS"))

    def _spread_maintenance(
        self,
        amount: float,
        start: pd.Timestamp,
        stop: pd.Timestamp
    ) -> List[RevenueAllocation]:
        """
        Spread MAINTENANCE revenue evenly over duration.

        Args:
            amount: Total project amount
            start: Project start date
            stop: Project end date

        Returns:
            List of monthly revenue allocations
        """
        months = self._iter_calendar_month_starts(start, stop)
        if not months:
            return []
        monthly_amount = amount / len(months)
        return [
            RevenueAllocation(year=m.year, month=m.month, amount=monthly_amount)
            for m in months
        ]

    def _spread_travaux(
        self,
        amount: float,
        start: pd.Timestamp,
        stop: pd.Timestamp
    ) -> List[RevenueAllocation]:
        """
        Spread TRAVAUX revenue based on duration.

        Rules:
        - Duration < 1 month: 100% on start date
        - Duration >= 1 month: Spread evenly

        Args:
            amount: Total project amount
            start: Project start date
            stop: Project end date

        Returns:
            List of monthly revenue allocations
        """
        # Define "short" as within a single calendar month
        if start.year == stop.year and start.month == stop.month:
            # Short project: 100% on start
            return [RevenueAllocation(year=start.year, month=start.month, amount=amount)]

        months = self._iter_calendar_month_starts(start, stop)
        if not months:
            return []
        monthly_amount = amount / len(months)
        return [
            RevenueAllocation(year=m.year, month=m.month, amount=monthly_amount)
            for m in months
        ]

    def _spread_conception(
        self,
        amount: float,
        start: pd.Timestamp
    ) -> List[RevenueAllocation]:
        """
        Spread CONCEPTION revenue with complex phasing.

        Rules based on amount thresholds:
        - < 15k€: 1/3 per month for 3 months
        - 15k€ - 30k€: 60% over 6 months, 6-month pause, 40% over 6 months
        - > 30k€: 40% over 12 months, 6-month pause, 60% over 12 months

        Args:
            amount: Total project amount
            start: Project start date

        Returns:
            List of monthly revenue allocations
        """
        allocations = []
        current = start

        if amount < CONCEPTION_THRESHOLD_LOW:
            # Small: 1/3 per month for 3 months
            monthly_amount = amount / 3
            for _ in range(3):
                allocations.append(RevenueAllocation(
                    year=current.year,
                    month=current.month,
                    amount=monthly_amount
                ))
                current = current + pd.DateOffset(months=1)

        elif amount <= CONCEPTION_THRESHOLD_HIGH:
            # Medium: 60% over 6 months
            phase1_monthly = (amount * 0.60) / 6
            for _ in range(6):
                allocations.append(RevenueAllocation(
                    year=current.year,
                    month=current.month,
                    amount=phase1_monthly
                ))
                current = current + pd.DateOffset(months=1)

            # 6-month pause
            current = current + pd.DateOffset(months=6)

            # 40% over 6 months
            phase2_monthly = (amount * 0.40) / 6
            for _ in range(6):
                allocations.append(RevenueAllocation(
                    year=current.year,
                    month=current.month,
                    amount=phase2_monthly
                ))
                current = current + pd.DateOffset(months=1)

        else:
            # Large: 40% over 12 months
            phase1_monthly = (amount * 0.40) / 12
            for _ in range(12):
                allocations.append(RevenueAllocation(
                    year=current.year,
                    month=current.month,
                    amount=phase1_monthly
                ))
                current = current + pd.DateOffset(months=1)

            # 6-month pause
            current = current + pd.DateOffset(months=6)

            # 60% over 12 months
            phase2_monthly = (amount * 0.60) / 12
            for _ in range(12):
                allocations.append(RevenueAllocation(
                    year=current.year,
                    month=current.month,
                    amount=phase2_monthly
                ))
                current = current + pd.DateOffset(months=1)

        return allocations

    def _compute_effective_dates(
        self,
        row: pd.Series
    ) -> Tuple[pd.Timestamp, pd.Timestamp, bool, str]:
        """
        Compute effective start/stop dates based on Rules 1-3.

        Rules:
        - Rule 1: If only start missing
          - MAINTENANCE: end = projet_stop, start = end - 11 months
          - TRAVAUX: start = date, end = projet_stop (even monthly spread)
          - CONCEPTION: start = date (existing phasing rules)
        - Rule 2: If only end missing
          - MAINTENANCE: start = projet_start, end = start + 11 months
          - TRAVAUX: start = projet_start, end = start + 5 months
          - CONCEPTION: unchanged (start-only)
        - Rule 3: If both missing
          - MAINTENANCE: start = date, end = start + 11 months
          - TRAVAUX: start = date, end = start + 5 months
          - CONCEPTION: start = date (existing phasing rules)

        Args:
            row: DataFrame row with projet_start, projet_stop, date, final_bu

        Returns:
            Tuple of (effective_start, effective_stop, rule_applied, rule_name)
        """
        bu = row.get('final_bu', 'AUTRE')
        start = row.get('projet_start')
        stop = row.get('projet_stop')
        proposal_date = row.get('date')

        start_missing = pd.isna(start)
        stop_missing = pd.isna(stop)
        proposal_date_valid = not pd.isna(proposal_date)

        # If both dates are present and valid, no rule needed
        if not start_missing and not stop_missing:
            if start <= stop:
                return start, stop, False, "none"
            # Invalid: start > stop, treat as both missing
            start_missing = True
            stop_missing = True

        # If proposal_date is also missing, we can't apply any rule
        if not proposal_date_valid:
            return pd.NaT, pd.NaT, False, "missing_all_dates"

        rule_applied = True

        # Rule 1: Only start missing
        if start_missing and not stop_missing:
            if bu == 'MAINTENANCE':
                # Use end_date and span until 12 months before (so start = end - 11 months)
                effective_start = stop - pd.DateOffset(months=11)
                effective_stop = stop
                return effective_start, effective_stop, rule_applied, "rule1_start_missing_maintenance"
            elif bu == 'TRAVAUX':
                # Price concentrated between date and end_date (even monthly spread)
                effective_start = proposal_date
                effective_stop = stop
                return effective_start, effective_stop, rule_applied, "rule1_start_missing_travaux"
            elif bu == 'CONCEPTION':
                # Use date column as reference (existing phasing rules)
                effective_start = proposal_date
                effective_stop = pd.NaT  # CONCEPTION doesn't need stop
                return effective_start, effective_stop, rule_applied, "rule1_start_missing_conception"
            else:
                # Default: treat as TRAVAUX
                effective_start = proposal_date
                effective_stop = stop
                return effective_start, effective_stop, rule_applied, "rule1_start_missing_default"

        # Rule 2: Only end missing
        elif not start_missing and stop_missing:
            if bu == 'MAINTENANCE':
                # Span until 1 year (12 months) later
                effective_start = start
                effective_stop = start + pd.DateOffset(months=11)
                return effective_start, effective_stop, rule_applied, "rule2_end_missing_maintenance"
            elif bu == 'TRAVAUX':
                # Do until 6 months from start_date
                effective_start = start
                effective_stop = start + pd.DateOffset(months=5)
                return effective_start, effective_stop, rule_applied, "rule2_end_missing_travaux"
            elif bu == 'CONCEPTION':
                # Unchanged (start-only, existing rules work)
                effective_start = start
                effective_stop = pd.NaT
                return effective_start, effective_stop, False, "none"  # CONCEPTION doesn't need end, so no rule applied
            else:
                # Default: treat as TRAVAUX
                effective_start = start
                effective_stop = start + pd.DateOffset(months=5)
                return effective_start, effective_stop, rule_applied, "rule2_end_missing_default"

        # Rule 3: Both missing
        elif start_missing and stop_missing:
            if bu == 'MAINTENANCE':
                # Use date column and span until 1 year (12 months) later
                effective_start = proposal_date
                effective_stop = proposal_date + pd.DateOffset(months=11)
                return effective_start, effective_stop, rule_applied, "rule3_both_missing_maintenance"
            elif bu == 'TRAVAUX':
                # Do until 6 months from date column
                effective_start = proposal_date
                effective_stop = proposal_date + pd.DateOffset(months=5)
                return effective_start, effective_stop, rule_applied, "rule3_both_missing_travaux"
            elif bu == 'CONCEPTION':
                # Use date column as reference (existing phasing rules)
                effective_start = proposal_date
                effective_stop = pd.NaT
                return effective_start, effective_stop, rule_applied, "rule3_both_missing_conception"
            else:
                # Default: treat as TRAVAUX
                effective_start = proposal_date
                effective_stop = proposal_date + pd.DateOffset(months=5)
                return effective_start, effective_stop, rule_applied, "rule3_both_missing_default"

        # Should not reach here
        return pd.NaT, pd.NaT, False, "unknown"

    def _clamp_allocation_to_window(
        self,
        alloc: RevenueAllocation,
        first_year: int,
        last_year: int
    ) -> RevenueAllocation:
        """
        Clamp allocation to tracked window (Rule 4).

        If allocation is before the window, move to first month of first year.
        If allocation is after the window, move to last month of last year.

        Args:
            alloc: Original allocation
            first_year: First year in tracked window
            last_year: Last year in tracked window

        Returns:
            Clamped allocation
        """
        if alloc.year < first_year:
            # Before window: move to first month of first year
            return RevenueAllocation(
                year=first_year,
                month=1,
                amount=alloc.amount
            )
        elif alloc.year > last_year:
            # After window: move to last month of last year
            return RevenueAllocation(
                year=last_year,
                month=12,
                amount=alloc.amount
            )
        else:
            # Within window: no change
            return alloc

    def calculate_revenue(self, row: pd.Series) -> Dict[str, float]:
        """
        Calculate all revenue columns for a single proposal row.

        Implements Rules 1-4:
        - Rules 1-3: Date replacement when dates are missing
        - Rule 4: Clamp allocations outside tracked window

        Args:
            row: DataFrame row with amount, final_bu, projet_start, projet_stop, date, probability_factor

        Returns:
            Dictionary of column names to values (including dates_rule_applied, dates_rule, dates_effective_start, dates_effective_stop)
        """
        # Initialize all columns to 0
        result = {col: 0.0 for col in self.get_financial_columns()}
        # Add flagging columns
        result['dates_rule_applied'] = False
        result['dates_rule'] = 'none'
        result['dates_effective_start'] = pd.NaT
        result['dates_effective_stop'] = pd.NaT

        amount = row.get('amount', 0)
        if amount == 0:
            return result

        prob_factor = row.get('probability_factor', 0.5)
        bu = row.get('final_bu', 'AUTRE')

        # Compute effective dates (Rules 1-3)
        effective_start, effective_stop, rule_applied, rule_name = self._compute_effective_dates(row)

        # Store flagging info
        result['dates_rule_applied'] = rule_applied
        result['dates_rule'] = rule_name
        result['dates_effective_start'] = effective_start
        result['dates_effective_stop'] = effective_stop

        # If we still don't have a valid start date, we can't allocate
        if pd.isna(effective_start):
            return result

        # Get window bounds for clamping (Rule 4)
        first_year = min(self.years_to_track) if self.years_to_track else datetime.now().year
        last_year = max(self.years_to_track) if self.years_to_track else datetime.now().year + 3

        # Calculate monthly allocations based on BU
        if bu == 'CONCEPTION':
            allocations = self._spread_conception(amount, effective_start)
        else:
            # For duration-based BUs, ensure we have a valid stop
            if pd.isna(effective_stop) or effective_stop < effective_start:
                # Fallback: 1-month project
                effective_stop = effective_start
                if rule_applied:
                    # Update rule name to indicate fallback
                    result['dates_rule'] = f"{rule_name}_fallback_1month"

            if bu == 'MAINTENANCE':
                allocations = self._spread_maintenance(amount, effective_start, effective_stop)
            elif bu == 'TRAVAUX':
                allocations = self._spread_travaux(amount, effective_start, effective_stop)
            else:
                # Default: treat as TRAVAUX
                allocations = self._spread_travaux(amount, effective_start, effective_stop)

        # Apply Rule 4: Clamp allocations outside window
        clamped_allocations = []
        for alloc in allocations:
            clamped = self._clamp_allocation_to_window(alloc, first_year, last_year)
            clamped_allocations.append(clamped)

        # Populate columns from clamped allocations
        for alloc in clamped_allocations:
            if alloc.year in self.years_to_track:
                quarter = self.get_quarter(alloc.month)

                # Total amounts
                result[f'Montant Total {alloc.year}'] += alloc.amount
                result[f'Montant Total Q{quarter}_{alloc.year}'] += alloc.amount

                # Weighted amounts (by probability)
                weighted = alloc.amount * prob_factor
                result[f'Montant Pondéré {alloc.year}'] += weighted
                result[f'Montant Pondéré Q{quarter}_{alloc.year}'] += weighted

        return result

    def process(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Apply revenue calculations to entire DataFrame.

        Args:
            df: Cleaned DataFrame with required columns

        Returns:
            DataFrame with financial columns added (including dates_rule_applied, dates_rule, dates_effective_start, dates_effective_stop)
        """
        if df.empty:
            return df

        df = df.copy()

        # Initialize financial columns
        financial_cols = self.get_financial_columns()
        for col in financial_cols:
            df[col] = 0.0

        # Initialize flagging columns
        df['dates_rule_applied'] = False
        df['dates_rule'] = 'none'
        df['dates_effective_start'] = pd.NaT
        df['dates_effective_stop'] = pd.NaT

        # Calculate for each row
        for index, row in df.iterrows():
            revenue_data = self.calculate_revenue(row)
            for col, value in revenue_data.items():
                df.at[index, col] = value

        return df


def apply_revenue_engine(df: pd.DataFrame, years: List[int] = None) -> pd.DataFrame:
    """
    Convenience function to apply revenue engine to DataFrame.

    Args:
        df: Cleaned proposals DataFrame
        years: Years to track (defaults to current +2)

    Returns:
        DataFrame with financial columns added
    """
    engine = RevenueEngine(years_to_track=years)
    return engine.process(df)
