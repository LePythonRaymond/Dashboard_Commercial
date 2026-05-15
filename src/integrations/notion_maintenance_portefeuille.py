"""
Fetch the running Maintenance "Portefeuille sites" total from Notion.

Sums the configurable PROPERTY_NAME (default "Total HT") across all pages
in the same Maintenance Notion database/data source used by
``notion_entretien_start.py``. Used by the budget export to populate the
"Portefeuille sites au {today}" cell with the live running portfolio value.
"""

from typing import Any, Optional

NOTION_VERSION = "2025-09-03"

# Notion property summed for the running portefeuille value.
# Open assumption (per plan); easy 1-line change once the exact name is confirmed.
PROPERTY_NAME = "Total HT"


def _normalize_id(raw_id: str) -> str:
    if not raw_id:
        return ""
    s = str(raw_id).strip().strip('"').strip("'")
    return s.replace("-", "")


def _extract_number_from_property(prop: Any) -> Optional[float]:
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


def fetch_maintenance_portefeuille_running(
    api_key: str,
    database_or_datasource_id: str,
    property_name: str = PROPERTY_NAME,
) -> Optional[float]:
    """
    Sum the given property across all pages in the Maintenance Notion DB.

    Args:
        api_key: Notion API key.
        database_or_datasource_id: Notion database ID or data_source ID.
        property_name: Name of the numeric/formula property to sum (default "Total HT").

    Returns:
        Sum across all pages, or None if not configured / unreachable / empty.
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
    start_cursor: Optional[str] = None

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
        params: dict = {"page_size": 100}
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
            if property_name not in props:
                continue
            val = _extract_number_from_property(props[property_name])
            if val is not None:
                total += val

        next_cursor = resp.get("next_cursor")
        if not next_cursor:
            break
        start_cursor = next_cursor

    return total
