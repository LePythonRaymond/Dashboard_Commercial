"""
Notion Alerts Sync Module

Syncs commercial alerts (Weird Proposals and Follow-ups) to Notion databases.
Creates pages in dedicated databases with person property mapping.
"""

from typing import Callable, Dict, List, Any, Optional, Set, Tuple
from datetime import date, datetime
from urllib.parse import urlparse, parse_qs
from notion_client import Client

from config.settings import settings, VIP_COMMERCIALS
from src.processing.alerts import AlertsOutput
from .notion_users import get_user_mapper, NotionUserMapper
from .notion_values import page_value, value_from_page, value_from_payload
from .notion_scope import ARCHIVED_PROP, scope_changes, scope_on_create


# Furious URL template
FURIOUS_URL_TEMPLATE = "https://merciraymond.furious-squad.com/compta.php?view=5&cherche={id}"


class NotionAlertsSync:
    """
    Syncs commercial alerts to Notion databases.

    Manages two databases:
    - Weird Proposals: Data quality issues
    - Commercial Follow-ups: Proposals needing attention
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        weird_database_id: Optional[str] = None,
        followup_database_id: Optional[str] = None,
        user_mapper: Optional[NotionUserMapper] = None
    ):
        """
        Initialize the alerts sync.

        Args:
            api_key: Notion API key. Defaults to settings.
            weird_database_id: Database ID for weird proposals. Defaults to settings.
            followup_database_id: Database ID for follow-ups. Defaults to settings.
            user_mapper: NotionUserMapper instance for person property. Auto-creates if None.
        """
        self.api_key = api_key or settings.notion_api_key
        self.weird_database_id = self._format_database_id(
            weird_database_id or settings.notion_weird_database_id
        )
        self.followup_database_id = self._format_database_id(
            followup_database_id or settings.notion_followup_database_id
        )
        self.user_mapper = user_mapper or get_user_mapper()
        self._client: Optional[Client] = None
        # Furious statuses that match no option of the follow-up "Statut" property
        self._unmapped_statuses: Set[str] = set()

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
        """Get or create Notion client (Notion API 2025-09-03)."""
        if self._client is None:
            self._client = Client(auth=self.api_key, notion_version="2025-09-03")
        return self._client

    def _get_data_source_id_for_database(self, database_id: str) -> str:
        """
        Resolve a Notion `data_source_id` from a `database_id`.

        Notion introduced "Data sources" (API 2025-09-03). In newer SDK versions,
        database querying can be done via `client.data_sources.query(data_source_id=...)`.
        """
        db = self.client.databases.retrieve(database_id=database_id)
        data_sources = db.get("data_sources") or []
        if not data_sources or not isinstance(data_sources, list) or not isinstance(data_sources[0], dict):
            raise RuntimeError(
                "Notion database has no `data_sources` field; cannot query pages safely "
                f"(database_id={database_id[:8]}...)."
            )
        ds_id = str(data_sources[0].get("id") or "").strip()
        if not ds_id:
            raise RuntimeError(
                "Notion database `data_sources[0].id` is empty; cannot query pages safely "
                f"(database_id={database_id[:8]}...)."
            )
        return ds_id

    def _query_pages(self, database_id: str, start_cursor: Optional[str] = None) -> Dict[str, Any]:
        """
        Query pages for a database, compatible with both old and new Notion SDK styles.

        - Old style: `client.databases.query(database_id=...)`
        - New style (Data sources): `client.data_sources.query(data_source_id=...)`
        """
        page_size = 100

        # 1) Prefer databases.query if available (older SDKs)
        databases_ep = getattr(self.client, "databases", None)
        if databases_ep is not None and hasattr(databases_ep, "query"):
            params: Dict[str, Any] = {"database_id": database_id, "page_size": page_size}
            if start_cursor:
                params["start_cursor"] = start_cursor
            return databases_ep.query(**params)

        # 2) Fallback to data_sources.query (newer SDKs / Notion API 2025-09-03)
        data_sources_ep = getattr(self.client, "data_sources", None)
        if data_sources_ep is not None and hasattr(data_sources_ep, "query"):
            data_source_id = self._get_data_source_id_for_database(database_id)
            params = {"data_source_id": data_source_id, "page_size": page_size}
            if start_cursor:
                params["start_cursor"] = start_cursor
            return data_sources_ep.query(**params)

        # 3) Fail closed: never "assume empty DB" if we can't query
        raise RuntimeError(
            "Notion SDK does not expose a supported query method. "
            "Refusing to sync to avoid duplicate page creation. "
            f"(has_databases_query={hasattr(databases_ep, 'query') if databases_ep is not None else False}, "
            f"has_data_sources_query={hasattr(data_sources_ep, 'query') if data_sources_ep is not None else False})"
        )

    def _build_furious_url(self, proposal_id: str) -> str:
        """Build Furious URL from proposal ID."""
        if not proposal_id:
            return ""
        return FURIOUS_URL_TEMPLATE.format(id=proposal_id)

    def _format_date(self, date_str: Optional[str]) -> Optional[str]:
        """Format date string for Notion API (YYYY-MM-DD)."""
        if not date_str or date_str == 'None':
            return None
        # Already in YYYY-MM-DD format from alerts
        if isinstance(date_str, str) and len(date_str) >= 10:
            return date_str[:10]
        return None

    def _build_person_property(self, owner: str) -> Dict[str, Any]:
        """
        Build Notion person property from Furious owner.

        Args:
            owner: Furious owner identifier

        Returns:
            Notion people property value
        """
        user_id = self.user_mapper.get_notion_user_id(owner)
        if user_id:
            return {
                "people": [{"object": "user", "id": user_id}]
            }
        return {"people": []}

    @staticmethod
    def _parse_assigned_to(assigned_to: Any) -> List[str]:
        """
        Parse Furious `assigned_to` into a list of identifier tokens.

        In Furious, this field is often whitespace-separated (e.g. "anne-valerie manon.navarro"),
        but we also defensively handle commas/semicolons.
        """
        if assigned_to is None:
            return []

        s = str(assigned_to).strip()
        if not s or s == "N/A" or s.lower() in ("nan", "none"):
            return []

        # Prefer whitespace split, then fallback to comma/semicolon if single token
        parts = s.split()
        if len(parts) == 1:
            parts = s.split(",")
        if len(parts) == 1:
            parts = s.split(";")

        return [p.lower().strip() for p in parts if p and p.strip()]

    @staticmethod
    def _normalize_identifier(identifier: str) -> str:
        """Normalize identifier for matching (lowercase, remove dots/hyphens, trim)."""
        if not identifier:
            return ""
        normalized = identifier.lower().strip()
        normalized = normalized.replace(".", "").replace("-", "")
        normalized = " ".join(normalized.split())
        return normalized

    def _classify_assignees(self, identifiers: List[str]) -> Tuple[List[str], List[str]]:
        """
        Split assignees into (Commercial, Chef de projet) buckets.

        Commercials are: VIP_COMMERCIALS + {'alienor', 'luana'}.
        Everything else becomes Chef de projet.
        """
        commercials_set: Set[str] = set(VIP_COMMERCIALS) | {"alienor", "luana"}
        normalized_commercials = {self._normalize_identifier(c) for c in commercials_set}

        commercials: List[str] = []
        chefs_de_projet: List[str] = []

        for identifier in identifiers:
            normalized_id = self._normalize_identifier(identifier)
            if identifier in commercials_set or normalized_id in normalized_commercials:
                commercials.append(identifier)
            else:
                chefs_de_projet.append(identifier)

        return commercials, chefs_de_projet

    def _build_people_property(self, identifiers: List[str]) -> Dict[str, Any]:
        """
        Build Notion People property from Furious identifiers.
        Skips identifiers that can't be mapped to Notion users.
        """
        people: List[Dict[str, str]] = []
        seen: Set[str] = set()

        for identifier in identifiers:
            user_id = self.user_mapper.get_notion_user_id(identifier)
            if user_id and user_id not in seen:
                people.append({"object": "user", "id": user_id})
                seen.add(user_id)

        return {"people": people}

    def _get_database_schema(self, database_id: str) -> Dict[str, Any]:
        """
        Get database schema (properties dict) so we can set only existing properties.

        If this fails, returns {} and we will "try all properties" (backward compatible behavior).
        """
        if not database_id:
            return {}
        try:
            db_info = self.client.databases.retrieve(database_id=database_id)

            # Old/standard shape: properties live directly on the database object
            props = db_info.get("properties") or {}
            if isinstance(props, dict) and props:
                return props

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

            # If we get here, schema couldn't be determined reliably
            return {}
        except Exception as e:
            print(f"    Warning: Could not fetch database schema for {database_id[:8]}...: {e}")
            return {}

    @staticmethod
    def _schema_allows(schema: Dict[str, Any], prop_name: str) -> bool:
        """
        Decide whether a Notion property should be sent.

        - If schema is known (non-empty): only allow properties that exist in schema.
        - If schema is unknown/empty: allow a conservative safe list of "core" properties and
          skip optional ones like Responsable/Commercial/Chef de projet to avoid 400 errors.
        """
        if schema:
            return prop_name in schema

        safe_when_unknown = {
            "Name",
            "ID Devis",
            "Client",
            "Montant",
            "Statut",
            "Probabilite",
            "Probleme",
            "Date",
            "Début projet",
            "Fin projet",
            "Lien Furious",
        }
        return prop_name in safe_when_unknown

    def _build_probleme_multi_select(self, reason: str) -> Dict[str, Any]:
        """
        Build Notion multi-select property from problem reason string.

        The reason string is pipe-separated (e.g., "Date début manquante | Probabilité 0%").

        Args:
            reason: Pipe-separated string of problem reasons

        Returns:
            Notion multi-select property value
        """
        if not reason or reason == '':
            return {"multi_select": []}

        # Split by pipe separator and clean up each reason
        reasons = [r.strip() for r in reason.split('|') if r.strip()]

        # Build multi-select array
        select_options = [{"name": r[:100]} for r in reasons if r]

        return {"multi_select": select_options}

    @staticmethod
    def _build_typologie_devis_multi_select(value: str) -> Dict[str, Any]:
        """
        Build Notion multi-select property from Furious cf_typologie_de_devis (comma-separated).

        Returns:
            Notion multi_select property value
        """
        if not value or not str(value).strip():
            return {"multi_select": []}
        parts = [p.strip()[:100] for p in str(value).split(",") if p.strip()]
        return {"multi_select": [{"name": p} for p in parts]}

    @staticmethod
    def _status_options(schema: Dict[str, Any], prop_name: str = "Statut") -> Dict[str, str]:
        """Existing options of a Notion status property, keyed by lowercase name."""
        prop = (schema or {}).get(prop_name) or {}
        options = (prop.get("status") or {}).get("options") or []
        return {str(o["name"]).strip().lower(): o["name"] for o in options if o.get("name")}

    def _status_payload(self, status: Any, schema: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Payload for the "Statut" status property, using the exact name of an existing option.

        Notion refuses a status option that does not exist (the API cannot create
        one), and that refusal would reject the whole page update. The Furious
        label is therefore matched without case ("Perdu", "gagnés en cours", ...);
        None means no option matches and "Statut" must not be sent. With an
        unknown schema the Furious label is sent as is (previous behaviour).
        """
        name = str(status or "").strip()
        if not name:
            return None
        options = self._status_options(schema or {})
        if not options:
            return {"status": {"name": name[:100]}}
        match = options.get(name.lower())
        if match is None:
            self._unmapped_statuses.add(name)
            return None
        return {"status": {"name": match}}

    def _build_weird_page_properties(self, item: Dict[str, Any], schema: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Build Notion page properties for a weird proposal alert.

        Args:
            item: Alert item dictionary

        Returns:
            Notion properties dictionary
        """
        proposal_id = item.get('id', '')
        furious_url = self._build_furious_url(proposal_id)
        owner = item.get('alert_owner', '')
        date_value = self._format_date(item.get('date'))
        start_date_value = self._format_date(item.get('projet_start'))
        end_date_value = self._format_date(item.get('projet_stop'))

        schema = schema or {}
        properties: Dict[str, Any] = {}

        if self._schema_allows(schema, "Name"):
            properties["Name"] = {
                "title": [{"text": {"content": str(item.get('title', 'Unknown'))[:100]}}]
            }
        if self._schema_allows(schema, "ID Devis"):
            properties["ID Devis"] = {"rich_text": [{"text": {"content": str(proposal_id)}}]}
        if self._schema_allows(schema, "Client"):
            properties["Client"] = {"rich_text": [{"text": {"content": str(item.get('company_name', 'N/A'))[:100]}}]}
        if self._schema_allows(schema, "Montant"):
            properties["Montant"] = {"number": float(item.get('amount', 0))}
        if self._schema_allows(schema, "Statut"):
            status_payload = self._status_payload(item.get('statut', 'Unknown'), schema)
            if status_payload:
                properties["Statut"] = status_payload
        if self._schema_allows(schema, "Probabilite"):
            properties["Probabilite"] = {"number": float(item.get('probability', 0))}
        if self._schema_allows(schema, "Probleme"):
            properties["Probleme"] = self._build_probleme_multi_select(item.get('reason', ''))

        # Add date if available
        if date_value and self._schema_allows(schema, "Date"):
            properties["Date"] = {"date": {"start": date_value}}

        # Add start date if available
        if start_date_value and self._schema_allows(schema, "Début projet"):
            properties["Début projet"] = {"date": {"start": start_date_value}}

        # Add end date if available
        if end_date_value and self._schema_allows(schema, "Fin projet"):
            properties["Fin projet"] = {"date": {"start": end_date_value}}

        # Add Furious URL if available
        if furious_url and self._schema_allows(schema, "Lien Furious"):
            properties["Lien Furious"] = {"url": furious_url}

        # Typologie devis (from Furious cf_typologie_de_devis) - multi_select (comma-separated in Furious)
        if self._schema_allows(schema, "Typologie devis"):
            properties["Typologie devis"] = self._build_typologie_devis_multi_select(
                item.get("cf_typologie_de_devis", "")
            )

        # All assignees (Commercial / Chef de projet) from assigned_to
        assigned_to_str = item.get("assigned_to", "")
        identifiers = self._parse_assigned_to(assigned_to_str)
        commercials, chefs_de_projet = self._classify_assignees(identifiers)

        if self._schema_allows(schema, "Commercial"):
            properties["Commercial"] = self._build_people_property(commercials)
        if self._schema_allows(schema, "Chef de projet"):
            properties["Chef de projet"] = self._build_people_property(chefs_de_projet)

        # Add person property if owner can be mapped
        if owner and self._schema_allows(schema, "Responsable"):
            person_prop = self._build_person_property(owner)
            if person_prop.get("people"):
                properties["Responsable"] = person_prop

        return properties

    def _build_followup_page_properties(self, item: Dict[str, Any], schema: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Build Notion page properties for a follow-up alert.

        Args:
            item: Alert item dictionary

        Returns:
            Notion properties dictionary
        """
        proposal_id = item.get('id', '')
        furious_url = self._build_furious_url(proposal_id)
        owner = item.get('alert_owner', '')
        date_value = self._format_date(item.get('date'))
        start_date_value = self._format_date(item.get('projet_start'))
        end_date_value = self._format_date(item.get('projet_stop'))

        schema = schema or {}
        properties: Dict[str, Any] = {}

        if self._schema_allows(schema, "Name"):
            properties["Name"] = {
                "title": [{"text": {"content": str(item.get('title', 'Unknown'))[:100]}}]
            }
        if self._schema_allows(schema, "ID Devis"):
            properties["ID Devis"] = {"rich_text": [{"text": {"content": str(proposal_id)}}]}
        if self._schema_allows(schema, "Client"):
            properties["Client"] = {"rich_text": [{"text": {"content": str(item.get('company_name', 'N/A'))[:100]}}]}
        if self._schema_allows(schema, "Montant"):
            properties["Montant"] = {"number": float(item.get('amount', 0))}
        if self._schema_allows(schema, "Statut"):
            status_payload = self._status_payload(item.get('statut', 'Unknown'), schema)
            if status_payload:
                properties["Statut"] = status_payload
        if self._schema_allows(schema, "Probabilite"):
            properties["Probabilite"] = {"number": float(item.get('probability', 0))}

        # Add date if available
        if date_value and self._schema_allows(schema, "Date"):
            properties["Date"] = {"date": {"start": date_value}}

        # Add start date if available
        if start_date_value and self._schema_allows(schema, "Début projet"):
            properties["Début projet"] = {"date": {"start": start_date_value}}

        # Add end date if available
        if end_date_value and self._schema_allows(schema, "Fin projet"):
            properties["Fin projet"] = {"date": {"start": end_date_value}}

        # Add Furious URL if available
        if furious_url and self._schema_allows(schema, "Lien Furious"):
            properties["Lien Furious"] = {"url": furious_url}

        # Typologie devis (from Furious cf_typologie_de_devis) - multi_select (comma-separated in Furious)
        if self._schema_allows(schema, "Typologie devis"):
            properties["Typologie devis"] = self._build_typologie_devis_multi_select(
                item.get("cf_typologie_de_devis", "")
            )

        # All assignees (Commercial / Chef de projet) from assigned_to
        assigned_to_str = item.get("assigned_to", "")
        identifiers = self._parse_assigned_to(assigned_to_str)
        commercials, chefs_de_projet = self._classify_assignees(identifiers)

        if self._schema_allows(schema, "Commercial"):
            properties["Commercial"] = self._build_people_property(commercials)
        if self._schema_allows(schema, "Chef de projet"):
            properties["Chef de projet"] = self._build_people_property(chefs_de_projet)

        # Add person property if owner can be mapped
        if owner and self._schema_allows(schema, "Responsable"):
            person_prop = self._build_person_property(owner)
            if person_prop.get("people"):
                properties["Responsable"] = person_prop

        return properties

    @staticmethod
    def _extract_id_devis_from_page(page: Dict[str, Any]) -> str:
        """
        Extract Furious proposal id from an existing Notion page.

        Primary key: "ID Devis" rich_text.
        Fallback: parse "Lien Furious" url (cherche=...).
        """
        props = page.get("properties", {}) or {}

        # Preferred: ID Devis rich_text
        id_prop = props.get("ID Devis")
        if isinstance(id_prop, dict):
            rich = id_prop.get("rich_text") or []
            if rich and isinstance(rich, list):
                txt = rich[0].get("text", {}).get("content") if isinstance(rich[0], dict) else None
                if txt:
                    return str(txt).strip()

        # Fallback: parse Lien Furious url
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

    def list_pages(self, database_id: str) -> List[Dict[str, Any]]:
        """Every page of a database (duplicates included), with its properties."""
        pages: List[Dict[str, Any]] = []
        has_more = True
        start_cursor = None

        while has_more:
            response = self._query_pages(database_id=database_id, start_cursor=start_cursor)
            pages.extend(response.get("results", []))
            has_more = response.get("has_more", False)
            start_cursor = response.get("next_cursor")

        return pages

    def _get_existing_page_objects_by_id(self, database_id: str) -> Tuple[Dict[str, Dict[str, Any]], int]:
        """
        Build a mapping: proposal_id -> Notion page (with properties), plus the number of duplicates.

        If duplicates exist, keep the first and ignore the rest to avoid oscillation.
        """
        pages: Dict[str, Dict[str, Any]] = {}
        duplicates = 0
        for page in self.list_pages(database_id):
            proposal_id = self._extract_id_devis_from_page(page)
            if not proposal_id:
                continue
            if proposal_id in pages:
                duplicates += 1
                continue
            pages[proposal_id] = page
        return pages, duplicates

    def _get_existing_pages_by_id(self, database_id: str) -> Dict[str, str]:
        """
        Build a mapping: proposal_id -> notion_page_id for a database.
        """
        pages, _ = self._get_existing_page_objects_by_id(database_id)
        return {proposal_id: page["id"] for proposal_id, page in pages.items()}

    def _update_page(self, page_id: str, properties: Dict[str, Any]) -> bool:
        """Update an existing page properties (does not touch comments)."""
        try:
            self.client.pages.update(page_id=page_id, properties=properties)
            return True
        except Exception as e:
            print(f"    Warning: Could not update page {page_id}: {e}")
            return False

    def _get_existing_pages(self, database_id: str) -> List[str]:
        """
        Get all existing page IDs in a database.

        Args:
            database_id: The Notion database ID

        Returns:
            List of page IDs
        """
        page_ids = []
        has_more = True
        start_cursor = None

        while has_more:
            response = self._query_pages(database_id=database_id, start_cursor=start_cursor)
            for page in response.get("results", []):
                page_ids.append(page["id"])

            has_more = response.get("has_more", False)
            start_cursor = response.get("next_cursor")

        return page_ids

    def _archive_pages(self, page_ids: List[str]) -> int:
        """
        Archive (soft delete) pages.

        Args:
            page_ids: List of page IDs to archive

        Returns:
            Number of pages archived
        """
        archived = 0
        for page_id in page_ids:
            try:
                self.client.pages.update(page_id=page_id, archived=True)
                archived += 1
            except Exception as e:
                print(f"    Warning: Could not archive page {page_id}: {e}")
        return archived

    def _create_page(self, database_id: str, properties: Dict[str, Any]) -> Optional[str]:
        """
        Create a new page in a database.

        Args:
            database_id: Target database ID
            properties: Page properties

        Returns:
            Created page ID or None if failed
        """
        try:
            # Notion API 2025-09-03: databases can contain multiple data sources.
            # Creating pages should target the data source when available.
            try:
                ds_id = self._get_data_source_id_for_database(database_id)
                parent: Dict[str, Any] = {"data_source_id": ds_id}
            except Exception:
                parent = {"database_id": database_id}

            response = self.client.pages.create(parent=parent, properties=properties)
            return response.get("id")
        except Exception as e:
            print(f"    Warning: Could not create page: {e}")
            return None

    @staticmethod
    def _flatten(alerts_by_owner: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        """All alert items of all owners, each carrying its owner."""
        items = []
        for owner, owner_items in alerts_by_owner.items():
            for item in owner_items:
                item_copy = item.copy()
                item_copy.setdefault("alert_owner", owner)
                items.append(item_copy)
        return items

    def _sync_scoped_table(
        self,
        database_id: str,
        items: List[Dict[str, Any]],
        build: Callable[..., Dict[str, Any]],
        status_by_id: Optional[Dict[str, str]],
        today: Optional[date],
    ) -> Dict[str, int]:
        """Upsert the items of one table and apply the scope rule (see notion_scope).

        The items are the table's scope. Existing pages are updated with the
        properties that changed only (the Name/title is never rewritten). A page
        whose devis is not among the items left the scope: its "Statut" shows the
        current Furious status and it is archived (unticked "Dans le périmètre",
        ticked "Pris en charge" with its date) unless a person already did.
        """
        stats = {"created": 0, "updated": 0, "unchanged": 0, "left_scope": 0, "back_in_scope": 0,
                 "relabelled": 0, "orphans": 0, "unmapped_status": 0, "duplicates_in_notion": 0,
                 "archived": 0, "errors": 0}
        schema = self._get_database_schema(database_id)
        if schema:
            print(f"    Schema loaded ({len(schema)} properties).")
        else:
            print("    Warning: Could not fetch schema - will attempt all properties")
        pages_by_id, stats["duplicates_in_notion"] = self._get_existing_page_objects_by_id(database_id)
        print(f"    Found {len(pages_by_id)} existing page(s) with ID Devis/Lien Furious.")
        self._unmapped_statuses = set()

        run_ids: Set[str] = set()
        print(f"    Upserting {len(items)} item(s)...")
        for item in items:
            properties = build(item, schema=schema)
            proposal_id = str(item.get("id", "")).strip()
            run_ids.add(proposal_id)
            page = pages_by_id.get(proposal_id)
            if page is None:
                properties.update(scope_on_create(schema))
                if self._create_page(database_id, properties):
                    stats["created"] += 1
                else:
                    stats["errors"] += 1
                continue
            properties.pop("Name", None)   # keep the title (and its comments) as they are
            current = page.get("properties") or {}
            changed = {name: value for name, value in properties.items()
                       if value_from_payload(value) != value_from_page(current.get(name, {}))}
            back = scope_changes(page, True, schema, today)
            stats["back_in_scope"] += int(ARCHIVED_PROP in back)
            changed.update(back)
            if not changed:
                stats["unchanged"] += 1
            elif self._update_page(page["id"], changed):
                stats["updated"] += 1
            else:
                stats["errors"] += 1

        leftover_ids = [pid for pid in pages_by_id if pid not in run_ids]
        if leftover_ids and not run_ids:
            print("    Warning: empty run (Furious sent nothing?): pages left untouched today.")
            leftover_ids = []
        for proposal_id in leftover_ids:
            page = pages_by_id[proposal_id]
            changes: Dict[str, Any] = {}
            if status_by_id is not None and self._schema_allows(schema, "Statut"):
                furious_status = status_by_id.get(proposal_id)
                if furious_status is None:
                    stats["orphans"] += 1
                else:
                    payload = self._status_payload(furious_status, schema)
                    if payload is not None and value_from_payload(payload) != page_value(page, "Statut"):
                        changes["Statut"] = payload
            leaving = scope_changes(page, False, schema, today)
            stats["left_scope"] += int(ARCHIVED_PROP in leaving)
            changes.update(leaving)
            if not changes:
                continue
            if self._update_page(page["id"], changes):
                stats["relabelled"] += int("Statut" in changes)
            else:
                stats["errors"] += 1

        stats["unmapped_status"] = len(self._unmapped_statuses)
        if self._unmapped_statuses:
            print(f"    Warning: Furious status(es) with no matching Notion option, Statut not written: "
                  f"{sorted(self._unmapped_statuses)} (add the option in Notion to fix)")
        print(
            f"    Done: {stats['created']} created, {stats['updated']} updated, {stats['unchanged']} unchanged, "
            f"{len(leftover_ids)} out of scope ({stats['left_scope']} archived today, {stats['relabelled']} relabelled, "
            f"{stats['orphans']} gone from Furious), {stats['back_in_scope']} back in scope, {stats['errors']} errors"
        )
        return stats

    def sync_weird_proposals(
        self,
        weird_alerts: Dict[str, List[Dict[str, Any]]],
        status_by_id: Optional[Dict[str, str]] = None,
        today: Optional[date] = None,
    ) -> Dict[str, int]:
        """
        Sync the weird proposals (devis with a data problem) to "Devis à normaliser".

        Scope: the devis that have a problem today. A devis whose problem was fixed
        leaves the scope: it is archived (see notion_scope) and leaves the views.
        Only changed properties are written; the team owns "Pris en charge" (the
        sync only ticks it when a devis leaves) and "Date archivage".

        Args:
            weird_alerts: Dictionary mapping owner to list of alert items
            status_by_id: Furious status of every devis, by id (relabels the pages that left)
            today: archive date written by the sync (defaults to today)

        Returns:
            Sync statistics
        """
        if not self.weird_database_id:
            print("  Skipping weird proposals sync: NOTION_WEIRD_DATABASE_ID not configured")
            return {"created": 0, "updated": 0, "archived": 0, "errors": 0}
        print(f"\n  Syncing weird proposals to Notion...")
        print(f"    Database: {self.weird_database_id[:8]}...")
        return self._sync_scoped_table(self.weird_database_id, self._flatten(weird_alerts),
                                       self._build_weird_page_properties, status_by_id, today)

    def sync_followup_alerts(
        self,
        followup_alerts: Dict[str, List[Dict[str, Any]]],
        status_by_id: Optional[Dict[str, str]] = None,
        today: Optional[date] = None,
    ) -> Dict[str, int]:
        """
        Sync the follow-up alerts (every WAITING devis) to "Devis à suivre".

        Scope: every waiting devis. Only the properties Furious owns are written,
        and only when their value changed: Client, Montant, Statut, Probabilite,
        Date, Début projet, Fin projet, Typologie devis, Commercial, Chef de projet,
        Lien Furious (Name on creation only, so the title and the comments stay).

        The team owns "Commentaire", "Origine Transfo", "Pris en charge" and
        "Date archivage". The sync never unticks a "Pris en charge" ticked by a
        person (until 2026-09-24 it unticked every current devis each morning).

        A devis that is no longer waiting (won, lost) leaves the scope: its
        "Statut" shows the current Furious status and it is archived ("Dans le
        périmètre" unticked, "Pris en charge" ticked with its date, see
        notion_scope), so it leaves the views; six months later the monthly job
        moves it to the trash. A page whose devis is gone from Furious is archived
        the same way and counted as an orphan.

        Example: devis 263464 was "envoyée(s) attente réponse" and is marked
        "Perdu" in Furious. Next morning its page reads "Perdu", is archived and
        drops out of "Vue Globale"; its "Commentaire" is unchanged.

        Args:
            followup_alerts: Dictionary mapping owner to list of alert items
            status_by_id: Furious status of every devis (any status), by id. Needed
                to relabel pages that left the list; None leaves "Statut" as it is.
            today: archive date written by the sync (defaults to today)

        Returns:
            Sync statistics
        """
        if not self.followup_database_id:
            print("  Skipping follow-up sync: NOTION_FOLLOWUP_DATABASE_ID not configured")
            return {"created": 0, "updated": 0, "archived": 0, "errors": 0}
        print(f"\n  Syncing follow-up alerts to Notion...")
        print(f"    Database: {self.followup_database_id[:8]}...")
        return self._sync_scoped_table(self.followup_database_id, self._flatten(followup_alerts),
                                       self._build_followup_page_properties, status_by_id, today)

    def sync_all(
        self,
        alerts_output: AlertsOutput,
        status_by_id: Optional[Dict[str, str]] = None,
        today: Optional[date] = None,
    ) -> Dict[str, Dict[str, int]]:
        """
        Sync all alerts to Notion databases.

        Args:
            alerts_output: AlertsOutput containing all alerts
            status_by_id: Furious status of every devis, by id (see sync_followup_alerts)
            today: archive date written by the sync (defaults to today)

        Returns:
            Combined sync statistics for both databases
        """
        print("\n" + "=" * 50)
        print("Syncing Alerts to Notion")
        print("=" * 50)

        results = {
            "weird_proposals": self.sync_weird_proposals(alerts_output.weird_proposals, status_by_id, today),
            "commercial_followup": self.sync_followup_alerts(alerts_output.commercial_followup, status_by_id, today)
        }

        # Print summary
        total_created = results["weird_proposals"]["created"] + results["commercial_followup"]["created"]
        total_archived = results["weird_proposals"]["archived"] + results["commercial_followup"]["archived"]
        total_errors = results["weird_proposals"]["errors"] + results["commercial_followup"]["errors"]
        total_left = results["weird_proposals"].get("left_scope", 0) + results["commercial_followup"].get("left_scope", 0)

        print("\n" + "=" * 50)
        print("Notion Alerts Sync Complete")
        print(f"  Total created: {total_created}")
        print(f"  Total archived: {total_archived}")
        print(f"  Pages that left their table's scope today (archived): {total_left}")
        print(f"  Total errors: {total_errors}")
        print("=" * 50)

        return results

    def test_connection(self) -> bool:
        """
        Test connection to Notion databases.

        Returns:
            True if both databases are accessible
        """
        success = True

        if self.weird_database_id:
            try:
                self.client.databases.retrieve(database_id=self.weird_database_id)
                print(f"✓ Weird proposals database accessible: {self.weird_database_id[:8]}...")
            except Exception as e:
                print(f"✗ Weird proposals database error: {e}")
                success = False
        else:
            print("⚠ Weird proposals database not configured")

        if self.followup_database_id:
            try:
                self.client.databases.retrieve(database_id=self.followup_database_id)
                print(f"✓ Follow-up database accessible: {self.followup_database_id[:8]}...")
            except Exception as e:
                print(f"✗ Follow-up database error: {e}")
                success = False
        else:
            print("⚠ Follow-up database not configured")

        return success


def sync_alerts_to_notion(alerts_output: AlertsOutput) -> Dict[str, Dict[str, int]]:
    """
    Convenience function to sync alerts to Notion.

    Args:
        alerts_output: AlertsOutput containing all alerts

    Returns:
        Sync statistics
    """
    sync = NotionAlertsSync()
    return sync.sync_all(alerts_output)
