"""
Unit tests for dashboard KPI project filtering logic.

Tests ensure that filtered project lists match the counts displayed in KPI cards.
This protects the "popover list matches the number on the card" contract.
"""

import sys
from pathlib import Path
import pandas as pd
import numpy as np

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.dashboard.app import (
    get_bu_amounts,
    get_production_bu_amounts,
    get_typologie_amounts_for_bu,
    get_production_typologie_amounts_for_bu,
    filter_projects_for_typologie_bu,
    filter_projects_for_typologie_bu_production,
    get_production_year_totals
)


def test_bu_project_list_matches_count():
    """Test that BU project list length equals get_bu_amounts count."""
    df = pd.DataFrame({
        'cf_bu': ['CONCEPTION', 'TRAVAUX', 'MAINTENANCE', 'CONCEPTION', 'TRAVAUX'],
        'amount': [1000, 2000, 3000, 4000, 5000],
        'cf_typologie_de_devis': ['Conception DV', 'Conception Paysage', 'Maintenance Animation', 'Conception DV', 'Conception Paysage'],
        'title': ['Proj1', 'Proj2', 'Proj3', 'Proj4', 'Proj5']
    })

    bu_amounts = get_bu_amounts(df, include_weighted=False)

    # Check each BU
    for bu in ['CONCEPTION', 'TRAVAUX', 'MAINTENANCE']:
        expected_count = bu_amounts.get(bu, {}).get('count', 0)
        bu_projects = df[df['cf_bu'] == bu]
        actual_count = len(bu_projects)

        assert actual_count == expected_count, f"BU {bu}: list length {actual_count} != count {expected_count}"


def test_production_bu_project_list_matches_count():
    """Test that production BU project list length matches get_production_bu_amounts count."""
    production_year = 2026
    total_col = f'Montant Total {production_year}'

    df = pd.DataFrame({
        'cf_bu': ['CONCEPTION', 'TRAVAUX', 'MAINTENANCE', 'CONCEPTION', 'TRAVAUX'],
        total_col: [1000, 2000, 0, 4000, 5000],  # One row with 0 (should be excluded)
        'cf_typologie_de_devis': ['Conception DV', 'Conception Paysage', 'Maintenance Animation', 'Conception DV', 'Conception Paysage'],
        'title': ['Proj1', 'Proj2', 'Proj3', 'Proj4', 'Proj5']
    })

    bu_amounts = get_production_bu_amounts(df, production_year, include_pondere=False)

    # Check each BU
    for bu in ['CONCEPTION', 'TRAVAUX', 'MAINTENANCE']:
        expected_count = bu_amounts.get(bu, {}).get('count', 0)
        bu_projects = df[(df['cf_bu'] == bu) & (df[total_col] > 0)]
        actual_count = len(bu_projects)

        assert actual_count == expected_count, f"Production BU {bu}: list length {actual_count} != count {expected_count}"


def test_typologie_project_list_matches_count():
    """Test that typologie project list matches get_typologie_amounts_for_bu count."""
    df = pd.DataFrame({
        'cf_bu': ['MAINTENANCE', 'MAINTENANCE', 'TRAVAUX', 'TRAVAUX'],
        'amount': [1000, 2000, 3000, 4000],
        'cf_typologie_de_devis': ['Maintenance TS', 'Maintenance Animation', 'Conception Paysage', 'Conception Paysage'],
        'title': ['Proj TS', 'Proj Anim', 'Proj Paysage 1', 'Proj Paysage 2']
    })

    # Test MAINTENANCE typologies
    bu = 'MAINTENANCE'
    type_amounts = get_typologie_amounts_for_bu(df, bu, include_weighted=False)

    for typ in ['Maintenance TS', 'Maintenance Animation']:
        expected_count = type_amounts.get(typ, {}).get('count', 0)
        typ_projects = filter_projects_for_typologie_bu(df, bu, typ)
        actual_count = len(typ_projects)

        assert actual_count == expected_count, f"Typologie {typ} in BU {bu}: list length {actual_count} != count {expected_count}"


def test_ts_special_case_typologie_list():
    """Test TS special case: TS under MAINTENANCE includes all rows where primary is TS."""
    df = pd.DataFrame({
        'cf_bu': ['TRAVAUX', 'MAINTENANCE', 'CONCEPTION'],  # TS can appear under any BU
        'amount': [1000, 2000, 3000],
        'cf_typologie_de_devis': ['Maintenance TS', 'Maintenance TS', 'Conception DV'],
        'title': ['Proj TS 1', 'Proj TS 2', 'Proj DV']
    })

    bu = 'MAINTENANCE'
    typ = 'Maintenance TS'

    type_amounts = get_typologie_amounts_for_bu(df, bu, include_weighted=False)
    expected_count = type_amounts.get(typ, {}).get('count', 0)

    # TS under MAINTENANCE should include ALL rows where primary is TS, regardless of BU
    typ_projects = filter_projects_for_typologie_bu(df, bu, typ)
    actual_count = len(typ_projects)

    assert actual_count == expected_count, f"TS special case: list length {actual_count} != count {expected_count}"
    # Should include both TS rows (from TRAVAUX and MAINTENANCE)
    assert actual_count == 2, f"TS special case should include 2 projects, got {actual_count}"


def test_production_typologie_project_list_matches_count():
    """Test that production typologie project list matches get_production_typologie_amounts_for_bu count."""
    production_year = 2026
    total_col = f'Montant Total {production_year}'

    df = pd.DataFrame({
        'cf_bu': ['MAINTENANCE', 'MAINTENANCE', 'TRAVAUX'],
        total_col: [1000, 2000, 3000],
        'cf_typologie_de_devis': ['Maintenance TS', 'Maintenance Animation', 'Conception Paysage'],
        'title': ['Proj TS', 'Proj Anim', 'Proj Paysage']
    })

    bu = 'MAINTENANCE'
    type_amounts = get_production_typologie_amounts_for_bu(df, production_year, bu, include_pondere=False)

    for typ in ['Maintenance TS', 'Maintenance Animation']:
        expected_count = type_amounts.get(typ, {}).get('count', 0)
        typ_projects = filter_projects_for_typologie_bu_production(df, production_year, bu, typ)
        actual_count = len(typ_projects)

        assert actual_count == expected_count, f"Production typologie {typ} in BU {bu}: list length {actual_count} != count {expected_count}"


def test_production_year_totals_project_list_matches_count():
    """Test that production year totals project list matches get_production_year_totals count."""
    production_year = 2026
    total_col = f'Montant Total {production_year}'

    df = pd.DataFrame({
        total_col: [1000, 2000, 0, 4000, 5000],  # One row with 0 (should be excluded)
        'cf_bu': ['CONCEPTION', 'TRAVAUX', 'MAINTENANCE', 'CONCEPTION', 'TRAVAUX'],
        'title': ['Proj1', 'Proj2', 'Proj3', 'Proj4', 'Proj5']
    })

    totals = get_production_year_totals(df, production_year, include_pondere=False)
    expected_count = totals.get('count', 0)

    # Projects with production in this year
    projects = df[df[total_col] > 0]
    actual_count = len(projects)

    assert actual_count == expected_count, f"Production year totals: list length {actual_count} != count {expected_count}"
    assert actual_count == 4, f"Should have 4 projects with production > 0, got {actual_count}"


def test_typologie_normal_case_bu_match():
    """Test normal typologie case: BU must match and typ in tags."""
    df = pd.DataFrame({
        'cf_bu': ['TRAVAUX', 'TRAVAUX', 'MAINTENANCE'],
        'amount': [1000, 2000, 3000],
        'cf_typologie_de_devis': ['Travaux DV', 'Travaux DV, Maintenance Animation', 'Travaux DV'],
        'title': ['Proj1', 'Proj2', 'Proj3']
    })

    bu = 'TRAVAUX'
    typ = 'Travaux DV'

    type_amounts = get_typologie_amounts_for_bu(df, bu, include_weighted=False)
    expected_count = type_amounts.get(typ, {}).get('count', 0)

    typ_projects = filter_projects_for_typologie_bu(df, bu, typ)
    actual_count = len(typ_projects)

    assert actual_count == expected_count, f"Typologie {typ} in BU {bu}: list length {actual_count} != count {expected_count}"
    # Should include both TRAVAUX rows with Travaux DV (count: typ in tags)
    assert actual_count == 2, f"Should have 2 projects, got {actual_count}"
