"""
Tests for ProjectsClient query building and filtering logic.
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch
import pandas as pd

from src.api.projects import ProjectsClient, ProjectsAPIError


def test_build_query_basic():
    """Test basic query construction without filters."""
    client = ProjectsClient()
    query = client._build_query(offset=0)

    assert "Project(" in query
    assert "limit:" in query
    assert "offset: 0" in query
    assert "order:" in query
    assert "created_at:desc" in query
    assert "id" in query
    assert "title" in query
    assert "cf_bu" in query


def test_build_query_with_filters():
    """Test query construction with date and BU filters."""
    client = ProjectsClient()
    created_at_min = "2026-01-15"
    cf_bu = "TRAVAUX"

    query = client._build_query(offset=0, created_at_min=created_at_min, cf_bu=cf_bu)

    assert f'created_at:{{gte:"{created_at_min}"}}' in query
    assert f'cf_bu:{{eq:"{cf_bu}"}}' in query


def test_build_query_date_format():
    """Test that date filter uses YYYY-MM-DD format."""
    client = ProjectsClient()
    created_at_min = "2026-01-15"

    query = client._build_query(offset=0, created_at_min=created_at_min)

    # Should contain the date in quotes
    assert f'"{created_at_min}"' in query
    assert "gte" in query.lower()


def test_fetch_recent_travaux_date_window():
    """Test that fetch_recent_travaux calculates correct date window."""
    client = ProjectsClient()

    with patch.object(client, '_fetch_page') as mock_fetch:
        # Mock successful response with empty results
        mock_fetch.return_value = {
            "success": True,
            "data": {"Project": []},
            "meta": {"totalElements": 0}
        }

        # Call the method
        result = client.fetch_recent_travaux(days=7)

        # Verify _fetch_page was called with correct date filter
        assert mock_fetch.called
        call_kwargs = mock_fetch.call_args[1]

        # Check that created_at_min is provided and in correct format
        assert "created_at_min" in call_kwargs
        created_at_min = call_kwargs["created_at_min"]
        assert isinstance(created_at_min, str)
        assert len(created_at_min) == 10  # YYYY-MM-DD format

        # Check that cf_bu filter is set
        assert call_kwargs.get("cf_bu") == "TRAVAUX"

        # Verify result is a DataFrame
        assert isinstance(result, pd.DataFrame)


def test_fetch_recent_travaux_client_side_filtering():
    """Test that client-side filtering enforces date window and BU."""
    client = ProjectsClient()

    # Create mock data with mixed dates and BUs
    now = datetime.now()
    window_start = now - timedelta(days=7)

    mock_projects = [
        {
            "id": 1,
            "title": "Project 1",
            "created_at": (now - timedelta(days=3)).strftime('%Y-%m-%d'),
            "cf_bu": "TRAVAUX",
            "type": "event",
            "total_amount": 1000
        },
        {
            "id": 2,
            "title": "Project 2",
            "created_at": (now - timedelta(days=10)).strftime('%Y-%m-%d'),  # Too old
            "cf_bu": "TRAVAUX",
            "type": "event",
            "total_amount": 2000
        },
        {
            "id": 3,
            "title": "Project 3",
            "created_at": (now - timedelta(days=2)).strftime('%Y-%m-%d'),
            "cf_bu": "CONCEPTION",  # Wrong BU
            "type": "event",
            "total_amount": 3000
        },
    ]

    with patch.object(client, '_fetch_page') as mock_fetch:
        mock_fetch.return_value = {
            "success": True,
            "data": {"Project": mock_projects},
            "meta": {"totalElements": 3}
        }

        result = client.fetch_recent_travaux(days=7)

        # Should only include Project 1 (within window and TRAVAUX)
        assert len(result) == 1
        assert result.iloc[0]['id'] == 1
        assert result.iloc[0]['cf_bu'] == 'TRAVAUX'


def test_fetch_recent_travaux_empty_result():
    """Test handling of empty results."""
    client = ProjectsClient()

    with patch.object(client, '_fetch_page') as mock_fetch:
        mock_fetch.return_value = {
            "success": True,
            "data": {"Project": []},
            "meta": {"totalElements": 0}
        }

        result = client.fetch_recent_travaux(days=7)

        assert isinstance(result, pd.DataFrame)
        assert len(result) == 0


def test_fetch_recent_travaux_api_error():
    """Test handling of API errors."""
    client = ProjectsClient()

    with patch.object(client, '_fetch_page') as mock_fetch:
        mock_fetch.return_value = {
            "success": False,
            "errors": ["Invalid filter"]
        }

        with pytest.raises(ProjectsAPIError):
            client.fetch_recent_travaux(days=7)
