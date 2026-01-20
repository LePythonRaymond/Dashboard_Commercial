"""
Notion TRAVAUX Projection Sync Module

Syncs TRAVAUX projection proposals to a dedicated Notion database.
Creates pages with proposal information for the "Projection Travaux prochains 12 mois" dashboard.
"""

from typing import List, Dict, Any, Optional, Set
from urllib.parse import urlparse, parse_qs
from notion_client import Client

from config.settings import settings, VIP_COMMERCIALS
from .notion_users import get_user_mapper, NotionUserMapper


class NotionTravauxSync:
    """
    Syncs TRAVAUX projection proposals to Notion database.

    Database properties expected:
    - Name (title): project name
    - ID Devis (rich_text): Furious proposal id (dedupe key)
    - Client (rich_text): company_name
    - Montant (number): amount
    - Commercial (people): Commercial assignees (from VIP_COMMERCIALS + alienor + luana)
    - Chef de projet (people): Project manager assignees (all others)
    - Date (date): proposal date
    - Début projet (date): projet_start
    - Probabilité (number): probability
    - Lien Furious (url): furious link
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        database_id: Optional[str] = None,
        user_mapper: Optional[NotionUserMapper] = None
    ):
        """
        Initialize the TRAVAUX sync.

        Args:
            api_key: Notion API key. Defaults to settings.
            database_id: Database ID for TRAVAUX projection. Defaults to settings.
            user_mapper: NotionUserMapper instance for person property. Auto-creates if None.
        """
        self.api_key = api_key or settings.notion_api_key
        self.database_id = self._format_database_id(
            database_id or settings.notion_travaux_projection_database_id
        )
        self.user_mapper = user_mapper or get_user_mapper()
        self._client: Optional[Client] = None

        # Define commercials set: VIP_COMMERCIALS + alienor + luana
        # Normalize all to lowercase for consistent matching
        commercials_list = [c.lower().strip() for c in VIP_COMMERCIALS] + ['alienor', 'luana']
        self.commercials_set: Set[str] = {c.lower().strip() for c in commercials_list}

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
        """Get or create Notion client."""
        if self._client is None:
            self._client = Client(auth=self.api_key)
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
            return properties
        except Exception as e:
            print(f"    Warning: Could not fetch database schema: {e}")
            return {}

    def _format_date(self, date_str: Optional[str]) -> Optional[str]:
        """Format date string for Notion API (YYYY-MM-DD)."""
        if not date_str or date_str == 'None':
            return None
        # Already in YYYY-MM-DD format from projection generator
        if isinstance(date_str, str) and len(date_str) >= 10:
            return date_str[:10]
        return None

    @staticmethod
    def _parse_assigned_to(assigned_to: str) -> List[str]:
        """
        Parse assigned_to string into a list of Furious identifiers.

        Handles whitespace-separated format (e.g., "anne-valerie manon.navarro")
        and defensively handles commas and semicolons as well.

        Args:
            assigned_to: The assigned_to string from Furious

        Returns:
            List of normalized identifier strings
        """
        if not assigned_to or assigned_to == 'N/A':
            return []

        # First, try splitting on whitespace (most common format)
        # If that doesn't produce multiple parts, try commas, then semicolons
        parts = assigned_to.split()
        if len(parts) == 1:
            # Try comma separation
            parts = assigned_to.split(',')
        if len(parts) == 1:
            # Try semicolon separation
            parts = assigned_to.split(';')

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

    @staticmethod
    def _normalize_identifier(identifier: str) -> str:
        """
        Normalize identifier for matching (lowercase, remove dots/hyphens, trim).

        This matches the normalization used in NotionUserMapper for consistent matching.
        """
        if not identifier:
            return ""
        normalized = identifier.lower().strip()
        # Remove dots and hyphens for matching (like user mapper does)
        normalized = normalized.replace('.', '').replace('-', '')
        # Remove extra spaces
        normalized = ' '.join(normalized.split())
        return normalized

    def _classify_assignees(self, identifiers: List[str]) -> tuple[List[str], List[str]]:
        """
        Classify assignees into Commercials and Chefs de projet.

        Uses flexible matching: normalizes both the identifier and commercials_set
        entries for comparison, similar to how NotionUserMapper works.

        Args:
            identifiers: List of Furious owner identifiers (already lowercase from parsing)

        Returns:
            Tuple of (commercial_identifiers, chef_de_projet_identifiers)
        """
        commercials = []
        chefs_de_projet = []

        # Create normalized commercials set for flexible matching
        normalized_commercials = {self._normalize_identifier(c) for c in self.commercials_set}

        for identifier in identifiers:
            normalized_id = self._normalize_identifier(identifier)
            # Try exact match first (for performance)
            if identifier in self.commercials_set:
                commercials.append(identifier)
            # Try normalized match (handles variations like "vincent.delavarende" vs "vincentdelavarende")
            elif normalized_id in normalized_commercials:
                commercials.append(identifier)
            else:
                chefs_de_projet.append(identifier)

        return commercials, chefs_de_projet

    def _build_page_properties(self, proposal: Dict[str, Any], schema: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Build Notion page properties from proposal data.
        Only includes properties that exist in the database schema.

        Args:
            proposal: Proposal dictionary with required fields
            schema: Database schema (optional, will fetch if not provided)

        Returns:
            Notion properties dictionary
        """
        if schema is None:
            schema = self._get_database_schema()

        proposal_id = proposal.get('id', '')
        date_value = self._format_date(proposal.get('date'))
        start_date_value = self._format_date(proposal.get('projet_start'))
        furious_url = proposal.get('furious_url', '')

        properties = {}

        # Name (title) - always required
        if "Name" in schema:
            properties["Name"] = {
                "title": [{"text": {"content": str(proposal.get('title', 'Unknown'))[:100]}}]
            }

        # ID Devis - only if property exists in schema
        if "ID Devis" in schema and proposal_id:
            properties["ID Devis"] = {
                "rich_text": [{"text": {"content": str(proposal_id)}}]
            }

        # Client - only if property exists
        if "Client" in schema:
            properties["Client"] = {
                "rich_text": [{"text": {"content": str(proposal.get('company_name', 'N/A'))[:100]}}]
            }

        # Montant - only if property exists
        if "Montant" in schema:
            properties["Montant"] = {
                "number": float(proposal.get('amount', 0))
            }

        # Commercial and Chef de projet (People properties)
        # Parse assigned_to and classify into Commercials vs Chefs de projet
        assigned_to_str = proposal.get('assigned_to', '')
        identifiers = self._parse_assigned_to(assigned_to_str)
        commercials, chefs_de_projet = self._classify_assignees(identifiers)

        # Build Commercial People property (always set, even if empty, to allow clearing)
        if "Commercial" in schema:
            commercial_prop = self._build_people_property(commercials)
            # Debug: log when we have commercial identifiers but no mapped users
            if commercials and not commercial_prop.get('people'):
                print(f"      Warning: Proposal {proposal.get('id', 'unknown')} has commercial identifiers {commercials} but no Notion users mapped")
            properties["Commercial"] = commercial_prop

        # Build Chef de projet People property (always set, even if empty, to allow clearing)
        if "Chef de projet" in schema:
            chef_prop = self._build_people_property(chefs_de_projet)
            properties["Chef de projet"] = chef_prop

        # Probabilite - only if property exists (note: without accent to match follow-ups DB)
        if "Probabilite" in schema:
            properties["Probabilite"] = {
                "number": float(proposal.get('probability', 0))
            }

        # Date - only if property exists and value is available
        if "Date" in schema and date_value:
            properties["Date"] = {"date": {"start": date_value}}

        # Début projet - only if property exists and value is available
        if "Début projet" in schema and start_date_value:
            properties["Début projet"] = {"date": {"start": start_date_value}}

        # Lien Furious - only if property exists and value is available
        if "Lien Furious" in schema and furious_url:
            properties["Lien Furious"] = {"url": furious_url}

        return properties

    @staticmethod
    def _extract_id_devis_from_page(page: Dict[str, Any]) -> str:
        """
        Extract Furious proposal id from an existing Notion page.

        Primary key: "ID Devis" rich_text.
        Fallback: parse "Lien Furious" url (cherche=...).
        """
        props = page.get("properties", {}) or {}

        id_prop = props.get("ID Devis")
        if isinstance(id_prop, dict):
            rich = id_prop.get("rich_text") or []
            if rich and isinstance(rich, list):
                txt = rich[0].get("text", {}).get("content") if isinstance(rich[0], dict) else None
                if txt:
                    return str(txt).strip()

        url_prop = props.get("Lien Furious")
        if isinstance(url_prop, dict):
            url_val = url_prop.get("url")
            if url_val:
                try:
                    parsed = urlparse(url_val)
                    qs = parse_qs(parsed.query)
                    cherche = qs.get("cherche")
                    if cherche and cherche[0]:
                        return str(cherche[0]).strip()
                except Exception:
                    pass

        return ""

    def _get_existing_pages_by_id(self) -> Dict[str, str]:
        """
        Build a mapping: proposal_id -> notion_page_id for the database.

        Returns:
            Dict mapping proposal IDs to Notion page IDs
        """
        mapping: Dict[str, str] = {}
        has_more = True
        start_cursor = None

        if not self.database_id:
            return mapping

        while has_more:
            response = self._query_pages(start_cursor=start_cursor)
            for page in response.get("results", []):
                proposal_id = self._extract_id_devis_from_page(page)
                if not proposal_id:
                    continue
                mapping.setdefault(proposal_id, page["id"])

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
        """Update an existing page properties (does not touch comments)."""
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

    def sync_proposals(self, proposals: List[Dict[str, Any]]) -> Dict[str, int]:
        """
        Sync TRAVAUX projection proposals to Notion database.

        Strategy: Upsert by ID Devis. If proposal already exists, update properties
        but keep the page (and its comments), keep the Name/title unchanged, and preserve
        Notion-only comment properties ("Commentaire Mathilde", "Next Steps Commercial").

        Args:
            proposals: List of proposal dictionaries

        Returns:
            Sync statistics
        """
        stats = {"created": 0, "updated": 0, "archived": 0, "errors": 0}

        if not self.database_id:
            print("  Skipping TRAVAUX projection sync: NOTION_TRAVAUX_PROJECTION_DATABASE_ID not configured")
            return stats

        print(f"\n  Syncing TRAVAUX projection to Notion...")
        print(f"    Database: {self.database_id[:8]}...")

        # Get database schema to validate properties
        schema = self._get_database_schema()
        if schema:
            print(f"    Database properties: {list(schema.keys())}")
        else:
            print(f"    Warning: Could not fetch database schema - will attempt all properties")

        # Debug: show commercials set for troubleshooting
        print(f"    Commercials set (for classification): {sorted(self.commercials_set)}")

        existing_by_id = self._get_existing_pages_by_id()
        print(f"    Found {len(existing_by_id)} existing page(s) with ID Devis/Lien Furious.")

        print(f"    Upserting {len(proposals)} proposal(s)...")
        for proposal in proposals:
            properties = self._build_page_properties(proposal, schema)
            proposal_id = str(proposal.get("id", "")).strip()
            existing_page_id = existing_by_id.get(proposal_id)
            if existing_page_id:
                # Keep Name/title and comment properties for comment continuity
                # These are Notion-only properties that contain meeting notes and next steps
                properties.pop("Name", None)
                properties.pop("Commentaire Mathilde", None)
                properties.pop("Next Steps Commercial", None)
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
            print("⚠ TRAVAUX projection database not configured")
            return False

        try:
            self.client.databases.retrieve(database_id=self.database_id)
            print(f"✓ TRAVAUX projection database accessible: {self.database_id[:8]}...")
            return True
        except Exception as e:
            print(f"✗ TRAVAUX projection database error: {e}")
            return False


def sync_travaux_projection_to_notion(proposals: List[Dict[str, Any]]) -> Dict[str, int]:
    """
    Convenience function to sync TRAVAUX projection to Notion.

    Args:
        proposals: List of proposal dictionaries

    Returns:
        Sync statistics
    """
    sync = NotionTravauxSync()
    return sync.sync_proposals(proposals)
