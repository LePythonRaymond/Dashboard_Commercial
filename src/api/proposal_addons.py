"""
Furious API Proposal Addons (Avenants) Client

Fetches proposal addons from the Furious CRM with pagination support.
Each addon is linked to a parent proposal via the `id` field (proposal ID).

Merge strategy:
- Addon on a current-year proposal → amount added to parent proposal's amount
- Addon dated current year on an older proposal → injected as a standalone row
  (the parent was already counted in a previous year; the addon is new revenue)
"""

import requests
import pandas as pd
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime

from config.settings import settings
from .auth import FuriousAuth, AuthenticationError


# Fields to fetch — rich enough to create standalone rows for cross-year addons
ADDON_FIELDS = [
    "id_system",
    "id",               # parent proposal ID
    "amount",
    "status",
    "title",
    "date",
    "probability",
    "signature_date",
    "discount",
    "project_id",
    "created_at",
    "updated_at",
    "cf_typologie_de_devis",
    "cf_typologie_myrium",
    "cf_bu",
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
            DataFrame with addon fields
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
        if 'amount' in df.columns:
            df['amount'] = pd.to_numeric(df['amount'], errors='coerce').fillna(0)
        return df


def merge_addons_into_proposals(
    df_raw: pd.DataFrame,
    df_addons: pd.DataFrame,
    target_year: int,
) -> Tuple[pd.DataFrame, List[str]]:
    """
    Merge addons into the proposals DataFrame using two strategies:

    1. **Same-year**: addon's parent proposal is dated in target_year
       → add addon amount to the parent proposal's `amount` field.
    2. **Cross-year**: addon is dated in target_year but parent is from an older year
       → inject the addon as a standalone row (new revenue for this year).

    Args:
        df_raw: Raw proposals DataFrame (must have 'id' and 'date' columns)
        df_addons: Addons DataFrame from ProposalAddonsClient.fetch_all()
        target_year: The pipeline's target year (e.g. 2026)

    Returns:
        Tuple of (updated df_raw, list of log messages describing what was merged)
    """
    log_lines: List[str] = []

    if df_addons.empty or df_raw.empty:
        return df_raw, log_lines

    df_raw = df_raw.copy()
    df_raw['amount'] = pd.to_numeric(df_raw['amount'], errors='coerce').fillna(0)

    # Parse proposal dates to determine which year each proposal belongs to
    proposal_dates = pd.to_datetime(df_raw['date'], errors='coerce')
    proposal_year = proposal_dates.dt.year
    current_year_ids = set(df_raw.loc[proposal_year == target_year, 'id'].astype(str))
    all_proposal_ids = set(df_raw['id'].astype(str))

    # Parse addon dates
    addon_dates = pd.to_datetime(df_addons['date'], errors='coerce')
    addon_year = addon_dates.dt.year

    # --- Same-year addons: parent proposal is in target_year → merge into amount ---
    mask_same_year = df_addons['id'].astype(str).isin(current_year_ids)
    df_same = df_addons[mask_same_year]

    merged_count = 0
    if not df_same.empty:
        same_year_totals = df_same.groupby(df_same['id'].astype(str))['amount'].sum()
        addon_map = same_year_totals.to_dict()
        df_raw['addon_amount'] = df_raw['id'].astype(str).map(addon_map).fillna(0)
        df_raw['amount'] = df_raw['amount'] + df_raw['addon_amount']
        merged_count = int((df_raw['addon_amount'] > 0).sum())
        merged_total = float(df_raw['addon_amount'].sum())

        for proposal_id, addon_total in same_year_totals.items():
            titles = df_same.loc[df_same['id'].astype(str) == proposal_id, 'title'].tolist()
            parent_title = df_raw.loc[df_raw['id'].astype(str) == proposal_id, 'title'].values
            parent_name = parent_title[0] if len(parent_title) > 0 else '?'
            for t in titles:
                log_lines.append(f"    MERGED: \"{t}\" (+{addon_total:,.0f}€) → D{proposal_id} ({parent_name})")
    else:
        df_raw['addon_amount'] = 0
        merged_total = 0

    # --- Cross-year addons: addon dated target_year, parent is older → standalone rows ---
    mask_cross = (
        (addon_year == target_year)
        & ~df_addons['id'].astype(str).isin(current_year_ids)
    )
    df_cross = df_addons[mask_cross]

    injected_rows = []
    if not df_cross.empty:
        # Look up parent proposal data for inherited fields
        parent_lookup = df_raw.set_index(df_raw['id'].astype(str))

        for _, addon in df_cross.iterrows():
            parent_id = str(addon['id'])
            parent = parent_lookup.loc[parent_id] if parent_id in parent_lookup.index else None

            # Build a proposal-like row from the addon
            row = {
                'id': f"{parent_id}_AV{addon.get('id_system', '')}",
                'date': addon.get('date'),
                'title': f"[Avenant] {addon.get('title', '')}",
                'amount': float(addon.get('amount', 0)),
                'discount': addon.get('discount', 0),
                'vat': parent['vat'] if parent is not None and 'vat' in parent.index else 20.0,
                'currency': parent['currency'] if parent is not None and 'currency' in parent.index else 'EUR',
                'assigned_to': parent['assigned_to'] if parent is not None and 'assigned_to' in parent.index else '',
                'client_id': parent['client_id'] if parent is not None and 'client_id' in parent.index else '',
                'opportunity_id': parent.get('opportunity_id', '') if parent is not None else '',
                'statut': parent['statut'] if parent is not None and 'statut' in parent.index else 'Gagnés en cours',
                'pipe': parent.get('pipe', '') if parent is not None else '',
                'pipe_name': parent.get('pipe_name', '') if parent is not None else '',
                'created_at': addon.get('created_at', addon.get('date')),
                'last_updated_at': addon.get('updated_at'),
                'legal_entity': parent.get('legal_entity', '') if parent is not None else '',
                'company_name': parent['company_name'] if parent is not None and 'company_name' in parent.index else '',
                'id_furious': parent.get('id_furious', '') if parent is not None else '',
                'total_sold_days': 0,
                'total_cost': 0,
                'probability': addon.get('probability', parent['probability'] if parent is not None and 'probability' in parent.index else 100),
                'entity': parent.get('entity', '') if parent is not None else '',
                'projet_start': addon.get('date'),  # addon date as project start
                'projet_stop': addon.get('date'),    # single-day for standalone
                'sign_url': '',
                'cf_typologie_de_devis': addon.get('cf_typologie_de_devis') or (parent.get('cf_typologie_de_devis', '') if parent is not None else ''),
                'cf_typologie_myrium': addon.get('cf_typologie_myrium') or (parent.get('cf_typologie_myrium', '') if parent is not None else ''),
                'cf_bu': addon.get('cf_bu') or (parent.get('cf_bu', '') if parent is not None else ''),
                'signature_date': addon.get('signature_date') or addon.get('date'),
                'addon_amount': 0,  # not an addon merge, it IS the addon
            }
            injected_rows.append(row)
            log_lines.append(
                f"    INJECTED: \"{addon.get('title', '')}\" ({float(addon.get('amount', 0)):,.0f}€) "
                f"as standalone (parent D{parent_id} is from older year)"
            )

    if injected_rows:
        df_injected = pd.DataFrame(injected_rows)
        # Align columns — add any missing columns as NaN
        for col in df_raw.columns:
            if col not in df_injected.columns:
                df_injected[col] = None
        df_raw = pd.concat([df_raw, df_injected[df_raw.columns]], ignore_index=True)

    # Summary log
    injected_total = sum(r['amount'] for r in injected_rows)
    print(f"  Addons summary for {target_year}:")
    print(f"    Same-year merged: {merged_count} proposal(s), +{merged_total:,.0f}€")
    print(f"    Cross-year injected: {len(injected_rows)} standalone row(s), +{injected_total:,.0f}€")
    print(f"    Skipped (addon not dated {target_year} and parent not in {target_year}): "
          f"{len(df_addons) - len(df_same) - len(df_cross)}")

    return df_raw, log_lines


class ProposalAddonsAPIError(Exception):
    """Raised when the Proposal Addons API request fails."""
    pass
