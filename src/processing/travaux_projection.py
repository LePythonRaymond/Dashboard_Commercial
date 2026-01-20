"""
TRAVAUX Projection Generator Module

Filters high-probability TRAVAUX proposals for the "Projection Travaux prochains 12 mois" feature.
Identifies proposals that are likely to be signed to help fill calendar gaps.
"""

import pandas as pd
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta

from config.settings import (
    STATUS_WAITING,
    TRAVAUX_PROJECTION_PROBABILITY_THRESHOLD,
    TRAVAUX_PROJECTION_START_WINDOW
)


class TravauxProjectionGenerator:
    """
    Filters high-probability TRAVAUX proposals for projection.

    Filtering Criteria:
    - BU = TRAVAUX (includes TS rule via final_bu column)
    - Status = WAITING (not yet signed)
    - Probability >= threshold (configurable)
    - date OR projet_start within rolling 365 days (today <= date <= today + 365 days OR today <= projet_start <= today + 365 days)
    """

    def __init__(self, reference_date: datetime = None):
        """
        Initialize the projection generator.

        Args:
            reference_date: Date for window calculations. Defaults to now.
        """
        self.today = reference_date or datetime.now()

        # Calculate rolling 365-day window for date and projet_start
        self.window_end = self.today + timedelta(days=TRAVAUX_PROJECTION_START_WINDOW)

    @staticmethod
    def _format_date(dt: Any) -> Optional[str]:
        """Format date for output (YYYY-MM-DD)."""
        if pd.isna(dt):
            return None
        if isinstance(dt, (datetime, pd.Timestamp)):
            return dt.strftime('%Y-%m-%d')
        return str(dt)

    def _build_furious_url(self, proposal_id: str) -> str:
        """
        Build Furious URL from proposal ID.

        Args:
            proposal_id: The proposal ID from Furious

        Returns:
            Full URL to the proposal in Furious
        """
        if not proposal_id:
            return ''
        return f"https://merciraymond.furious-squad.com/compta.php?view=5&cherche={proposal_id}"

    def _matches_criteria(self, row: pd.Series) -> bool:
        """
        Check if a proposal matches the TRAVAUX projection criteria.

        Args:
            row: DataFrame row

        Returns:
            True if proposal matches all criteria
        """
        # Filter 1: BU must be TRAVAUX
        final_bu = row.get('final_bu', '')
        if final_bu != 'TRAVAUX':
            return False

        # Filter 2: Status must be WAITING
        statut_clean = row.get('statut_clean', '')
        if statut_clean not in STATUS_WAITING:
            return False

        # Filter 3: Probability >= threshold
        probability = row.get('probability', 0)
        if probability < TRAVAUX_PROJECTION_PROBABILITY_THRESHOLD:
            return False

        # Filter 4: date OR projet_start must be within rolling 365-day window
        date_val = row.get('date')
        start_date_val = row.get('projet_start')

        today_ts = pd.Timestamp(self.today)
        window_end_ts = pd.Timestamp(self.window_end)

        # Check if date is within window
        date_in_window = False
        if not pd.isna(date_val):
            date_ts = pd.Timestamp(date_val)
            date_in_window = today_ts <= date_ts <= window_end_ts

        # Check if projet_start is within window
        start_in_window = False
        if not pd.isna(start_date_val):
            start_ts = pd.Timestamp(start_date_val)
            start_in_window = today_ts <= start_ts <= window_end_ts

        # Must match at least one date criterion (OR logic)
        if not (date_in_window or start_in_window):
            return False

        return True

    def generate(self, df: pd.DataFrame) -> List[Dict[str, Any]]:
        """
        Generate list of TRAVAUX proposals matching projection criteria.

        Args:
            df: Fully processed DataFrame (after cleaning and revenue engine)

        Returns:
            List of proposal dictionaries with required fields
        """
        if df.empty:
            return []

        # Apply all filters
        mask = df.apply(self._matches_criteria, axis=1)
        df_filtered = df[mask].copy()

        if df_filtered.empty:
            return []

        # Convert to list of dictionaries
        proposals = []
        for _, row in df_filtered.iterrows():
            proposal_id = str(row.get('id', ''))
            proposals.append({
                'id': proposal_id,
                'title': row.get('title', 'Unknown'),
                'company_name': row.get('company_name', 'N/A'),
                'amount': float(row.get('amount', 0)),
                'assigned_to': row.get('assigned_to', 'N/A'),
                'date': self._format_date(row.get('date')),
                'projet_start': self._format_date(row.get('projet_start')),
                'probability': float(row.get('probability', 0)),
                'furious_url': self._build_furious_url(proposal_id)
            })

        return proposals


def generate_travaux_projection(df: pd.DataFrame, reference_date: datetime = None) -> List[Dict[str, Any]]:
    """
    Convenience function to generate TRAVAUX projection.

    Args:
        df: Processed proposals DataFrame
        reference_date: Optional reference date for calculations

    Returns:
        List of proposal dictionaries
    """
    generator = TravauxProjectionGenerator(reference_date)
    return generator.generate(df)
