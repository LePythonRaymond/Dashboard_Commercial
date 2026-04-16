"""
Furious API Proposal Addons (Avenants) Client

Fetches all proposal addons from the Furious CRM with pagination support.
Each addon is linked to a parent proposal via the `id` field (proposal ID).
"""

import requests
import pandas as pd
from typing import List, Dict, Any, Optional

from config.settings import settings
from .auth import FuriousAuth, AuthenticationError


# Fields to fetch from the ProposalAddon API
ADDON_FIELDS = [
    "id_system",
    "id",          # parent proposal ID
    "amount",
    "status",
    "title",
    "date",
]


class ProposalAddonsClient:
    """
    Client for fetching proposal addons (avenants) from Furious CRM.

    Handles paginated requests to fetch all addons with status filtering.
    """

    def __init__(self, auth: Optional[FuriousAuth] = None):
        self.auth = auth or FuriousAuth()
        self.endpoint = f"{settings.furious_api_url}/proposal-addon/"
        self.page_limit = settings.proposals_page_limit
        self.fields = ADDON_FIELDS

    def _build_query(self, offset: int = 0) -> str:
        fields_str = ",".join(self.fields)
        # Only fetch validated addons: status 1 (billing) or 2 (validated)
        query = f"""{{
  ProposalAddon(
    limit: {self.page_limit},
    offset: {offset},
    order: [{{date:desc}}],
    filter: {{status:{{in:["1","2"]}}}}
  ){{
    {fields_str}
  }}
}}"""
        return query

    def _fetch_page(self, offset: int = 0) -> Dict[str, Any]:
        query = self._build_query(offset)
        url = f"{self.endpoint}?query={requests.utils.quote(query)}"

        try:
            response = requests.get(
                url,
                headers=self.auth.get_headers(),
                timeout=settings.api_timeout,
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            raise ProposalAddonsAPIError(f"Failed to fetch addons at offset {offset}: {e}")

    def fetch_all(self) -> pd.DataFrame:
        """
        Fetch all validated proposal addons with automatic pagination.

        Returns:
            DataFrame with columns: id_system, id (proposal_id), amount, status, title, date
        """
        all_addons: List[Dict] = []
        offset = 0

        print(f"Starting to fetch proposal addons from {self.endpoint}")

        while True:
            print(f"  Fetching addons offset {offset}...")
            response = self._fetch_page(offset)

            if not response.get("success", False):
                error = response.get("errors", response.get("message", "Unknown error"))
                raise ProposalAddonsAPIError(f"Addons API returned error: {error}")

            addons = response.get("data", {}).get("ProposalAddon", [])

            if not addons:
                print(f"  No more addons at offset {offset}. Done.")
                break

            all_addons.extend(addons)
            print(f"  Retrieved {len(addons)} addon(s) (total: {len(all_addons)})")

            if len(addons) < self.page_limit:
                print(f"  Got {len(addons)} < {self.page_limit} (last page). Done.")
                break

            meta = response.get("meta", {})
            total_elements = meta.get("totalElementsWithFilters", meta.get("totalElements", 0))
            if total_elements and len(all_addons) >= total_elements:
                print(f"  Reached total of {total_elements} addons. Done.")
                break

            offset += self.page_limit

        print(f"Total addons fetched: {len(all_addons)}")

        if not all_addons:
            return pd.DataFrame()

        df = pd.DataFrame(all_addons)
        # Ensure amount is numeric
        if 'amount' in df.columns:
            df['amount'] = pd.to_numeric(df['amount'], errors='coerce').fillna(0)
        return df


def aggregate_addons_by_proposal(df_addons: pd.DataFrame) -> pd.Series:
    """
    Group addons by parent proposal ID and sum their amounts.

    Args:
        df_addons: DataFrame from ProposalAddonsClient.fetch_all()

    Returns:
        Series mapping proposal_id (str) -> total addon amount
    """
    if df_addons.empty or 'id' not in df_addons.columns:
        return pd.Series(dtype=float)
    grouped = df_addons.groupby(df_addons['id'].astype(str))['amount'].sum()
    return grouped


class ProposalAddonsAPIError(Exception):
    """Raised when the Proposal Addons API request fails."""
    pass
