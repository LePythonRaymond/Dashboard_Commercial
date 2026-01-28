"""
Unit tests for typologie allocation logic.

Tests cover:
- Single tag parsing
- Multi-tag parsing with Animation demotion
- TS detection (by tag and by title)
- Primary typologie selection rules
- Edge cases (empty, NaN, whitespace)
"""

import sys
from pathlib import Path
import pandas as pd
import numpy as np

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.processing.typologie_allocation import (
    parse_typologie_list,
    title_has_ts,
    detect_ts,
    inject_ts_tag,
    choose_primary_typologie,
    allocate_typologie_for_row
)


def test_parse_typologie_list_single():
    """Test parsing single typologie tag."""
    assert parse_typologie_list("Conception Paysage") == ["Conception Paysage"]
    assert parse_typologie_list("Conception DV") == ["Conception DV"]
    assert parse_typologie_list("Maintenance Animation") == ["Maintenance Animation"]


def test_parse_typologie_list_multi():
    """Test parsing multiple typologie tags."""
    assert parse_typologie_list("Conception Paysage, Maintenance Animation") == ["Conception Paysage", "Maintenance Animation"]
    assert parse_typologie_list("Conception DV, Conception Paysage, Maintenance Animation") == ["Conception DV", "Conception Paysage", "Maintenance Animation"]
    assert parse_typologie_list("Maintenance Animation, Conception Paysage") == ["Maintenance Animation", "Conception Paysage"]


def test_parse_typologie_list_whitespace():
    """Test parsing with various whitespace."""
    assert parse_typologie_list("  Conception Paysage ,  Maintenance Animation ") == ["Conception Paysage", "Maintenance Animation"]
    assert parse_typologie_list("Conception DV,Conception Paysage") == ["Conception DV", "Conception Paysage"]


def test_parse_typologie_list_empty():
    """Test parsing empty/NaN values."""
    assert parse_typologie_list("") == []
    assert parse_typologie_list(None) == []
    assert parse_typologie_list(pd.NA) == []
    assert parse_typologie_list("nan") == []
    assert parse_typologie_list("Non défini") == []


def test_title_has_ts():
    """Test TS detection in titles."""
    assert title_has_ts("Project TS") == True
    assert title_has_ts("TS Project") == True
    assert title_has_ts("Project (TS)") == True
    assert title_has_ts("TS-123") == True
    assert title_has_ts("Project") == False
    assert title_has_ts("") == False
    assert title_has_ts(None) == False


def test_detect_ts():
    """Test TS detection from tags or title."""
    # TS in tags
    assert detect_ts(["TS"], "Project") == True
    assert detect_ts(["DV", "TS"], "Project") == True

    # TS in title
    assert detect_ts(["DV"], "Project TS") == True
    assert detect_ts([], "Project TS") == True

    # No TS
    assert detect_ts(["DV"], "Project") == False
    assert detect_ts([], "Project") == False


def test_inject_ts_tag():
    """Test TS tag injection."""
    # TS already in tags
    assert inject_ts_tag(["TS"], "Project") == ["TS"]
    assert inject_ts_tag(["DV", "TS"], "Project") == ["DV", "TS"]

    # TS in title, not in tags
    assert inject_ts_tag(["DV"], "Project TS") == ["DV", "TS"]
    assert inject_ts_tag([], "Project TS") == ["TS"]

    # No TS
    assert inject_ts_tag(["DV"], "Project") == ["DV"]
    assert inject_ts_tag([], "Project") == []


def test_choose_primary_typologie_ts_priority():
    """Test Maintenance TS has highest priority for primary selection."""
    assert choose_primary_typologie(["Maintenance TS"]) == "Maintenance TS"
    assert choose_primary_typologie(["Conception DV", "Maintenance TS"]) == "Maintenance TS"
    assert choose_primary_typologie(["Maintenance TS", "Conception DV"]) == "Maintenance TS"
    assert choose_primary_typologie(["Maintenance Animation", "Maintenance TS", "Conception DV"]) == "Maintenance TS"


def test_choose_primary_typologie_animation_demotion():
    """Test Maintenance Animation demotion when multiple tags."""
    # Maintenance Animation first, other tag exists
    assert choose_primary_typologie(["Maintenance Animation", "Conception Paysage"]) == "Conception Paysage"
    assert choose_primary_typologie(["Maintenance Animation", "Conception DV"]) == "Conception DV"

    # Multiple tags, Maintenance Animation not first
    assert choose_primary_typologie(["Conception Paysage", "Maintenance Animation"]) == "Conception Paysage"
    assert choose_primary_typologie(["Conception DV", "Maintenance Animation", "Conception Paysage"]) == "Conception DV"

    # Only Maintenance Animation
    assert choose_primary_typologie(["Maintenance Animation"]) == "Maintenance Animation"

    # All Maintenance Animation
    assert choose_primary_typologie(["Maintenance Animation", "Maintenance Animation"]) == "Maintenance Animation"


def test_choose_primary_typologie_single_tag():
    """Test single tag selection."""
    assert choose_primary_typologie(["Conception DV"]) == "Conception DV"
    assert choose_primary_typologie(["Conception Paysage"]) == "Conception Paysage"
    assert choose_primary_typologie(["Maintenance Entretien"]) == "Maintenance Entretien"


def test_choose_primary_typologie_empty():
    """Test empty tag list."""
    assert choose_primary_typologie([]) is None


def test_allocate_typologie_for_row_single_tag():
    """Test allocation for single tag."""
    row = pd.Series({
        'cf_typologie_de_devis': 'Conception Paysage',
        'title': 'Project'
    })
    tags, primary = allocate_typologie_for_row(row)
    assert 'Conception Paysage' in tags
    assert primary == 'Conception Paysage'


def test_allocate_typologie_for_row_multi_tag():
    """Test allocation for multiple tags."""
    row = pd.Series({
        'cf_typologie_de_devis': 'Conception Paysage, Maintenance Animation',
        'title': 'Project'
    })
    tags, primary = allocate_typologie_for_row(row)
    assert 'Conception Paysage' in tags
    assert 'Maintenance Animation' in tags
    assert primary == 'Conception Paysage'  # Maintenance Animation demoted


def test_allocate_typologie_for_row_ts_by_title():
    """Test Maintenance TS detection from title."""
    row = pd.Series({
        'cf_typologie_de_devis': 'Maintenance Entretien',
        'title': 'Project TS'
    })
    tags, primary = allocate_typologie_for_row(row)
    assert 'Maintenance TS' in tags
    assert 'Maintenance Entretien' in tags
    assert primary == 'Maintenance TS'  # Maintenance TS has priority


def test_allocate_typologie_for_row_ts_by_tag():
    """Test Maintenance TS detection from tag."""
    row = pd.Series({
        'cf_typologie_de_devis': 'Maintenance TS',
        'title': 'Project'
    })
    tags, primary = allocate_typologie_for_row(row)
    assert 'Maintenance TS' in tags
    assert primary == 'Maintenance TS'


def test_allocate_typologie_for_row_ts_both():
    """Test Maintenance TS detection when both tag and title have TS (no double counting)."""
    row = pd.Series({
        'cf_typologie_de_devis': 'Maintenance TS',
        'title': 'Project TS'
    })
    tags, primary = allocate_typologie_for_row(row)
    # Maintenance TS should appear only once in tags
    assert tags.count('Maintenance TS') == 1
    assert primary == 'Maintenance TS'


def test_allocate_typologie_for_row_animation_first():
    """Test Maintenance Animation demotion when first in list."""
    row = pd.Series({
        'cf_typologie_de_devis': 'Maintenance Animation, Conception Paysage',
        'title': 'Project'
    })
    tags, primary = allocate_typologie_for_row(row)
    assert 'Maintenance Animation' in tags
    assert 'Conception Paysage' in tags
    assert primary == 'Conception Paysage'  # Maintenance Animation demoted


def test_allocate_typologie_for_row_empty():
    """Test allocation for empty typologie."""
    row = pd.Series({
        'cf_typologie_de_devis': '',
        'title': 'Project'
    })
    tags, primary = allocate_typologie_for_row(row)
    assert tags == []
    assert primary is None


def test_allocate_typologie_for_row_deduplication():
    """Test tag deduplication (e.g., 'Conception DV, Conception DV')."""
    row = pd.Series({
        'cf_typologie_de_devis': 'Conception DV, Conception DV',
        'title': 'Project'
    })
    tags, primary = allocate_typologie_for_row(row)
    # Conception DV should appear only once
    assert tags.count('Conception DV') == 1
    assert primary == 'Conception DV'


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
