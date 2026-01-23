"""
Furious API Projects Client

Fetches projects from the Furious CRM with pagination and filtering support.
"""

import requests
import pandas as pd
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from datetime import datetime, timedelta

from config.settings import settings
from .auth import FuriousAuth, AuthenticationError


@dataclass
class ProjectFields:
    """Defines the fields to fetch from the Projects API."""

    FIELDS: List[str] = None

    def __post_init__(self):
        self.FIELDS = [
            "id",
            "title",
            "type",
            "type_label",
            "tags",
            "start_date",
            "end_date",
            "created_at",
            "project_manager",
            "business_account",
            "total_amount",
            "cf_bu"
        ]


class ProjectsClient:
    """
    Client for fetching projects from Furious CRM.

    Handles paginated requests to fetch projects with filtering.
    """

    def __init__(self, auth: Optional[FuriousAuth] = None):
        """
        Initialize the projects client.

        Args:
            auth: FuriousAuth instance (creates new one if not provided)
        """
        self.auth = auth or FuriousAuth()
        self.endpoint = f"{settings.furious_api_url}/project/"
        self.page_limit = settings.proposals_page_limit
        self.fields = ProjectFields().FIELDS

    def _build_query(
        self,
        offset: int = 0,
        created_at_min: Optional[str] = None,
        cf_bu: Optional[str] = None
    ) -> str:
        """
        Build the GraphQL-like query string.

        Args:
            offset: Pagination offset
            created_at_min: Minimum created_at date (YYYY-MM-DD format)
            cf_bu: Business unit filter value

        Returns:
            Formatted query string
        """
        fields_str = ",".join(self.fields)

        # Build filter object
        filter_parts = []
        if created_at_min:
            filter_parts.append(f'created_at:{{gte:"{created_at_min}"}}')
        if cf_bu:
            filter_parts.append(f'cf_bu:{{eq:"{cf_bu}"}}')

        filter_str = ",".join(filter_parts) if filter_parts else ""
        filter_clause = f"filter: {{{filter_str}}}" if filter_str else "filter: {}"

        query = f"""{{
  Project(
    limit: {self.page_limit},
    offset: {offset},
    order: [{{created_at:desc}}],
    {filter_clause}
  ){{
    {fields_str}
  }}
}}"""
        return query

    def _fetch_page(
        self,
        offset: int = 0,
        created_at_min: Optional[str] = None,
        cf_bu: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Fetch a single page of projects.

        Args:
            offset: Pagination offset
            created_at_min: Minimum created_at date (YYYY-MM-DD format)
            cf_bu: Business unit filter value

        Returns:
            API response as dictionary

        Raises:
            ProjectsAPIError: If the request fails
        """
        query = self._build_query(offset, created_at_min, cf_bu)
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
            raise ProjectsAPIError(f"Failed to fetch projects at offset {offset}: {e}")

    def fetch_recent_travaux(self, days: int = 7) -> pd.DataFrame:
        """
        Fetch TRAVAUX projects created in the last N days.

        Args:
            days: Number of days to look back (default: 7)

        Returns:
            DataFrame containing filtered projects

        Raises:
            ProjectsAPIError: If any request fails
        """
        # Calculate date window
        now = datetime.now()
        window_start = now - timedelta(days=days)
        created_at_min = window_start.strftime('%Y-%m-%d')
        today_str = now.strftime('%Y-%m-%d')

        print(f"Fetching TRAVAUX projects created since {created_at_min} (last {days} days)")

        all_projects: List[Dict] = []
        offset = 0

        while True:
            print(f"  Fetching offset {offset}...")
            response = self._fetch_page(
                offset=offset,
                created_at_min=created_at_min,
                cf_bu="TRAVAUX"
            )

            if not response.get("success", False):
                error = response.get("errors", response.get("message", "Unknown error"))
                raise ProjectsAPIError(f"API returned error: {error}")

            projects = response.get("data", {}).get("Project", [])

            if not projects:
                print(f"  No more projects at offset {offset}. Done.")
                break

            all_projects.extend(projects)
            print(f"  Retrieved {len(projects)} projects (total: {len(all_projects)})")

            # Check if we got fewer than the limit (last page)
            meta = response.get("meta", {})
            total_elements = meta.get("totalElementsWithFilters", meta.get("totalElements", 0))

            if len(all_projects) >= total_elements:
                print(f"  Reached total of {total_elements} projects. Done.")
                break

            offset += self.page_limit

        print(f"Total projects fetched: {len(all_projects)}")

        if not all_projects:
            return pd.DataFrame()

        df = pd.DataFrame(all_projects)

        # Defensive client-side filtering (belt-and-suspenders)
        # Parse created_at to datetime for validation
        if 'created_at' in df.columns and not df.empty:
            df['created_at_parsed'] = pd.to_datetime(df['created_at'], errors='coerce')
            window_start_ts = pd.Timestamp(window_start.date())
            today_ts = pd.Timestamp(now.date())

            # Filter by date window
            date_mask = (
                (df['created_at_parsed'] >= window_start_ts) &
                (df['created_at_parsed'] <= today_ts)
            )
            df = df[date_mask].copy()
            df = df.drop(columns=['created_at_parsed'])

        # Filter by cf_bu == TRAVAUX
        if 'cf_bu' in df.columns and not df.empty:
            df = df[df['cf_bu'] == 'TRAVAUX'].copy()

        print(f"After client-side filtering: {len(df)} projects")
        return df

    def fetch_all(self) -> pd.DataFrame:
        """
        Fetch all projects with automatic pagination.

        Loops through all pages until no more results are returned.

        Returns:
            DataFrame containing all projects

        Raises:
            ProjectsAPIError: If any request fails
        """
        all_projects: List[Dict] = []
        offset = 0

        print(f"Starting to fetch projects from {self.endpoint}")

        while True:
            print(f"  Fetching offset {offset}...")
            response = self._fetch_page(offset)

            if not response.get("success", False):
                error = response.get("errors", response.get("message", "Unknown error"))
                raise ProjectsAPIError(f"API returned error: {error}")

            projects = response.get("data", {}).get("Project", [])

            if not projects:
                print(f"  No more projects at offset {offset}. Done.")
                break

            all_projects.extend(projects)
            print(f"  Retrieved {len(projects)} projects (total: {len(all_projects)})")

            # Check if we got fewer than the limit (last page)
            meta = response.get("meta", {})
            total_elements = meta.get("totalElementsWithFilters", meta.get("totalElements", 0))

            if len(all_projects) >= total_elements:
                print(f"  Reached total of {total_elements} projects. Done.")
                break

            offset += self.page_limit

        print(f"Total projects fetched: {len(all_projects)}")

        if not all_projects:
            return pd.DataFrame()

        return pd.DataFrame(all_projects)


class ProjectsAPIError(Exception):
    """Raised when the Projects API request fails."""
    pass
