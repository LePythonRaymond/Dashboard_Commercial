#!/usr/bin/env python3
"""
Test script to debug Notion sync and see what data is being sent.
"""

import pytest
pytest.skip("Legacy manual script (Notion Gantt sync was removed). Excluded from pytest collection.", allow_module_level=True)

import sys
import os
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.api.projects import ProjectsClient
from src.api.auth import FuriousAuth
from src.integrations.notion_sync import NotionSync
from config.settings import settings


def test_notion_sync():
    """Test Notion sync with actual project data."""
    print("=" * 60)
    print("Testing Notion Sync with Project Data")
    print("=" * 60)

    # Step 1: Fetch projects
    print("\n1. Fetching projects from API...")
    try:
        auth = FuriousAuth()
        projects_client = ProjectsClient(auth=auth)
        df_travaux = projects_client.fetch_travaux_for_gantt(horizon_days=90)

        if df_travaux.empty:
            print("  ✗ No projects found!")
            print("  This could mean:")
            print("    - No TRAVAUX projects in the date range")
            print("    - Projects don't have start_date/end_date")
            print("    - Projects don't match the filters")
            return False

        print(f"  ✓ Found {len(df_travaux)} projects")

        # Show first project data
        if len(df_travaux) > 0:
            first_project = df_travaux.iloc[0].to_dict()
            print(f"\n  First project sample:")
            print(f"    ID: {first_project.get('id')}")
            print(f"    Title: {first_project.get('title')}")
            print(f"    Type: {first_project.get('type')}")
            print(f"    Type Label: {first_project.get('type_label')}")
            print(f"    Company: {first_project.get('company_name')}")
            print(f"    Start Date: {first_project.get('start_date')}")
            print(f"    End Date: {first_project.get('end_date')}")
            print(f"    Project Manager: {first_project.get('project_manager')}")
            print(f"    Total Amount: {first_project.get('total_amount')}")
            print(f"    All columns: {list(df_travaux.columns)}")

    except Exception as e:
        print(f"  ✗ Error fetching projects: {e}")
        import traceback
        traceback.print_exc()
        return False

    # Step 2: Initialize Notion sync
    print("\n2. Initializing Notion sync...")
    try:
        notion_sync = NotionSync()

        # Test connection
        if not notion_sync.database_id:
            print("  ✗ Database ID not set!")
            return False

        print(f"  ✓ Database ID: {notion_sync.database_id[:8]}...{notion_sync.database_id[-8:]}")

        # Get schema
        schema = notion_sync._get_database_schema()
        print(f"  ✓ Database schema retrieved")
        print(f"    Available properties: {list(schema.keys())}")

    except Exception as e:
        print(f"  ✗ Error initializing Notion sync: {e}")
        import traceback
        traceback.print_exc()
        return False

    # Step 3: Test building properties for first project
    print("\n3. Testing property building...")
    try:
        if len(df_travaux) > 0:
            first_project = df_travaux.iloc[0].to_dict()
            properties = notion_sync._build_page_properties(first_project)

            print(f"  ✓ Properties built:")
            for prop_name, prop_value in properties.items():
                print(f"    - {prop_name}: {prop_value}")

            # Check if dates are set
            if "Dates" in properties:
                print(f"  ✓ Dates property is set!")
            else:
                print(f"  ✗ Dates property is NOT set!")
                print(f"    Schema has 'Dates': {'Dates' in schema}")
                print(f"    Project start_date: {first_project.get('start_date')}")
                print(f"    Project end_date: {first_project.get('end_date')}")

    except Exception as e:
        print(f"  ✗ Error building properties: {e}")
        import traceback
        traceback.print_exc()
        return False

    # Step 4: Try to create a test page
    print("\n4. Testing page creation...")
    try:
        if len(df_travaux) > 0:
            first_project = df_travaux.iloc[0].to_dict()
            page_id = notion_sync.create_page(first_project)

            if page_id:
                print(f"  ✓ Test page created successfully!")
                print(f"    Page ID: {page_id}")
                return True
            else:
                print(f"  ✗ Failed to create test page")
                return False
        else:
            print("  ✗ No projects to test with")
            return False

    except Exception as e:
        print(f"  ✗ Error creating page: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = test_notion_sync()
    sys.exit(0 if success else 1)
