"""
Notion User Mapping Module

Fetches workspace users from Notion API and builds a mapping
from Furious owner identifiers to Notion user IDs.
"""

import json
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Any
from notion_client import Client

from config.settings import settings


# Default path for the user mapping JSON file
DEFAULT_MAPPING_PATH = Path(__file__).parent.parent.parent / "config" / "notion_user_mapping.json"


class NotionUserMapper:
    """
    Maps Furious owner identifiers to Notion user IDs.

    Fetches users from Notion workspace and builds a mapping based on
    email prefix or name matching.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        mapping_file: Optional[Path] = None
    ):
        """
        Initialize the user mapper.

        Args:
            api_key: Notion API key. Defaults to settings.
            mapping_file: Path to the mapping JSON file. Defaults to config/notion_user_mapping.json
        """
        self.api_key = api_key or settings.notion_api_key
        self.mapping_file = Path(mapping_file) if mapping_file else DEFAULT_MAPPING_PATH
        self._client: Optional[Client] = None
        self._mapping: Optional[Dict[str, str]] = None

    @property
    def client(self) -> Client:
        """Get or create Notion client (Notion API 2025-09-03)."""
        if self._client is None:
            self._client = Client(auth=self.api_key, notion_version="2025-09-03")
        return self._client

    @staticmethod
    def _normalize_name(name: str) -> str:
        """
        Normalize a name for comparison.

        Converts to lowercase, removes dots, hyphens, and extra spaces.

        Args:
            name: The name to normalize

        Returns:
            Normalized name string
        """
        if not name:
            return ""
        # Convert to lowercase
        normalized = name.lower().strip()
        # Remove dots and hyphens, replace with nothing
        normalized = normalized.replace('.', '').replace('-', '')
        # Remove extra spaces
        normalized = ' '.join(normalized.split())
        return normalized

    @staticmethod
    def _extract_email_prefix(email: str) -> str:
        """
        Extract the prefix (username part) from an email address.

        Args:
            email: Full email address

        Returns:
            The part before the @ symbol, normalized
        """
        if not email or '@' not in email:
            return ""
        prefix = email.split('@')[0]
        return NotionUserMapper._normalize_name(prefix)

    def fetch_workspace_users(self) -> List[Dict[str, Any]]:
        """
        Fetch all users from the Notion workspace.

        Returns:
            List of user objects from Notion API
        """
        users = []
        has_more = True
        start_cursor = None

        print("Fetching Notion workspace users...")

        while has_more:
            try:
                params = {"page_size": 100}
                if start_cursor:
                    params["start_cursor"] = start_cursor

                response = self.client.users.list(**params)
                users.extend(response.get("results", []))
                has_more = response.get("has_more", False)
                start_cursor = response.get("next_cursor")

            except Exception as e:
                print(f"Error fetching users: {e}")
                break

        print(f"  Found {len(users)} users in workspace")
        return users

    def build_mapping(self, users: Optional[List[Dict[str, Any]]] = None) -> Dict[str, str]:
        """
        Build a mapping from Furious owner names to Notion user IDs.

        The mapping uses the following logic:
        1. Extract email prefix from Notion user email
        2. Normalize both Furious owner and Notion email prefix
        3. Match if normalized values are equal

        Args:
            users: List of Notion user objects. If None, fetches from API.

        Returns:
            Dictionary mapping furious_owner -> notion_user_id
        """
        if users is None:
            users = self.fetch_workspace_users()

        mapping = {}
        notion_users_info = []

        print("\nBuilding user mapping...")

        for user in users:
            user_id = user.get("id", "")
            user_type = user.get("type", "")
            name = user.get("name", "")

            # Skip bots
            if user_type == "bot":
                continue

            # Get email if available (for person type)
            email = ""
            if user_type == "person":
                person_info = user.get("person", {})
                email = person_info.get("email", "")

            if not email and not name:
                continue

            # Store user info for display
            notion_users_info.append({
                "id": user_id,
                "name": name,
                "email": email,
                "normalized_email_prefix": self._extract_email_prefix(email),
                "normalized_name": self._normalize_name(name)
            })

            # Create mappings based on email prefix
            if email:
                email_prefix = self._extract_email_prefix(email)
                if email_prefix:
                    mapping[email_prefix] = user_id

                    # Also add the original email prefix (with dots)
                    original_prefix = email.split('@')[0].lower()
                    if original_prefix != email_prefix:
                        mapping[original_prefix] = user_id

            # Create mapping based on name
            if name:
                normalized_name = self._normalize_name(name)
                # Split name into parts and try first name
                name_parts = name.lower().split()
                if name_parts:
                    first_name = name_parts[0]
                    if first_name not in mapping:
                        mapping[first_name] = user_id

                    # Also try full name without spaces
                    full_name_no_spaces = ''.join(name_parts)
                    if full_name_no_spaces not in mapping:
                        mapping[full_name_no_spaces] = user_id

        print(f"  Created {len(mapping)} mapping entries")

        # Print discovered users for reference
        print("\n  Notion Users Found:")
        for info in notion_users_info:
            print(f"    - {info['name']} ({info['email']}) -> {info['id'][:8]}...")

        self._mapping = mapping
        return mapping

    def save_mapping(self, mapping: Optional[Dict[str, str]] = None, path: Optional[Path] = None) -> None:
        """
        Save the mapping to a JSON file.

        Args:
            mapping: The mapping to save. Uses internal mapping if None.
            path: File path to save to. Uses default if None.
        """
        mapping_to_save = mapping or self._mapping
        if not mapping_to_save:
            raise ValueError("No mapping to save. Call build_mapping() first.")

        save_path = Path(path) if path else self.mapping_file

        # Ensure directory exists
        save_path.parent.mkdir(parents=True, exist_ok=True)

        with open(save_path, 'w', encoding='utf-8') as f:
            json.dump(mapping_to_save, f, indent=2, ensure_ascii=False)

        print(f"\n  Mapping saved to {save_path}")

    def load_mapping(self, path: Optional[Path] = None) -> Dict[str, str]:
        """
        Load the mapping from a JSON file.

        Args:
            path: File path to load from. Uses default if None.

        Returns:
            The loaded mapping dictionary

        Raises:
            FileNotFoundError: If the mapping file doesn't exist
        """
        load_path = Path(path) if path else self.mapping_file

        if not load_path.exists():
            raise FileNotFoundError(
                f"User mapping file not found: {load_path}\n"
                "Run 'python scripts/build_notion_user_mapping.py' to generate it."
            )

        with open(load_path, 'r', encoding='utf-8') as f:
            self._mapping = json.load(f)

        return self._mapping

    def get_mapping(self) -> Dict[str, str]:
        """
        Get the user mapping, loading from file if necessary.

        Returns:
            The user mapping dictionary
        """
        if self._mapping is None:
            try:
                self.load_mapping()
            except FileNotFoundError:
                # If file doesn't exist, build fresh mapping
                print("Mapping file not found, building fresh mapping...")
                self.build_mapping()
                self.save_mapping()

        return self._mapping or {}

    def get_notion_user_id(self, furious_owner: str) -> Optional[str]:
        """
        Get Notion user ID for a Furious owner identifier.

        Args:
            furious_owner: The owner identifier from Furious (e.g., "clemence", "vincent.delavarende")

        Returns:
            Notion user ID if found, None otherwise
        """
        mapping = self.get_mapping()

        if not furious_owner:
            return None

        # Try direct lookup
        owner_lower = furious_owner.lower()
        if owner_lower in mapping:
            return mapping[owner_lower]

        # Try normalized lookup
        normalized = self._normalize_name(furious_owner)
        if normalized in mapping:
            return mapping[normalized]

        # Try first part of owner (before dot)
        if '.' in owner_lower:
            first_part = owner_lower.split('.')[0]
            if first_part in mapping:
                return mapping[first_part]

        return None


# Singleton instance for easy access
_mapper_instance: Optional[NotionUserMapper] = None


def get_user_mapper() -> NotionUserMapper:
    """Get or create the global user mapper instance."""
    global _mapper_instance
    if _mapper_instance is None:
        _mapper_instance = NotionUserMapper()
    return _mapper_instance


def get_notion_user_id(furious_owner: str) -> Optional[str]:
    """
    Convenience function to get Notion user ID for a Furious owner.

    Args:
        furious_owner: The owner identifier from Furious

    Returns:
        Notion user ID if found, None otherwise
    """
    return get_user_mapper().get_notion_user_id(furious_owner)
