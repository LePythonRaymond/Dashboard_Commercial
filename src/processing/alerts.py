"""
Alerts Generator Module

Generates two types of alerts:
1. Weird Proposals: Data quality issues (low amount, missing dates, etc.)
2. Commercial Follow-up: Proposals needing attention based on date windows
"""

import pandas as pd
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta
from dataclasses import dataclass, field

from config.settings import (
    STATUS_WAITING,
    ALERT_FOLLOWUP_DAYS_FORWARD
)
from .views import ViewGenerator


@dataclass
class WeirdProposalAlert:
    """Represents a single weird proposal alert."""
    title: str
    amount: float
    reason: str


@dataclass
class FollowupAlert:
    """Represents a single commercial follow-up alert."""
    title: str
    date: str
    projet_start: str
    amount: float
    statut: str
    probability: float


@dataclass
class AlertsOutput:
    """Container for all generated alerts."""
    weird_proposals: Dict[str, List[Dict[str, Any]]]
    commercial_followup: Dict[str, List[Dict[str, Any]]]
    count_weird: int
    count_followup: int


class AlertsGenerator:
    """
    Generates alerts for data quality and commercial follow-up.

    Weird Proposals:
    - Only proposals in active views (Snapshot/Sent/Won)
    - Triggers: Missing dates, Start > End, Probability = 0%

    Commercial Follow-up:
    - Status must be WAITING
    - Date window: 1st of Previous Month → Today + 60 days
    - Different date reference for CONCEPTION vs TRAVAUX/MAINTENANCE
    """

    def __init__(
        self,
        reference_date: datetime = None,
        followup_days_forward: int = ALERT_FOLLOWUP_DAYS_FORWARD,
        followup_days_forward_by_owner: Optional[Dict[str, int]] = None
    ):
        """
        Initialize the alerts generator.

        Args:
            reference_date: Date for window calculations. Defaults to now.
            followup_days_forward: Default forward window in days. Defaults to ALERT_FOLLOWUP_DAYS_FORWARD.
            followup_days_forward_by_owner: Optional dict mapping owner identifiers to custom forward window days.
                If provided, owners in this dict will use their custom window instead of the default.
        """
        self.today = reference_date or datetime.now()

        # Calculate date windows
        first_of_month = self.today.replace(day=1)
        prev_month_end = first_of_month - timedelta(days=1)

        self.window_start = prev_month_end.replace(day=1)  # 1st of prev month
        self.default_window_end = self.today + timedelta(days=followup_days_forward)

        # Store owner-specific forward windows if provided
        self.followup_days_forward_by_owner = followup_days_forward_by_owner or {}

        # Create view generator for combined mask
        self.view_generator = ViewGenerator(self.today)

    @staticmethod
    def _format_date(dt: Any) -> Optional[str]:
        """Format date for JSON output."""
        if pd.isna(dt):
            return None
        if isinstance(dt, (datetime, pd.Timestamp)):
            return dt.strftime('%Y-%m-%d')
        return str(dt)

    def _get_weird_reason(self, row: pd.Series) -> str:
        """
        Determine the reason(s) a proposal is flagged as weird.

        Args:
            row: DataFrame row

        Returns:
            Pipe-separated string of reasons, or empty if not weird
        """
        reasons = []

        # Date checks
        if pd.isna(row.get('projet_start')):
            reasons.append("Date début manquante")

        if pd.isna(row.get('projet_stop')):
            reasons.append("Date fin manquante")

        # Date order check
        start = row.get('projet_start')
        stop = row.get('projet_stop')
        if not pd.isna(start) and not pd.isna(stop) and start > stop:
            reasons.append("Date début > Date fin")

        # Probability check
        if row.get('probability', 0) == 0:
            reasons.append("Probabilité 0%")

        return " | ".join(reasons)

    def _get_window_end_for_owner(self, owner: str) -> datetime:
        """
        Get the forward window end date for a specific owner.

        Args:
            owner: Owner identifier

        Returns:
            Window end datetime (owner-specific if configured, otherwise default)
        """
        if owner in self.followup_days_forward_by_owner:
            custom_days = self.followup_days_forward_by_owner[owner]
            return self.today + timedelta(days=custom_days)
        return self.default_window_end

    def _needs_followup(self, row: pd.Series) -> bool:
        """
        Determine if a proposal needs commercial follow-up.

        Logic differs by BU:
        - CONCEPTION: Uses 'date' (proposal date) for both backward/forward checks
        - TRAVAUX/MAINTENANCE: Uses OR rule: 'date' <= window_end OR 'projet_start' <= window_end

        The forward window can be owner-specific if configured via followup_days_forward_by_owner.

        Args:
            row: DataFrame row

        Returns:
            True if proposal is in the follow-up window
        """
        bu = row.get('final_bu', row.get('cf_bu', ''))
        d_date = row.get('date')
        d_start = row.get('projet_start')
        owner = row.get('alert_owner', 'unassigned')

        # Get owner-specific window end (or default)
        window_end = self._get_window_end_for_owner(owner)

        # Backward check (common): 'date' must be >= start of window
        if pd.isna(d_date) or d_date < pd.Timestamp(self.window_start):
            return False

        # Forward check (split by BU)
        if bu == 'CONCEPTION':
            # CONCEPTION: use 'date' for forward check
            if d_date > pd.Timestamp(window_end):
                return False
        else:
            # TRAVAUX / MAINTENANCE: OR rule - either date or projet_start within window
            window_end_ts = pd.Timestamp(window_end)
            date_in_window = not pd.isna(d_date) and d_date <= window_end_ts
            start_in_window = not pd.isna(d_start) and d_start <= window_end_ts

            # Pass if either date is within window
            if not (date_in_window or start_in_window):
                return False

        return True

    def generate_weird_alerts(self, df: pd.DataFrame) -> Dict[str, List[Dict[str, Any]]]:
        """
        Generate weird proposal alerts grouped by owner.

        Args:
            df: Processed DataFrame

        Returns:
            Dictionary mapping owner to list of alert dicts
        """
        # Only check proposals in active views
        mask_tracked = self.view_generator.get_combined_mask(df)
        df_tracked = df[mask_tracked].copy()

        if df_tracked.empty:
            return {}

        # Add weird reason column
        df_tracked['flag_reason'] = df_tracked.apply(self._get_weird_reason, axis=1)

        # Filter to only weird proposals
        df_weird = df_tracked[df_tracked['flag_reason'] != ""].copy()

        if df_weird.empty:
            return {}

        # Group by owner
        alerts_grouped: Dict[str, List[Dict]] = {}

        for _, row in df_weird.iterrows():
            owner = row.get('alert_owner', 'unassigned')

            if owner not in alerts_grouped:
                alerts_grouped[owner] = []

            alerts_grouped[owner].append({
                'id': str(row.get('id', '')),
                'title': row.get('title', 'Unknown'),
                'company_name': row.get('company_name', 'N/A'),
                'amount': row.get('amount', 0),
                'statut': row.get('statut', 'Unknown'),
                'probability': row.get('probability', 0),
                'sign_url': row.get('sign_url', ''),
                'assigned_to': row.get('assigned_to', 'N/A'),
                'date': self._format_date(row.get('date')),
                'projet_start': self._format_date(row.get('projet_start')),
                'projet_stop': self._format_date(row.get('projet_stop')),
                'signature_date': self._format_date(row.get('signature_date')),
                'created_at': self._format_date(row.get('created_at')),
                'reason': row['flag_reason'],
                'alert_owner': owner
            })

        return alerts_grouped

    def generate_followup_alerts(self, df: pd.DataFrame) -> Dict[str, List[Dict[str, Any]]]:
        """
        Generate commercial follow-up alerts grouped by owner.

        Args:
            df: Processed DataFrame

        Returns:
            Dictionary mapping owner to list of alert dicts
        """
        # Filter to only WAITING status (source is Snapshot)
        mask_waiting = df['statut_clean'].isin(STATUS_WAITING)
        df_waiting = df[mask_waiting].copy()

        if df_waiting.empty:
            return {}

        # Apply time window filter
        mask_followup = df_waiting.apply(self._needs_followup, axis=1)
        df_followup = df_waiting[mask_followup].copy()

        if df_followup.empty:
            return {}

        # Group by owner
        alerts_grouped: Dict[str, List[Dict]] = {}

        for _, row in df_followup.iterrows():
            owner = row.get('alert_owner', 'unassigned')

            if owner not in alerts_grouped:
                alerts_grouped[owner] = []

            alerts_grouped[owner].append({
                'id': str(row.get('id', '')),
                'title': row.get('title', 'Unknown'),
                'company_name': row.get('company_name', 'N/A'),
                'date': self._format_date(row.get('date')),
                'projet_start': self._format_date(row.get('projet_start')),
                'projet_stop': self._format_date(row.get('projet_stop')),
                'amount': row.get('amount', 0),
                'statut': row.get('statut', 'Unknown'),
                'probability': row.get('probability', 0),
                'sign_url': row.get('sign_url', ''),
                'assigned_to': row.get('assigned_to', 'N/A'),
                'alert_owner': owner
            })

        return alerts_grouped

    def generate(self, df: pd.DataFrame) -> AlertsOutput:
        """
        Generate all alerts from processed DataFrame.

        Args:
            df: Fully processed DataFrame

        Returns:
            AlertsOutput containing all alerts
        """
        weird = self.generate_weird_alerts(df)
        followup = self.generate_followup_alerts(df)

        # Count total items
        count_weird = sum(len(items) for items in weird.values())
        count_followup = sum(len(items) for items in followup.values())

        return AlertsOutput(
            weird_proposals=weird,
            commercial_followup=followup,
            count_weird=count_weird,
            count_followup=count_followup
        )


def generate_alerts(df: pd.DataFrame, reference_date: datetime = None) -> AlertsOutput:
    """
    Convenience function to generate all alerts.

    Args:
        df: Processed proposals DataFrame
        reference_date: Optional reference date for calculations

    Returns:
        AlertsOutput with all alerts
    """
    generator = AlertsGenerator(reference_date)
    return generator.generate(df)
