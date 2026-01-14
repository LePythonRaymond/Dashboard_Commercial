"""
Furious API Proposals Client

Fetches all proposals from the Furious CRM with pagination support.
"""

import requests
import pandas as pd
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

from config.settings import settings
from .auth import FuriousAuth, AuthenticationError


@dataclass
class ProposalFields:
    """Defines the fields to fetch from the Proposals API."""

    FIELDS: List[str] = None

    def __post_init__(self):
        self.FIELDS = [
            "id",
            "date",
            "title",
            "amount",
            "discount",
            "vat",
            "currency",
            "assigned_to",
            "client_id",
            "opportunity_id",
            "statut",
            "pipe",
            "pipe_name",
            "created_at",
            "last_updated_at",
            "legal_entity",
            "company_name",
            "id_furious",
            "total_sold_days",
            "total_cost",
            "probability",
            "entity",
            "projet_start",
            "projet_stop",
            "sign_url",
            "cf_typologie_de_devis",
            "cf_typologie_myrium",
            "cf_bu",
            "signature_date"
        ]


class ProposalsClient:
    """
    Client for fetching proposals from Furious CRM.

    Handles paginated requests to fetch all proposals.
    """

    def __init__(self, auth: Optional[FuriousAuth] = None):
        """
        Initialize the proposals client.

        Args:
            auth: FuriousAuth instance (creates new one if not provided)
        """
        self.auth = auth or FuriousAuth()
        self.endpoint = f"{settings.furious_api_url}/proposal/"
        self.page_limit = settings.proposals_page_limit
        self.fields = ProposalFields().FIELDS

    def _build_query(self, offset: int = 0) -> str:
        """
        Build the GraphQL-like query string.

        Args:
            offset: Pagination offset

        Returns:
            Formatted query string
        """
        fields_str = ",".join(self.fields)
        query = f"""{{
  Proposal(
    limit: {self.page_limit},
    offset: {offset},
    order: [{{date:desc}}],
    filter: {{}}
  ){{
    {fields_str}
  }}
}}"""
        return query

    def _fetch_page(self, offset: int = 0) -> Dict[str, Any]:
        """
        Fetch a single page of proposals.

        Args:
            offset: Pagination offset

        Returns:
            API response as dictionary

        Raises:
            ProposalsAPIError: If the request fails
        """
        query = self._build_query(offset)
        url = f"{self.endpoint}?query={requests.utils.quote(query)}"

        try:
            response = requests.get(
                url,
                headers=self.auth.get_headers(),
                timeout=settings.api_timeout
            )
            response.raise_for_status()
            return response.json()

        except requests.RequestException as e:
            raise ProposalsAPIError(f"Failed to fetch proposals at offset {offset}: {e}")

    def fetch_all(self) -> pd.DataFrame:
        """
        Fetch all proposals with automatic pagination.

        Loops through all pages until no more results are returned.

        Returns:
            DataFrame containing all proposals

        Raises:
            ProposalsAPIError: If any request fails
        """
        all_proposals: List[Dict] = []
        offset = 0

        print(f"Starting to fetch proposals from {self.endpoint}")

        while True:
            print(f"  Fetching offset {offset}...")
            response = self._fetch_page(offset)

            if not response.get("success", False):
                error = response.get("errors", response.get("message", "Unknown error"))
                raise ProposalsAPIError(f"API returned error: {error}")

            proposals = response.get("data", {}).get("Proposal", [])

            if not proposals:
                print(f"  No more proposals at offset {offset}. Done.")
                break

            all_proposals.extend(proposals)
            print(f"  Retrieved {len(proposals)} proposals (total: {len(all_proposals)})")

            # Check if we got fewer than the limit (last page)
            meta = response.get("meta", {})
            total_elements = meta.get("totalElementsWithFilters", meta.get("totalElements", 0))

            if len(all_proposals) >= total_elements:
                print(f"  Reached total of {total_elements} proposals. Done.")
                break

            offset += self.page_limit

        print(f"Total proposals fetched: {len(all_proposals)}")

        if not all_proposals:
            return pd.DataFrame()

        return pd.DataFrame(all_proposals)

    def fetch_filtered(
        self,
        filters: Optional[Dict[str, Any]] = None,
        limit: Optional[int] = None
    ) -> pd.DataFrame:
        """
        Fetch proposals with optional filters.

        Args:
            filters: Filter dictionary (e.g., {"statut": {"eq": "gagné"}})
            limit: Maximum number of proposals to fetch

        Returns:
            DataFrame containing filtered proposals
        """
        # For now, fetch all and filter in pandas
        # Could be optimized to use API filters
        df = self.fetch_all()

        if limit and len(df) > limit:
            df = df.head(limit)

        return df


class ProposalsAPIError(Exception):
    """Raised when the Proposals API request fails."""
    pass
