"""
Notion MAINTENANCE Won Proposals Sync Module

Syncs current month's proposals won by MAINTENANCE BU to a dedicated Notion database.
Upserts by ID Devis to avoid duplicates; no archiving so the database accumulates all months.
"""

from typing import Dict, List, Any, Optional, Set, Tuple
from urllib.parse import urlparse, parse_qs
import pandas as pd
from notion_client import Client
from notion_client.errors import APIResponseError, APIErrorCode

from config.settings import settings, VIP_COMMERCIALS
from .notion_users import get_user_mapper, NotionUserMapper


FURIOUS_URL_TEMPLATE = "https://merciraymond.furious-squad.com/compta.php?view=5&cherche={id}"


class NotionMaintenanceWonSync:
    """
    Syncs MAINTENANCE won proposals (current month) to a Notion database.

    Strategy: Upsert by ID Devis. If proposal already exists, update properties
    but keep Name/title unchanged (preserve manual renames). No archiving.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        database_id: Optional[str] = None,
        user_mapper: Optional[NotionUserMapper] = None
    ):
        self.api_key = api_key or settings.notion_api_key
        self.database_id = self._format_database_id(
            database_id or settings.notion_maintenance_won_database_id
        )
        self.user_mapper = user_mapper or get_user_mapper()
        self._client: Optional[Client] = None
        self._data_source_id: Optional[str] = None

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

    def _get_data_source_id_for_database(self) -> str:
        """Resolve data_source_id from database_id for API 2025-09-03."""
        if self._data_source_id:
            return self._data_source_id
        if not self.database_id:
            raise RuntimeError("Database ID is empty; cannot resolve data source id.")
        try:
            db = self.client.databases.retrieve(database_id=self.database_id)
            data_sources = db.get("data_sources") or []
            if not data_sources or not isinstance(data_sources, list) or not isinstance(data_sources[0], dict):
                raise RuntimeError(
                    "Notion database has no `data_sources` field; cannot query pages safely."
                )
            ds_id = str(data_sources[0].get("id") or "").strip()
            if not ds_id:
                raise RuntimeError("Notion database data_sources[0].id is empty.")
            self._data_source_id = ds_id
            return ds_id
        except APIResponseError as e:
            if e.code in (APIErrorCode.ObjectNotFound, APIErrorCode.ValidationError):
                data_sources_ep = getattr(self.client, "data_sources", None)
                if data_sources_ep is not None and hasattr(data_sources_ep, "retrieve"):
                    try:
                        _ = data_sources_ep.retrieve(data_source_id=self.database_id)
                        self._data_source_id = self.database_id
                        return self.database_id
                    except Exception:
                        pass
            raise

    def _query_pages(self, start_cursor: Optional[str] = None) -> Dict[str, Any]:
        """Query pages for this database (databases.query or data_sources.query)."""
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
            "Refusing to sync to avoid duplicate page creation."
        )

    def _get_database_schema(self) -> Dict[str, Any]:
        """Get database schema; support data_sources fallback for API 2025-09-03."""
        if not self.database_id:
            return {}
        try:
            db_info = self.client.databases.retrieve(database_id=self.database_id)
            props = db_info.get("properties") or {}
            if isinstance(props, dict) and props:
                return props
            data_sources = db_info.get("data_sources") or []
            if isinstance(data_sources, list) and data_sources and isinstance(data_sources[0], dict):
                ds_id = str(data_sources[0].get("id") or "").strip()
                if ds_id:
                    self._data_source_id = ds_id
                    data_sources_ep = getattr(self.client, "data_sources", None)
                    if data_sources_ep is not None and hasattr(data_sources_ep, "retrieve"):
                        ds_info = data_sources_ep.retrieve(data_source_id=ds_id)
                        ds_props = ds_info.get("properties") or {}
                        if isinstance(ds_props, dict) and ds_props:
                            return ds_props
            return {}
        except APIResponseError as e:
            if e.code == APIErrorCode.ObjectNotFound:
                data_sources_ep = getattr(self.client, "data_sources", None)
                if data_sources_ep is not None and hasattr(data_sources_ep, "retrieve"):
                    try:
                        ds_info = data_sources_ep.retrieve(data_source_id=self.database_id)
                        self._data_source_id = self.database_id
                        ds_props = ds_info.get("properties") or {}
                        if isinstance(ds_props, dict) and ds_props:
                            return ds_props
                    except Exception:
                        pass
            print(
                "    Error: Could not access Notion database/data source. "
                f"(id={self.database_id})"
            )
            return {}
        except Exception as e:
            print(f"    Warning: Could not fetch database schema: {e}")
            return {}

    @staticmethod
    def _format_date(value: Any) -> Optional[str]:
        """Format date for Notion API (YYYY-MM-DD). Handles pandas Timestamp, NaT, and strings."""
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return None
        if value == 'None':
            return None
        if hasattr(value, 'strftime'):
            if pd.isna(value):
                return None
            return value.strftime('%Y-%m-%d')
        if isinstance(value, str) and len(value) >= 10:
            return value[:10]
        return None

    def _build_furious_url(self, proposal_id: str) -> str:
        if not proposal_id:
            return ""
        return FURIOUS_URL_TEMPLATE.format(id=proposal_id)

    @staticmethod
    def _parse_assigned_to(assigned_to: Any) -> List[str]:
        if assigned_to is None:
            return []
        s = str(assigned_to).strip()
        if not s or s == "N/A" or s.lower() in ("nan", "none"):
            return []
        parts = s.split()
        if len(parts) == 1:
            parts = s.split(",")
        if len(parts) == 1:
            parts = s.split(";")
        return [p.lower().strip() for p in parts if p and p.strip()]

    @staticmethod
    def _normalize_identifier(identifier: str) -> str:
        if not identifier:
            return ""
        normalized = identifier.lower().strip().replace(".", "").replace("-", "")
        return " ".join(normalized.split())

    def _classify_assignees(self, identifiers: List[str]) -> Tuple[List[str], List[str]]:
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
        people: List[Dict[str, str]] = []
        seen: Set[str] = set()
        for identifier in identifiers:
            user_id = self.user_mapper.get_notion_user_id(identifier)
            if user_id and user_id not in seen:
                people.append({"object": "user", "id": user_id})
                seen.add(user_id)
        return {"people": people}

    @staticmethod
    def _schema_allows(schema: Dict[str, Any], prop_name: str) -> bool:
        if schema:
            return prop_name in schema
        safe = {
            "Name", "ID Devis", "Client", "Montant", "Statut", "Probabilite",
            "Date", "Début projet", "Fin projet", "Lien Furious", "Commercial", "Chef de projet",
            "Mois signé",
        }
        return prop_name in safe

    @staticmethod
    def _build_typologie_devis_multi_select(value: str) -> Dict[str, Any]:
        """Build Notion multi_select from Furious cf_typologie_de_devis (comma-separated)."""
        if not value or not str(value).strip():
            return {"multi_select": []}
        parts = [p.strip()[:100] for p in str(value).split(",") if p.strip()]
        return {"multi_select": [{"name": p} for p in parts]}

    def _build_page_properties(self, item: Dict[str, Any], schema: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Build Notion page properties for a MAINTENANCE won proposal."""
        schema = schema or {}
        proposal_id = str(item.get('id', '')).strip()
        furious_url = self._build_furious_url(proposal_id)
        date_value = self._format_date(item.get('date'))
        start_value = self._format_date(item.get('projet_start'))
        end_value = self._format_date(item.get('projet_stop'))
        signature_value = self._format_date(item.get('signature_date') or item.get('date_effective_won'))

        properties: Dict[str, Any] = {}
        if self._schema_allows(schema, "Name"):
            properties["Name"] = {
                "title": [{"text": {"content": str(item.get('title', 'Unknown'))[:100]}}]
            }
        if self._schema_allows(schema, "ID Devis") and proposal_id:
            properties["ID Devis"] = {"rich_text": [{"text": {"content": proposal_id}}]}
        if self._schema_allows(schema, "Client"):
            properties["Client"] = {"rich_text": [{"text": {"content": str(item.get('company_name', 'N/A'))[:100]}}]}
        if self._schema_allows(schema, "Montant"):
            try:
                properties["Montant"] = {"number": float(item.get('amount', 0))}
            except (TypeError, ValueError):
                properties["Montant"] = {"number": 0.0}
        if self._schema_allows(schema, "Statut"):
            statut_val = str(item.get('statut', item.get('statut_clean', 'Unknown')))[:100]
            properties["Statut"] = {"status": {"name": statut_val}}
        if self._schema_allows(schema, "Probabilite"):
            try:
                properties["Probabilite"] = {"number": float(item.get('probability', 0))}
            except (TypeError, ValueError):
                properties["Probabilite"] = {"number": 0.0}
        if date_value and self._schema_allows(schema, "Date"):
            properties["Date"] = {"date": {"start": date_value}}
        if start_value and self._schema_allows(schema, "Début projet"):
            properties["Début projet"] = {"date": {"start": start_value}}
        if end_value and self._schema_allows(schema, "Fin projet"):
            properties["Fin projet"] = {"date": {"start": end_value}}
        if signature_value and self._schema_allows(schema, "Mois signé"):
            properties["Mois signé"] = {"date": {"start": signature_value}}
        if furious_url and self._schema_allows(schema, "Lien Furious"):
            properties["Lien Furious"] = {"url": furious_url}

        # Typologie devis (from Furious cf_typologie_de_devis) - multi_select (comma-separated in Furious)
        if self._schema_allows(schema, "Typologie devis"):
            properties["Typologie devis"] = self._build_typologie_devis_multi_select(
                item.get("cf_typologie_de_devis", "")
            )

        assigned_to_str = item.get("assigned_to", "")
        identifiers = self._parse_assigned_to(assigned_to_str)
        commercials, chefs_de_projet = self._classify_assignees(identifiers)
        if self._schema_allows(schema, "Commercial"):
            properties["Commercial"] = self._build_people_property(commercials)
        if self._schema_allows(schema, "Chef de projet"):
            properties["Chef de projet"] = self._build_people_property(chefs_de_projet)

        return properties

    @staticmethod
    def _extract_id_devis_from_page(page: Dict[str, Any]) -> str:
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
        mapping: Dict[str, str] = {}
        has_more = True
        start_cursor = None
        if not self.database_id:
            return mapping
        while has_more:
            response = self._query_pages(start_cursor=start_cursor)
            for page in response.get("results", []):
                proposal_id = self._extract_id_devis_from_page(page)
                if proposal_id:
                    mapping.setdefault(proposal_id, page["id"])
            has_more = response.get("has_more", False)
            start_cursor = response.get("next_cursor")
        return mapping

    def _create_page(self, properties: Dict[str, Any]) -> Optional[str]:
        if not self.database_id:
            return None
        try:
            try:
                ds_id = self._get_data_source_id_for_database()
                parent: Dict[str, Any] = {"data_source_id": ds_id}
            except Exception:
                parent = {"database_id": self.database_id}
            response = self.client.pages.create(parent=parent, properties=properties)
            return response.get("id")
        except Exception as e:
            print(f"    Warning: Could not create page: {e}")
            return None

    def _update_page(self, page_id: str, properties: Dict[str, Any]) -> bool:
        try:
            self.client.pages.update(page_id=page_id, properties=properties)
            return True
        except Exception as e:
            print(f"    Warning: Could not update page {page_id}: {e}")
            return False

    def sync_maintenance_won(self, items: List[Dict[str, Any]]) -> Dict[str, int]:
        """
        Sync MAINTENANCE won proposals to Notion. Upsert by ID Devis; no archiving.
        """
        stats = {"created": 0, "updated": 0, "errors": 0}
        if not self.database_id:
            print("  Skipping MAINTENANCE won sync: NOTION_MAINTENANCE_WON_DATABASE_ID not configured")
            return stats

        print("\n  Syncing MAINTENANCE won proposals to Notion...")
        print(f"    Database: {self.database_id[:8]}...")
        schema = self._get_database_schema()
        if not schema:
            print("    Error: Could not fetch schema; refusing to create/update pages.")
            stats["errors"] = len(items)
            return stats
        if schema:
            print(f"    Schema loaded ({len(schema)} properties).")
        existing_by_id = self._get_existing_pages_by_id()
        print(f"    Found {len(existing_by_id)} existing page(s) with ID Devis.")
        print(f"    Upserting {len(items)} proposal(s)...")
        for item in items:
            properties = self._build_page_properties(item, schema=schema)
            proposal_id = str(item.get("id", "")).strip()
            existing_page_id = existing_by_id.get(proposal_id)
            if existing_page_id:
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
        print(f"    Done: {stats['created']} created, {stats['updated']} updated, {stats['errors']} errors")
        return stats


def sync_maintenance_won_to_notion(items: List[Dict[str, Any]]) -> Dict[str, int]:
    """Convenience function to sync MAINTENANCE won proposals to Notion."""
    sync = NotionMaintenanceWonSync()
    return sync.sync_maintenance_won(items)
