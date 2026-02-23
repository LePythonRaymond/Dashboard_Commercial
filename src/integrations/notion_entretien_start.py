"""
Fetch Maintenance Entretien start-of-year value from a Notion data source.

Sums the "Total HT Cette année" property across all pages in the given
database/data source (e.g. 285d9278-02d7-808a-9395-000b04dfc654).
Used by the dashboard for the Réalisé carryover and info box.
"""

from typing import Any, Optional

# Notion API version aligned with rest of codebase
NOTION_VERSION = "2025-09-03"

PROPERTY_NAME = "Total HT Cette année"


def _normalize_id(raw_id: str) -> str:
    """Strip and optionally remove dashes for Notion API."""
    if not raw_id:
        return ""
    s = str(raw_id).strip().strip('"').strip("'")
    return s.replace("-", "")


def _extract_number_from_property(prop: Any) -> Optional[float]:
    """Extract numeric value from a Notion page property (number or formula)."""
    if prop is None or not isinstance(prop, dict):
        return None
    if "number" in prop and prop["number"] is not None:
        try:
            return float(prop["number"])
        except (TypeError, ValueError):
            return None
    if "formula" in prop:
        formula = prop["formula"]
        if isinstance(formula, dict) and "number" in formula and formula["number"] is not None:
            try:
                return float(formula["number"])
            except (TypeError, ValueError):
                return None
    return None


def fetch_maintenance_entretien_start_2026(
    api_key: str,
    database_or_datasource_id: str,
) -> Optional[float]:
    """
    Query the Notion database/data source and sum "Total HT Cette année" over all pages.

    Args:
        api_key: Notion API key.
        database_or_datasource_id: Notion database ID or data_source ID (e.g. from env
            NOTION_MAINTENANCE_ENTRETIEN_OBJECTIF_DATASOURCE_ID).

    Returns:
        Sum of the property across all pages, or None if not configured, empty, or on error.
    """
    if not api_key or not database_or_datasource_id:
        return None

    try:
        from notion_client import Client
    except ImportError:
        return None

    client = Client(auth=api_key, notion_version=NOTION_VERSION)
    normalized_id = _normalize_id(database_or_datasource_id)
    total = 0.0
    start_cursor = None

    # Prefer data_sources.query (API 2025-09-03); fallback to databases.query
    query_method = None
    query_kw = None
    data_sources_ep = getattr(client, "data_sources", None)
    if data_sources_ep is not None and hasattr(data_sources_ep, "query"):
        query_method = data_sources_ep.query
        query_kw = "data_source_id"
    if query_method is None:
        db_ep = getattr(client, "databases", None)
        if db_ep is not None and hasattr(db_ep, "query"):
            query_method = db_ep.query
            query_kw = "database_id"

    if query_method is None:
        return None

    while True:
        params = {"page_size": 100}
        if query_kw == "data_source_id":
            params["data_source_id"] = database_or_datasource_id.strip()
        else:
            params["database_id"] = normalized_id or database_or_datasource_id
        if start_cursor:
            params["start_cursor"] = start_cursor

        try:
            resp = query_method(**params)
        except Exception:
            return None

        results = resp.get("results") or []
        for page in results:
            props = page.get("properties") or {}
            if PROPERTY_NAME not in props:
                continue
            val = _extract_number_from_property(props[PROPERTY_NAME])
            if val is not None:
                total += val

        next_cursor = resp.get("next_cursor")
        if not next_cursor:
            break
        start_cursor = next_cursor

    return total
