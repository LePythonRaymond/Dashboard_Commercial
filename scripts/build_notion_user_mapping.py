#!/usr/bin/env python3
"""
Build Notion User Mapping Script

Fetches all users from the Notion workspace and creates a JSON mapping file
that maps Furious owner identifiers to Notion user IDs.

Usage:
    python scripts/build_notion_user_mapping.py

The mapping is saved to: config/notion_user_mapping.json
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.integrations.notion_users import NotionUserMapper
from config.settings import settings


def main():
    """Main function to build and save the user mapping."""
    print("=" * 60)
    print("Notion User Mapping Builder")
    print("=" * 60)

    # Check for API key
    if not settings.notion_api_key:
        print("\nError: NOTION_API_KEY not set in environment.")
        print("Please add it to your .env file:")
        print("  NOTION_API_KEY=secret_your_api_key_here")
        sys.exit(1)

    print(f"\nUsing Notion API key: {settings.notion_api_key[:10]}...")

    try:
        # Create mapper and build mapping
        mapper = NotionUserMapper()
        mapping = mapper.build_mapping()

        if not mapping:
            print("\nWarning: No users found or mapping is empty.")
            print("Make sure your Notion integration has user information capabilities enabled.")
            sys.exit(1)

        # Save mapping
        mapper.save_mapping()

        print("\n" + "=" * 60)
        print("Mapping Summary")
        print("=" * 60)
        print(f"\nTotal mapping entries: {len(mapping)}")
        print("\nSample mappings:")
        for i, (key, value) in enumerate(list(mapping.items())[:10]):
            print(f"  '{key}' -> '{value[:8]}...'")
            if i >= 9:
                print(f"  ... and {len(mapping) - 10} more")
                break

        print("\n" + "=" * 60)
        print("Success!")
        print("=" * 60)
        print(f"\nMapping saved to: {mapper.mapping_file}")
        print("\nYou can now run the pipeline to sync alerts to Notion.")

    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
