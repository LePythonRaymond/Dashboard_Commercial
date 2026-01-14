#!/usr/bin/env python3
"""
Test script to debug Notion API connection and database access.
"""

import sys
import os
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from notion_client import Client
from config.settings import settings


def format_database_id(db_id: str, keep_dashes: bool = True) -> str:
    """Format Notion database ID for API calls."""
    if not db_id:
        return ""
    db_id = str(db_id).strip().strip('"').strip("'")
    # Remove any other whitespace characters (tabs, newlines, etc.)
    db_id = ''.join(db_id.split())
    # The Notion SDK accepts IDs with or without dashes
    # By default, we keep dashes as the SDK handles both formats
    if not keep_dashes:
        db_id = db_id.replace('-', '')
    return db_id


def test_notion_connection():
    """Test Notion API connection and database access."""
    print("=" * 60)
    print("Testing Notion API Connection")
    print("=" * 60)

    # Check API key
    api_key = settings.notion_api_key
    if not api_key:
        print("❌ ERROR: NOTION_API_KEY not set in environment")
        return False

    print(f"✓ API Key found: {api_key[:10]}...{api_key[-4:]}")

    # Check database ID
    raw_db_id = settings.notion_database_id
    if not raw_db_id:
        print("❌ ERROR: NOTION_DATABASE_ID not set in environment")
        return False

    print(f"✓ Raw Database ID: {raw_db_id}")

    # Format database ID (keep dashes - SDK handles both formats)
    clean_db_id = format_database_id(raw_db_id, keep_dashes=True)
    print(f"✓ Cleaned Database ID: {clean_db_id[:8]}...{clean_db_id[-8:]} (length: {len(clean_db_id)})")

    # Check length (with dashes: 36 chars, without: 32 chars)
    db_id_no_dashes = clean_db_id.replace('-', '')
    if len(db_id_no_dashes) != 32:
        print(f"⚠️  WARNING: Database ID should be 32 characters (without dashes), got {len(db_id_no_dashes)}")

    # Initialize client with latest API version
    try:
        client = Client(auth=api_key, notion_version="2025-09-03")
        print("✓ Notion client initialized")

        # Check client version
        print(f"✓ Client API version: {client.options.notion_version}")

    except Exception as e:
        print(f"❌ ERROR: Failed to initialize client: {e}")
        return False

    # Test 1: Try to retrieve the database (with dashes)
    print("\n" + "-" * 60)
    print("Test 1: Retrieve Database (with dashes)")
    print("-" * 60)
    try:
        db_info = client.databases.retrieve(database_id=clean_db_id)
        print("✓ Database retrieved successfully!")
        print(f"  Database Title: {db_info.get('title', [{}])[0].get('plain_text', 'N/A')}")
        print(f"  Database ID: {db_info.get('id', 'N/A')}")
        print(f"  Parent Type: {db_info.get('parent', {}).get('type', 'N/A')}")
        db_retrieved = True
    except Exception as e:
        error_msg = str(e)
        print(f"❌ ERROR: Failed to retrieve database with dashes")
        print(f"  Error: {error_msg}")
        db_retrieved = False

        # Try without dashes
        print("\n" + "-" * 60)
        print("Test 1b: Retrieve Database (without dashes)")
        print("-" * 60)
        try:
            db_id_no_dashes = clean_db_id.replace('-', '')
            db_info = client.databases.retrieve(database_id=db_id_no_dashes)
            print("✓ Database retrieved successfully (without dashes)!")
            print(f"  Database Title: {db_info.get('title', [{}])[0].get('plain_text', 'N/A')}")
            print(f"  Database ID: {db_info.get('id', 'N/A')}")
            print(f"  Parent Type: {db_info.get('parent', {}).get('type', 'N/A')}")
            clean_db_id = db_id_no_dashes  # Use format that worked
            db_retrieved = True
        except Exception as e2:
            error_msg2 = str(e2)
            print(f"❌ ERROR: Failed to retrieve database without dashes")
            print(f"  Error: {error_msg2}")
            db_retrieved = False

    if not db_retrieved:
        # Try searching for the database
        print("\n" + "-" * 60)
        print("Test 1c: Search for Accessible Databases")
        print("-" * 60)
        try:
            # Search for all databases accessible to the integration
            # Note: API changed - use "data_source" instead of "database"
            search_results = client.search(
                filter={"property": "object", "value": "data_source"},
                page_size=10
            )
            print(f"✓ Found {len(search_results.get('results', []))} accessible databases:")
            for db in search_results.get('results', []):
                db_id = db.get('id', 'N/A')
                db_title = db.get('title', [{}])[0].get('plain_text', 'Untitled')
                print(f"  - {db_title} (ID: {db_id})")

                # Check if this matches our database ID (with or without dashes)
                db_id_clean = db_id.replace('-', '')
                our_db_id_clean = clean_db_id.replace('-', '')
                if db_id_clean == our_db_id_clean:
                    print(f"    ✓ This matches your configured database ID!")
                    clean_db_id = db_id  # Use the exact ID from Notion
                    db_retrieved = True
                    break

            if not db_retrieved:
                print("\n  ⚠️  Your configured database ID was not found in accessible databases.")
                print("  This suggests:")
                print("  1. The database ID might be incorrect")
                print("  2. The integration doesn't have access to the database")
                print("  3. The database might be in a different workspace")
                print("\n  To fix:")
                print("  1. Open the database in Notion")
                print("  2. Click 'Share' → 'Add connections'")
                print("  3. Select your integration")
                print("  4. Copy the database ID from the URL")
                print("     (The 32-char ID in the URL, with or without dashes)")
                return False
        except Exception as e:
            print(f"❌ ERROR: Failed to search databases: {e}")
            return False

    if not db_retrieved:
        return False

    # Test 2: Try to query the database via data source
    print("\n" + "-" * 60)
    print("Test 2: Query Database (via Data Source)")
    print("-" * 60)
    try:
        # Get database to find data source
        db_info = client.databases.retrieve(database_id=clean_db_id)
        data_sources = db_info.get("data_sources", [])

        if not data_sources:
            print("❌ ERROR: Database has no data sources")
            return False

        data_source_id = data_sources[0].get("id")
        if not data_source_id:
            print("❌ ERROR: Data source ID not found")
            return False

        # Format data source ID (remove dashes)
        data_source_id_clean = data_source_id.replace('-', '')
        print(f"  Using data source ID: {data_source_id_clean[:8]}...{data_source_id_clean[-8:]}")

        # Query the data source using direct request
        response = client.request(
            path=f"data_sources/{data_source_id}/query",
            method="POST",
            body={"page_size": 1}
        )
        print("✓ Data source query successful!")
        print(f"  Found {len(response.get('results', []))} pages")
        return True
    except Exception as e:
        error_msg = str(e)
        print(f"❌ ERROR: Failed to query data source")
        print(f"  Error: {error_msg}")
        return False

    # Test 3: Try to create a test page
    print("\n" + "-" * 60)
    print("Test 3: Create Test Page")
    print("-" * 60)
    try:
        # First, get database properties to understand the schema
        db_info = client.databases.retrieve(database_id=clean_db_id)
        properties = db_info.get('properties', {})

        # Find the title property
        title_prop = None
        for prop_name, prop_info in properties.items():
            if prop_info.get('type') == 'title':
                title_prop = prop_name
                break

        if not title_prop:
            print("⚠️  WARNING: No title property found, using 'Name'")
            title_prop = "Name"

        # Build minimal properties
        test_properties = {
            title_prop: {
                "title": [
                    {
                        "text": {
                            "content": "Test Page - Delete Me"
                        }
                    }
                ]
            }
        }

        # Try to create page
        response = client.pages.create(
            parent={"database_id": clean_db_id},
            properties=test_properties
        )

        page_id = response.get("id")
        print("✓ Test page created successfully!")
        print(f"  Page ID: {page_id}")
        print(f"  Page URL: {response.get('url', 'N/A')}")
        print("\n  ⚠️  Remember to delete this test page!")
        return True

    except Exception as e:
        error_msg = str(e)
        print(f"❌ ERROR: Failed to create test page")
        print(f"  Error: {error_msg}")

        # Check for specific error types
        if "404" in error_msg or "object_not_found" in error_msg.lower():
            print("\n  This suggests the database ID is correct but:")
            print("  - The integration may not have 'insert content' capability")
            print("  - Or there's an issue with the parent database reference")
        elif "validation_error" in error_msg.lower():
            print("\n  This suggests a property schema mismatch")
            print("  Check that property names match the database schema")

        return False


if __name__ == "__main__":
    success = test_notion_connection()
    sys.exit(0 if success else 1)
