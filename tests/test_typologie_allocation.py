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
    assert parse_typologie_list("Paysage") == ["Paysage"]
    assert parse_typologie_list("DV") == ["DV"]
    assert parse_typologie_list("Animation") == ["Animation"]


def test_parse_typologie_list_multi():
    """Test parsing multiple typologie tags."""
    assert parse_typologie_list("Paysage, Animation") == ["Paysage", "Animation"]
    assert parse_typologie_list("DV, Paysage, Animation") == ["DV", "Paysage", "Animation"]
    assert parse_typologie_list("Animation, Paysage") == ["Animation", "Paysage"]


def test_parse_typologie_list_whitespace():
    """Test parsing with various whitespace."""
    assert parse_typologie_list("  paysage ,  ANIMATION ") == ["paysage", "ANIMATION"]
    assert parse_typologie_list("DV,Paysage") == ["DV", "Paysage"]


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
    """Test TS has highest priority for primary selection."""
    assert choose_primary_typologie(["TS"]) == "TS"
    assert choose_primary_typologie(["DV", "TS"]) == "TS"
    assert choose_primary_typologie(["TS", "DV"]) == "TS"
    assert choose_primary_typologie(["Animation", "TS", "DV"]) == "TS"


def test_choose_primary_typologie_animation_demotion():
    """Test Animation demotion when multiple tags."""
    # Animation first, other tag exists
    assert choose_primary_typologie(["Animation", "Paysage"]) == "Paysage"
    assert choose_primary_typologie(["Animation", "DV"]) == "DV"

    # Multiple tags, Animation not first
    assert choose_primary_typologie(["Paysage", "Animation"]) == "Paysage"
    assert choose_primary_typologie(["DV", "Animation", "Paysage"]) == "DV"

    # Only Animation
    assert choose_primary_typologie(["Animation"]) == "Animation"

    # All Animation
    assert choose_primary_typologie(["Animation", "Animation"]) == "Animation"


def test_choose_primary_typologie_single_tag():
    """Test single tag selection."""
    assert choose_primary_typologie(["DV"]) == "DV"
    assert choose_primary_typologie(["Paysage"]) == "Paysage"
    assert choose_primary_typologie(["Entretien"]) == "Entretien"


def test_choose_primary_typologie_empty():
    """Test empty tag list."""
    assert choose_primary_typologie([]) is None


def test_allocate_typologie_for_row_single_tag():
    """Test allocation for single tag."""
    row = pd.Series({
        'cf_typologie_de_devis': 'Paysage',
        'title': 'Project'
    })
    tags, primary = allocate_typologie_for_row(row)
    assert 'Paysage' in tags
    assert primary == 'Paysage'


def test_allocate_typologie_for_row_multi_tag():
    """Test allocation for multiple tags."""
    row = pd.Series({
        'cf_typologie_de_devis': 'Paysage, Animation',
        'title': 'Project'
    })
    tags, primary = allocate_typologie_for_row(row)
    assert 'Paysage' in tags
    assert 'Animation' in tags
    assert primary == 'Paysage'  # Animation demoted


def test_allocate_typologie_for_row_ts_by_title():
    """Test TS detection from title."""
    row = pd.Series({
        'cf_typologie_de_devis': 'Entretien',
        'title': 'Project TS'
    })
    tags, primary = allocate_typologie_for_row(row)
    assert 'TS' in tags
    assert 'Entretien' in tags
    assert primary == 'TS'  # TS has priority


def test_allocate_typologie_for_row_ts_by_tag():
    """Test TS detection from tag."""
    row = pd.Series({
        'cf_typologie_de_devis': 'TS',
        'title': 'Project'
    })
    tags, primary = allocate_typologie_for_row(row)
    assert 'TS' in tags
    assert primary == 'TS'


def test_allocate_typologie_for_row_ts_both():
    """Test TS detection when both tag and title have TS (no double counting)."""
    row = pd.Series({
        'cf_typologie_de_devis': 'TS',
        'title': 'Project TS'
    })
    tags, primary = allocate_typologie_for_row(row)
    # TS should appear only once in tags
    assert tags.count('TS') == 1
    assert primary == 'TS'


def test_allocate_typologie_for_row_animation_first():
    """Test Animation demotion when first in list."""
    row = pd.Series({
        'cf_typologie_de_devis': 'Animation, Paysage',
        'title': 'Project'
    })
    tags, primary = allocate_typologie_for_row(row)
    assert 'Animation' in tags
    assert 'Paysage' in tags
    assert primary == 'Paysage'  # Animation demoted


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
    """Test tag deduplication (e.g., 'DV, DV')."""
    row = pd.Series({
        'cf_typologie_de_devis': 'DV, DV',
        'title': 'Project'
    })
    tags, primary = allocate_typologie_for_row(row)
    # DV should appear only once
    assert tags.count('DV') == 1
    assert primary == 'DV'


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
