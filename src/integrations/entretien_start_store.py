"""
Fetch Maintenance Entretien start-of-year 2026 from Notion and write to a JSON file.

Used by the daily pipeline so the dashboard can read the value from file (updated daily).
Source: sum of "Total HT Cette année" on the Notion database/datasource.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

from src.integrations.notion_entretien_start import fetch_maintenance_entretien_start_2026

logger = logging.getLogger(__name__)

DEFAULT_FILENAME = "entretien_start_2026.json"
STALE_HOURS = 25


def get_store_path(base_dir: Path) -> Path:
    """Return the path to the JSON store file (under base_dir/data/)."""
    data_dir = base_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / DEFAULT_FILENAME


def fetch_and_write_entretien_start_2026(
    api_key: str,
    database_or_datasource_id: str,
    store_path: Path,
) -> Optional[float]:
    """
    Fetch the start-of-year value from Notion and write it to the store file.

    Args:
        api_key: Notion API key.
        database_or_datasource_id: Notion database or datasource ID.
        store_path: Path to the JSON file to write.

    Returns:
        The value written, or None if fetch failed or config missing.
    """
    if not api_key or not database_or_datasource_id:
        return None

    value = fetch_maintenance_entretien_start_2026(api_key, database_or_datasource_id)
    if value is None:
        return None

    store_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "value": float(value),
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }
    store_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return value


def read_entretien_start_2026_file_snapshot(store_path: Path) -> Optional[Tuple[float, str]]:
    """
    Read value and updated_at from the JSON store if the file exists.

    Does not apply STALE_HOURS — for dashboard display of pipeline freshness only.
    """
    if not store_path.exists():
        return None
    try:
        data = json.loads(store_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    value = data.get("value")
    updated_at_str = data.get("updated_at")
    if value is None or not updated_at_str:
        return None
    try:
        return (float(value), str(updated_at_str))
    except (TypeError, ValueError):
        return None


def read_entretien_start_2026_from_file(store_path: Path) -> Optional[float]:
    """
    Read the stored value from the JSON file if present and not stale.

    Args:
        store_path: Path to the JSON file.

    Returns:
        The value if file exists and was updated within STALE_HOURS, else None.
    """
    if not store_path.exists():
        return None

    try:
        data = json.loads(store_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    value = data.get("value")
    updated_at_str = data.get("updated_at")
    if value is None or not updated_at_str:
        return None

    try:
        updated_at = datetime.fromisoformat(updated_at_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None

    from datetime import timezone

    now = datetime.now(timezone.utc)
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    age_hours = (now - updated_at).total_seconds() / 3600.0
    if age_hours > STALE_HOURS:
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None
