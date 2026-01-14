"""
Data Cleaning & Normalization Module

Handles all data cleaning, date parsing, numeric conversion,
and business unit assignment logic including the TS rule.
"""

import re
import pandas as pd
import numpy as np
from typing import Any, List, Optional
from datetime import datetime

from config.settings import (
    VIP_COMMERCIALS,
    BU_MAINTENANCE_KEYWORDS,
    BU_TRAVAUX_KEYWORDS,
    BU_CONCEPTION_KEYWORDS,
    EXCLUDED_OWNERS
)


class DataCleaner:
    """
    Cleans and normalizes raw proposal data from the Furious API.

    Handles:
    - String field cleaning (list handling, NaN replacement)
    - Date parsing with edge case handling
    - Numeric conversion
    - Status normalization
    - Business Unit assignment with TS rule
    - Owner resolution for VIP routing
    """

    # Columns that may contain list values
    COLS_TO_CLEAN = ['cf_bu', 'cf_typologie_de_devis', 'cf_typologie_myrium']

    # Date columns to parse
    DATE_COLS = ['date', 'projet_start', 'projet_stop', 'created_at', 'signature_date', 'last_updated_at']

    def __init__(self):
        """Initialize the data cleaner."""
        self.vip_commercials = VIP_COMMERCIALS

    @staticmethod
    def clean_string_field(val: Any) -> str:
        """
        Clean a string field, handling lists and NaN values.

        Args:
            val: Raw value from API (can be list, string, or NaN)

        Returns:
            Cleaned string value
        """
        if isinstance(val, list):
            return ", ".join(str(v) for v in val if v)
        if pd.isna(val) or val is None:
            return "Non défini"
        return str(val).strip()

    @staticmethod
    def parse_date(date_str: Any) -> Optional[pd.Timestamp]:
        """
        Parse a date string with robust error handling.

        Handles:
        - Standard ISO format
        - Empty strings
        - Invalid dates like '0000-00-00'

        Args:
            date_str: Raw date value

        Returns:
            Parsed timestamp or NaT if invalid
        """
        if pd.isna(date_str) or date_str == '' or date_str is None:
            return pd.NaT

        str_val = str(date_str)

        # Check for invalid date patterns
        if str_val.startswith('0000') or str_val == 'None':
            return pd.NaT

        try:
            # Take first 10 chars to handle datetime strings
            return pd.to_datetime(str_val[:10], errors='coerce')
        except Exception:
            return pd.NaT

    @staticmethod
    def assign_bu(row: pd.Series) -> str:
        """
        Assign Business Unit with TS rule priority.

        Rules (in order of priority):
        1. If title contains "TS" (word boundary or parentheses), assign TRAVAUX
        2. If cf_bu contains MAINTENANCE/ENTRETIEN keywords, assign MAINTENANCE
        3. If cf_bu contains TRAVAUX/CHANTIER keywords, assign TRAVAUX
        4. If cf_bu contains CONCEPTION/ETUDE keywords, assign CONCEPTION
        5. Otherwise, use raw value or 'AUTRE'

        Args:
            row: DataFrame row with 'title' and 'cf_bu' columns

        Returns:
            Assigned business unit string
        """
        title = str(row.get('title', '')).upper()
        bu_raw = str(row.get('cf_bu', '')).upper()

        # TS Rule: Priority #1
        # Matches "TS" as a word boundary or in parentheses
        if re.search(r'\bTS\b', title) or "(TS)" in title:
            return "TRAVAUX"

        # Standard BU mapping
        if any(kw in bu_raw for kw in BU_MAINTENANCE_KEYWORDS):
            return "MAINTENANCE"

        if any(kw in bu_raw for kw in BU_TRAVAUX_KEYWORDS):
            return "TRAVAUX"

        if any(kw in bu_raw for kw in BU_CONCEPTION_KEYWORDS):
            return "CONCEPTION"

        # Use raw value if it has content
        if len(bu_raw) > 2 and bu_raw != "NON DÉFINI":
            return bu_raw

        return 'AUTRE'

    def resolve_owner(self, raw_assigned: Any) -> str:
        """
        Resolve the alert owner from assigned_to field.

        VIP Priority:
        - If any VIP is found in the assigned_to field, they become the owner
        - Otherwise, the first person in the list is the owner

        Args:
            raw_assigned: Raw assigned_to value (may contain multiple names)

        Returns:
            Resolved owner identifier
        """
        raw_str = str(raw_assigned).lower()
        potential_owners = re.split(r'[ ,&]+', raw_str)

        # Check for VIP match first
        for owner_part in potential_owners:
            for vip in self.vip_commercials:
                if vip in owner_part:
                    return vip

        # Fall back to first owner
        if potential_owners and potential_owners[0]:
            return potential_owners[0]

        return "unassigned"

    def clean(self, df: pd.DataFrame, skip_excluded_owners: bool = False) -> pd.DataFrame:
        """
        Apply all cleaning transformations to a proposals DataFrame.

        Args:
            df: Raw DataFrame from API
            skip_excluded_owners: If True, skip filtering out excluded owners (default: False)

        Returns:
            Cleaned DataFrame with all transformations applied
        """
        if df.empty:
            return df

        df = df.copy()

        # 1. Clean string fields (handle lists, NaN)
        for col in self.COLS_TO_CLEAN:
            if col in df.columns:
                df[col] = df[col].apply(self.clean_string_field)
            else:
                df[col] = "Non défini"

        # 2. Parse all date columns
        for col in self.DATE_COLS:
            if col in df.columns:
                df[col] = df[col].apply(self.parse_date)

        # 3. Convert numerics
        df['amount'] = pd.to_numeric(df.get('amount'), errors='coerce').fillna(0)
        df['probability'] = pd.to_numeric(df.get('probability'), errors='coerce').fillna(50)

        # Probability calculation (default 50% if 0)
        df['probability_calc'] = df['probability'].apply(lambda x: 50 if x == 0 else x)
        df['probability_factor'] = df['probability_calc'] / 100.0

        # 4. Normalize status
        if 'statut' in df.columns:
            df['statut_clean'] = df['statut'].astype(str).str.lower().str.strip()
        else:
            df['statut_clean'] = 'unknown'

        # 5. Assign Business Unit with TS rule
        df['final_bu'] = df.apply(self.assign_bu, axis=1)

        # Overwrite original cf_bu with final assignment
        df['cf_bu'] = df['final_bu']

        # 6. Resolve alert owner (VIP routing)
        if 'assigned_to' in df.columns:
            df['alert_owner'] = df['assigned_to'].apply(self.resolve_owner)
        else:
            df['alert_owner'] = 'unassigned'

        # 7. Effective won date (for filtering won proposals by month)
        if 'signature_date' in df.columns:
            df['date_effective_won'] = df['signature_date']
        else:
            df['date_effective_won'] = pd.NaT

        # 8. Filter out excluded owners (e.g., former employees) - unless skip_excluded_owners is True
        if not skip_excluded_owners and 'assigned_to' in df.columns:
            # Create a mask to exclude proposals from excluded owners
            excluded_mask = df['assigned_to'].astype(str).str.lower().isin(
                [owner.lower() for owner in EXCLUDED_OWNERS]
            )
            # Also check if assigned_to contains any excluded owner name
            for excluded_owner in EXCLUDED_OWNERS:
                excluded_mask |= df['assigned_to'].astype(str).str.lower().str.contains(
                    excluded_owner.lower(), na=False, regex=False
                )

            # Filter out excluded proposals
            df = df[~excluded_mask].copy()

            if excluded_mask.sum() > 0:
                print(f"  Filtered out {excluded_mask.sum()} proposal(s) from excluded owners: {EXCLUDED_OWNERS}")
        elif skip_excluded_owners:
            print(f"  Skipping excluded owners filter (including all proposals)")

        return df

    def get_internal_columns(self) -> List[str]:
        """
        Get list of internal columns that should be dropped before output.

        Returns:
            List of column names to drop
        """
        return [
            'statut_clean',
            'probability_calc',
            'probability_factor',
            'date_effective_won',
            'alert_owner',
            'final_bu'
        ]


def clean_proposals(df: pd.DataFrame) -> pd.DataFrame:
    """
    Convenience function to clean proposals DataFrame.

    Args:
        df: Raw DataFrame from API

    Returns:
        Cleaned DataFrame
    """
    cleaner = DataCleaner()
    return cleaner.clean(df)
