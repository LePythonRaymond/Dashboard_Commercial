"""
Notion Recent TRAVAUX Projects Sync Module

Syncs recent TRAVAUX projects (created in last 7 days) to a dedicated Notion database.
Creates/updates pages with project information for the "Récent projets travaux" dashboard.
"""

import pandas as pd
from typing import List, Dict, Any, Optional, Set
from notion_client import Client

from config.settings import settings
from .notion_users import get_user_mapper, NotionUserMapper


class NotionRecentTravauxProjectsSync:
    """
    Syncs recent TRAVAUX projects to Notion database.

    Database properties expected:
    - Name (title): project title
    - ID Projet (rich_text or number): Furious project id (dedupe key)
    - Voir Furious (rich_text): link to Furious project view
    - Type (multi_select): project type
    - Label (multi_select): type label
    - Tags (multi_select): project tags
    - Date début (date): start_date
    - Date fin (date): end_date
    - Date Creation (date): created_at
    - Chef de projet (people): project_manager
    - Commercial (people): business_account
    - CA (number): total_amount
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        database_id: Optional[str] = None,
        user_mapper: Optional[NotionUserMapper] = None
    ):
        """
        Initialize the recent TRAVAUX projects sync.

        Args:
            api_key: Notion API key. Defaults to settings.
            database_id: Database ID for recent TRAVAUX projects. Defaults to settings.
            user_mapper: NotionUserMapper instance for person property. Auto-creates if None.
        """
        self.api_key = api_key or settings.notion_api_key
        self.database_id = self._format_database_id(
            database_id or settings.notion_travaux_recent_projects_database_id
        )
        self.user_mapper = user_mapper or get_user_mapper()
        self._client: Optional[Client] = None

    @staticmethod
    def _format_database_id(db_id: str) -> str:
        """Format Notion database ID (remove dashes)."""
        if not db_id:
            return ""
        db_id = str(db_id).strip().strip('"').strip("'")
        db_id = db_id.replace('-', '')
        db_id = ''.join(db_id.split())
        return db_id

    @property
    def client(self) -> Client:
        """Get or create Notion client with pinned API version."""
        if self._client is None:
            self._client = Client(auth=self.api_key, notion_version="2025-09-03")
        return self._client

    def _get_data_source_id_for_database(self) -> str:
        """
        Resolve a Notion `data_source_id` from this sync's `database_id`.

        Newer Notion API versions introduce `data_sources` under a database; querying is done
        via `client.data_sources.query(data_source_id=...)` in newer SDK versions.
        """
        if not self.database_id:
            raise RuntimeError("Database ID is empty; cannot resolve data source id.")
        db = self.client.databases.retrieve(database_id=self.database_id)
        data_sources = db.get("data_sources") or []
        if not data_sources or not isinstance(data_sources, list) or not isinstance(data_sources[0], dict):
            raise RuntimeError(
                "Notion database has no `data_sources` field; cannot query pages safely "
                f"(database_id={self.database_id[:8]}...)."
            )
        ds_id = str(data_sources[0].get("id") or "").strip()
        if not ds_id:
            raise RuntimeError(
                "Notion database `data_sources[0].id` is empty; cannot query pages safely "
                f"(database_id={self.database_id[:8]}...)."
            )
        return ds_id

    def _query_pages(self, start_cursor: Optional[str] = None) -> Dict[str, Any]:
        """
        Query pages for this database, compatible with both old and new Notion SDK styles.

        - Old style: `client.databases.query(database_id=...)`
        - New style (Data sources): `client.data_sources.query(data_source_id=...)`
        """
        if not self.database_id:
            raise RuntimeError("Database ID is empty; cannot query pages.")

        page_size = 100

        databases_ep = getattr(self.client, "databases", None)
        if databases_ep is not None and hasattr(databases_ep, "query"):
            params: Dict[str, Any] = {"database_id": self.database_id, "page_size": page_size}
            if start_cursor:
                params["start_cursor"] = start_cursor
            return databases_ep.query(**params)

        data_sources_ep = getattr(self.client, "data_sources", None)
        if data_sources_ep is not None and hasattr(data_sources_ep, "query"):
            data_source_id = self._get_data_source_id_for_database()
            params = {"data_source_id": data_source_id, "page_size": page_size}
            if start_cursor:
                params["start_cursor"] = start_cursor
            return data_sources_ep.query(**params)

        raise RuntimeError(
            "Notion SDK does not expose a supported query method. "
            "Refusing to sync to avoid duplicate page creation. "
            f"(has_databases_query={hasattr(databases_ep, 'query') if databases_ep is not None else False}, "
            f"has_data_sources_query={hasattr(data_sources_ep, 'query') if data_sources_ep is not None else False})"
        )

    def _get_database_schema(self) -> Dict[str, Any]:
        """
        Get the database schema to check which properties exist.

        Returns:
            Dictionary mapping property names to their types
        """
        if not self.database_id:
            return {}

        try:
            db_info = self.client.databases.retrieve(database_id=self.database_id)
            properties = db_info.get("properties", {})
            if isinstance(properties, dict) and properties:
                return properties

            # Newer Notion API can expose properties via "data_sources" attached to the database.
            # Try to retrieve the first data source and read its properties.
            data_sources = db_info.get("data_sources") or []
            if isinstance(data_sources, list) and data_sources and isinstance(data_sources[0], dict):
                ds_id = str(data_sources[0].get("id") or "").strip()
                if ds_id:
                    data_sources_ep = getattr(self.client, "data_sources", None)
                    if data_sources_ep is not None and hasattr(data_sources_ep, "retrieve"):
                        ds_info = data_sources_ep.retrieve(data_source_id=ds_id)
                        ds_props = ds_info.get("properties") or {}
                        if isinstance(ds_props, dict) and ds_props:
                            return ds_props

            return {}
        except Exception as e:
            print(f"    Warning: Could not fetch database schema: {e}")
            return {}

    def _format_date(self, date_str: Optional[str]) -> Optional[str]:
        """Format date string for Notion API (YYYY-MM-DD)."""
        if not date_str or date_str == 'None' or pd.isna(date_str):
            return None
        # Handle pandas Timestamp
        if hasattr(date_str, 'strftime'):
            return date_str.strftime('%Y-%m-%d')
        # Already in YYYY-MM-DD format
        if isinstance(date_str, str) and len(date_str) >= 10:
            return date_str[:10]
        return None

    @staticmethod
    def _parse_multi_select(value: Optional[str]) -> List[str]:
        """
        Parse a comma-separated string into a list of values for multi-select.

        Args:
            value: Comma-separated string (e.g., "tag1,tag2,tag3" or "Label 1, Label 2")

        Returns:
            List of trimmed, non-empty values
        """
        if not value or value == 'None':
            return []
        if isinstance(value, (list, tuple)):
            return [str(v).strip() for v in value if str(v).strip()]
        # Split by comma and clean
        parts = str(value).split(',')
        return [part.strip() for part in parts if part.strip()]

    def _build_multi_select_property(self, values: List[str]) -> Dict[str, Any]:
        """
        Build Notion multi-select property from a list of values.

        Args:
            values: List of string values

        Returns:
            Notion multi-select property value
        """
        # Truncate each value to 100 chars (Notion limit) and filter empty
        select_options = [
            {"name": val[:100]} for val in values if val and val.strip()
        ]
        return {"multi_select": select_options}

    @staticmethod
    def _parse_person_field(value: Optional[str]) -> List[str]:
        """
        Parse a person field (project_manager or business_account) into a list of identifiers.

        Handles whitespace-separated, comma-separated, or semicolon-separated formats.

        Args:
            value: The person field string from Furious

        Returns:
            List of normalized identifier strings
        """
        if not value or value == 'N/A':
            return []

        s = str(value).strip()
        if not s or s.lower() in ("nan", "none"):
            return []

        # Prefer whitespace split, then fallback to comma/semicolon if single token
        parts = s.split()
        if len(parts) == 1:
            parts = s.split(',')
        if len(parts) == 1:
            parts = s.split(';')

        # Normalize: convert to lowercase, strip whitespace, and filter out empty strings
        normalized = [part.lower().strip() for part in parts if part.strip()]
        return normalized

    def _build_people_property(self, identifiers: List[str]) -> Dict[str, Any]:
        """
        Build Notion People property from a list of Furious identifiers.

        Maps each identifier to a Notion user ID and builds the People property.
        Skips identifiers that can't be mapped.

        Args:
            identifiers: List of Furious owner identifiers

        Returns:
            Notion people property value with unique user IDs
        """
        user_ids = []
        seen_ids = set()

        for identifier in identifiers:
            user_id = self.user_mapper.get_notion_user_id(identifier)
            if user_id and user_id not in seen_ids:
                user_ids.append({"object": "user", "id": user_id})
                seen_ids.add(user_id)

        return {"people": user_ids}

    def _build_furious_url(self, project_id: str) -> str:
        """
        Build Furious URL from project ID.

        Args:
            project_id: The project ID from Furious

        Returns:
            Full URL to the project in Furious
        """
        if not project_id:
            return ''
        return f"https://merciraymond.furious-squad.com/projet_view.php?id={project_id}&view=1"

    def _build_page_properties(self, project: Dict[str, Any], schema: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Build Notion page properties from project data.
        Only includes properties that exist in the database schema.

        Args:
            project: Project dictionary with required fields
            schema: Database schema (optional, will fetch if not provided)

        Returns:
            Notion properties dictionary
        """
        if schema is None:
            schema = self._get_database_schema()

        project_id = str(project.get('id', '')).strip()
        title = str(project.get('title', 'Unknown'))[:100]
        furious_url = self._build_furious_url(project_id)

        properties = {}

        # Name (title) - always required
        if "Name" in schema:
            properties["Name"] = {
                "title": [{"text": {"content": title}}]
            }

        # ID Projet - only if property exists in schema (dedupe key)
        if "ID Projet" in schema and project_id:
            # Try number first, fallback to rich_text
            prop_type = schema.get("ID Projet", {}).get("type", "rich_text")
            if prop_type == "number":
                try:
                    properties["ID Projet"] = {"number": int(project_id)}
                except (ValueError, TypeError):
                    properties["ID Projet"] = {"rich_text": [{"text": {"content": project_id}}]}
            else:
                properties["ID Projet"] = {
                    "rich_text": [{"text": {"content": project_id}}]
                }

        # Voir Furious (rich_text with link) - only if property exists
        if "Voir Furious" in schema and furious_url:
            properties["Voir Furious"] = {
                "rich_text": [{
                    "type": "text",
                    "text": {
                        "content": "Voir Furious",
                        "link": {"url": furious_url}
                    }
                }]
            }

        # Type (multi_select) - only if property exists
        if "Type" in schema:
            type_values = self._parse_multi_select(project.get('type'))
            properties["Type"] = self._build_multi_select_property(type_values)

        # Label (multi_select) - only if property exists
        if "Label" in schema:
            label_values = self._parse_multi_select(project.get('type_label'))
            properties["Label"] = self._build_multi_select_property(label_values)

        # Tags (multi_select) - only if property exists
        if "Tags" in schema:
            tags_values = self._parse_multi_select(project.get('tags'))
            properties["Tags"] = self._build_multi_select_property(tags_values)

        # Date début (date) - only if property exists and value is available
        if "Date début" in schema:
            start_date_value = self._format_date(project.get('start_date'))
            if start_date_value:
                properties["Date début"] = {"date": {"start": start_date_value}}

        # Date fin (date) - only if property exists and value is available
        if "Date fin" in schema:
            end_date_value = self._format_date(project.get('end_date'))
            if end_date_value:
                properties["Date fin"] = {"date": {"start": end_date_value}}

        # Date Creation (date) - only if property exists and value is available
        if "Date Creation" in schema:
            created_at_value = self._format_date(project.get('created_at'))
            if created_at_value:
                properties["Date Creation"] = {"date": {"start": created_at_value}}

        # Chef de projet (people) - only if property exists
        if "Chef de projet" in schema:
            project_manager_str = project.get('project_manager', '')
            manager_identifiers = self._parse_person_field(project_manager_str)
            manager_prop = self._build_people_property(manager_identifiers)
            properties["Chef de projet"] = manager_prop

        # Commercial (people) - only if property exists
        if "Commercial" in schema:
            business_account_str = project.get('business_account', '')
            business_identifiers = self._parse_person_field(business_account_str)
            business_prop = self._build_people_property(business_identifiers)
            properties["Commercial"] = business_prop

        # CA (number) - only if property exists
        if "CA" in schema:
            total_amount = project.get('total_amount', 0)
            try:
                properties["CA"] = {"number": float(total_amount)}
            except (ValueError, TypeError):
                properties["CA"] = {"number": 0.0}

        return properties

    @staticmethod
    def _extract_id_projet_from_page(page: Dict[str, Any]) -> str:
        """
        Extract Furious project id from an existing Notion page.

        Primary key: "ID Projet" (rich_text or number).
        """
        props = page.get("properties", {}) or {}

        id_prop = props.get("ID Projet")
        if isinstance(id_prop, dict):
            # Try number first
            if id_prop.get("type") == "number":
                num_val = id_prop.get("number")
                if num_val is not None:
                    return str(int(num_val)).strip()
            # Try rich_text
            rich = id_prop.get("rich_text") or []
            if rich and isinstance(rich, list):
                txt = rich[0].get("text", {}).get("content") if isinstance(rich[0], dict) else None
                if txt:
                    return str(txt).strip()

        return ""

    def _get_existing_pages_by_id(self) -> Dict[str, str]:
        """
        Build a mapping: project_id -> notion_page_id for the database.

        Returns:
            Dict mapping project IDs to Notion page IDs
        """
        mapping: Dict[str, str] = {}
        has_more = True
        start_cursor = None

        if not self.database_id:
            return mapping

        while has_more:
            response = self._query_pages(start_cursor=start_cursor)
            for page in response.get("results", []):
                project_id = self._extract_id_projet_from_page(page)
                if not project_id:
                    continue
                mapping.setdefault(project_id, page["id"])

            has_more = response.get("has_more", False)
            start_cursor = response.get("next_cursor")

        return mapping

    def _create_page(self, properties: Dict[str, Any]) -> Optional[str]:
        """
        Create a new page in the database.

        Args:
            properties: Page properties

        Returns:
            Created page ID or None if failed
        """
        if not self.database_id:
            print("    Error: Database ID is empty")
            return None

        try:
            response = self.client.pages.create(
                parent={"database_id": self.database_id},
                properties=properties
            )
            return response.get("id")
        except Exception as e:
            # Log detailed error for debugging
            error_msg = str(e)
            if hasattr(e, 'code'):
                error_msg += f" (code: {e.code})"
            if hasattr(e, 'body'):
                error_body = e.body if isinstance(e.body, str) else str(e.body)
                error_msg += f" (body: {error_body[:200]})"
            print(f"    Warning: Could not create page: {error_msg}")
            print(f"    Properties attempted: {list(properties.keys())}")
            return None

    def _update_page(self, page_id: str, properties: Dict[str, Any]) -> bool:
        """Update an existing page properties (does not touch comments or manual fields)."""
        try:
            self.client.pages.update(page_id=page_id, properties=properties)
            return True
        except Exception as e:
            # Log detailed error for debugging
            error_msg = str(e)
            if hasattr(e, 'code'):
                error_msg += f" (code: {e.code})"
            if hasattr(e, 'body'):
                error_body = e.body if isinstance(e.body, str) else str(e.body)
                error_msg += f" (body: {error_body[:200]})"
            print(f"    Warning: Could not update page {page_id}: {error_msg}")
            return False

    def sync_projects(self, projects: List[Dict[str, Any]]) -> Dict[str, int]:
        """
        Sync recent TRAVAUX projects to Notion database.

        Strategy: Upsert by ID Projet. If project already exists, update properties
        but keep the page (and its comments), keep the Name/title unchanged, and preserve
        Notion-only manual properties.

        Args:
            projects: List of project dictionaries

        Returns:
            Sync statistics
        """
        stats = {"created": 0, "updated": 0, "archived": 0, "errors": 0}

        if not self.database_id:
            print("  Skipping recent TRAVAUX projects sync: NOTION_TRAVAUX_RECENT_PROJECTS_DATABASE_ID not configured")
            return stats

        print(f"\n  Syncing recent TRAVAUX projects to Notion...")
        print(f"    Database: {self.database_id[:8]}...")

        # Get database schema to validate properties
        schema = self._get_database_schema()
        if schema:
            print(f"    Database properties: {list(schema.keys())}")
        else:
            print(f"    Warning: Could not fetch database schema - will attempt all properties")

        existing_by_id = self._get_existing_pages_by_id()
        print(f"    Found {len(existing_by_id)} existing page(s) with ID Projet.")

        print(f"    Upserting {len(projects)} project(s)...")
        for project in projects:
            properties = self._build_page_properties(project, schema)
            project_id = str(project.get("id", "")).strip()
            existing_page_id = existing_by_id.get(project_id)
            if existing_page_id:
                # Keep Name/title unchanged to preserve manual renames
                properties.pop("Name", None)
                if self._update_page(existing_page_id, properties):
                    stats["updated"] += 1
                else:
                    stats["errors"] += 1
            else:
                page_id = self._create_page(properties)
                if page_id:
                    stats["created"] += 1
                else:
                    stats["errors"] += 1

        print(
            f"    Done: {stats['created']} created, {stats['updated']} updated, "
            f"{stats['archived']} archived, {stats['errors']} errors"
        )
        return stats

    def test_connection(self) -> bool:
        """
        Test connection to Notion database.

        Returns:
            True if database is accessible
        """
        if not self.database_id:
            print("⚠ Recent TRAVAUX projects database not configured")
            return False

        try:
            self.client.databases.retrieve(database_id=self.database_id)
            print(f"✓ Recent TRAVAUX projects database accessible: {self.database_id[:8]}...")
            return True
        except Exception as e:
            print(f"✗ Recent TRAVAUX projects database error: {e}")
            return False


def sync_recent_travaux_projects_to_notion(projects: List[Dict[str, Any]]) -> Dict[str, int]:
    """
    Convenience function to sync recent TRAVAUX projects to Notion.

    Args:
        projects: List of project dictionaries

    Returns:
        Sync statistics
    """
    sync = NotionRecentTravauxProjectsSync()
    return sync.sync_projects(projects)
