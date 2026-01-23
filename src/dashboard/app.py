"""
Streamlit Dashboard for Myrium

BI Dashboard for visualizing commercial tracking data from Google Sheets.
Complete overhaul with BU theming and advanced charts.
"""

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime
import re
import sys
import io
import tempfile
from pathlib import Path
import time
import json
import uuid
import numpy as np

# #region agent log - Debug instrumentation
DEBUG_LOG_PATH = Path(__file__).parent.parent.parent.parent / ".cursor" / "debug.log"
DEBUG_SESSION_ID = str(uuid.uuid4())[:8]
DEBUG_RUN_ID = 0

def debug_log(location: str, message: str, data: dict = None, hypothesis_id: str = ""):
    """Write debug log entry to file."""
    global DEBUG_RUN_ID
    try:
        DEBUG_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": time.time(),
            "time_str": datetime.now().strftime("%H:%M:%S.%f")[:-3],
            "session": DEBUG_SESSION_ID,
            "run_id": DEBUG_RUN_ID,
            "hypothesis": hypothesis_id,
            "location": location,
            "message": message,
            "data": data or {}
        }
        with open(DEBUG_LOG_PATH, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        print(f"Debug log error: {e}")

def increment_run():
    """Increment run ID for new script execution."""
    global DEBUG_RUN_ID
    DEBUG_RUN_ID += 1
    return DEBUG_RUN_ID
# #endregion

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from config.settings import settings, MONTH_MAP
from src.integrations.google_sheets import GoogleSheetsClient
from src.processing.objectives import (
    objective_for_month, objective_for_quarter, objective_for_year,
    get_quarter_for_month, quarter_start_dates, quarter_end_dates, get_all_objectives_for_dimension,
    EXPECTED_BUS, EXPECTED_TYPOLOGIES,
    get_accounting_period_for_month, get_accounting_period_label, get_months_for_accounting_period,
    count_unique_accounting_periods, ACCOUNTING_PERIODS
)
from src.processing.typologie_allocation import allocate_typologie_for_row

# =============================================================================
# CONSTANTS
# =============================================================================

# BU Color Theme - Consistent across all charts
BU_COLORS = {
    'CONCEPTION': '#2d5a3f',   # Green
    'TRAVAUX': '#f4c430',      # Yellow
    'MAINTENANCE': '#7b4b94',  # Purple
    'AUTRE': '#808080'         # Gray
}

# Ordered list for consistent chart ordering
BU_ORDER = ['CONCEPTION', 'TRAVAUX', 'MAINTENANCE', 'AUTRE']

# BU to Typologies mapping (source of truth for new typology structure)
BU_TO_TYPOLOGIES = {
    'CONCEPTION': ['DV', 'Paysage', 'Concours'],
    'TRAVAUX': ['DV(Travaux)', 'Travaux Vincent', 'Travaux conception'],
    'MAINTENANCE': ['TS', 'Entretien', 'Animation'],
    'AUTRE': ['Autre']
}

# Typologie Color Palette - Unique colors distinct from BU colors
# Using warm coral/teal palette to differentiate from BU's green/yellow/purple
TYPOLOGIE_COLORS = {
    'DV': '#e76f51',        # Coral Red
    'Animation': '#2a9d8f', # Ocean Teal
    'Paysage': '#90be6d',   # Light Green (distinct from BU yellow/green)
    'Concours': '#e63946',  # Bright Red
    'DV(Travaux)': '#f77f00', # Orange
    'Travaux Vincent': '#fcbf49', # Golden Yellow
    'Travaux conception': '#fca311', # Amber
    'TS': '#9b59b6',        # Purple (for typology TS, distinct from BU colors)
    'Entretien': '#3498db', # Blue
    'Toiture': '#264653',   # Deep Navy
    'Intérieur': '#f4a261', # Sunset Orange
    'Etude': '#d4a5a5',     # Rose Pink
    'Potager': '#5e60ce',   # Slate Purple
    'Formation': '#48cae4', # Sky Blue
    'Autre': '#808080',     # Gray (same as BU AUTRE)
}

# Default gray for undefined/unknown typologies
TYPOLOGIE_DEFAULT_COLOR = '#808080'

# Ordered list of typologie colors for charts (fallback for unknown types)
TYPOLOGIE_COLOR_LIST = [
    '#e76f51', '#2a9d8f', '#90be6d', '#264653', '#f4a261',
    '#d4a5a5', '#5e60ce', '#48cae4', '#808080', '#00b4d8',
    '#bc6c25', '#a8dadc', '#457b9d', '#e9c46a', '#f8961e'
]

# Month order for charts
MONTH_ORDER = {v: k for k, v in MONTH_MAP.items()}
MONTH_NAMES = ['Janvier', 'Février', 'Mars', 'Avril', 'Mai', 'Juin',
               'Juillet', 'Août', 'Septembre', 'Octobre', 'Novembre', 'Décembre']

# =============================================================================
# PAGE CONFIG & STYLING
# =============================================================================

st.set_page_config(
    page_title="Dashboard Commercial",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS for styling
st.markdown("""
<style>
    /* Main header styling */
    .main-header {
        font-family: 'Playfair Display', Georgia, serif;
        color: #1a472a;
        font-size: 2.5rem;
        font-weight: 700;
        margin-bottom: 0.5rem;
    }

    /* Section headers */
    .section-header {
        font-size: 1.3rem;
        font-weight: 600;
        color: #1a472a;
        border-bottom: 2px solid #1a472a;
        padding-bottom: 0.5rem;
        margin-bottom: 1rem;
    }

    /* Metric cards - Base */
    .metric-card {
        padding: 1rem;
        border-radius: 10px;
        color: white;
        text-align: center;
        box-shadow: 0 2px 8px rgba(0,0,0,0.15);
        margin-bottom: 0.5rem;
    }

    /* Metric card variants by BU */
    .metric-card-conception {
        background: linear-gradient(135deg, #2d5a3f 0%, #3d7a52 100%);
    }
    .metric-card-travaux {
        background: linear-gradient(135deg, #f4c430 0%, #ffd700 100%);
        color: #333;
    }
    .metric-card-maintenance {
        background: linear-gradient(135deg, #7b4b94 0%, #9b6bb4 100%);
    }
    .metric-card-autre {
        background: linear-gradient(135deg, #606060 0%, #808080 100%);
    }
    .metric-card-default {
        background: linear-gradient(135deg, #1a472a 0%, #2d5a3f 100%);
    }

    /* Metric card variants by Typologie */
    .metric-card-dv {
        background: linear-gradient(135deg, #e76f51 0%, #f4845f 100%);
    }
    .metric-card-animation {
        background: linear-gradient(135deg, #2a9d8f 0%, #3ab7a7 100%);
    }
    .metric-card-paysage {
        background: linear-gradient(135deg, #90be6d 0%, #a8d08d 100%);
        color: #333;
    }
    .metric-card-toiture {
        background: linear-gradient(135deg, #264653 0%, #3a6b7c 100%);
    }
    .metric-card-intérieur, .metric-card-interieur {
        background: linear-gradient(135deg, #f4a261 0%, #f6b87c 100%);
        color: #333;
    }
    .metric-card-etude {
        background: linear-gradient(135deg, #d4a5a5 0%, #e0bfbf 100%);
        color: #333;
    }
    .metric-card-potager {
        background: linear-gradient(135deg, #5e60ce 0%, #7879d8 100%);
    }
    .metric-card-formation {
        background: linear-gradient(135deg, #48cae4 0%, #6bd4e9 100%);
        color: #333;
    }
    .metric-card-concours {
        background: linear-gradient(135deg, #e63946 0%, #ef4f5f 100%);
    }
    .metric-card-dv-travaux {
        background: linear-gradient(135deg, #f77f00 0%, #ff9500 100%);
        color: #333;
    }
    .metric-card-travaux-vincent {
        background: linear-gradient(135deg, #fcbf49 0%, #ffd166 100%);
        color: #333;
    }
    .metric-card-travaux-conception {
        background: linear-gradient(135deg, #fca311 0%, #ffb84d 100%);
        color: #333;
    }
    .metric-card-ts {
        background: linear-gradient(135deg, #9b59b6 0%, #b573d9 100%);
    }
    .metric-card-entretien {
        background: linear-gradient(135deg, #3498db 0%, #5dade2 100%);
    }
    .metric-card-typologie-default {
        background: linear-gradient(135deg, #606060 0%, #808080 100%);
    }

    .metric-value {
        font-size: 1.5rem;
        font-weight: 700;
        margin: 0.3rem 0;
    }

    .metric-label {
        font-size: 0.8rem;
        opacity: 0.9;
        text-transform: uppercase;
        letter-spacing: 1px;
    }

    /* Sidebar styling */
    .css-1d391kg {
        background-color: #f5f7f5;
    }

    /* Tab styling */
    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
    }

    .stTabs [data-baseweb="tab"] {
        background-color: #f0f4f0;
        border-radius: 8px 8px 0 0;
        padding: 10px 20px;
    }

    .stTabs [aria-selected="true"] {
        background-color: #1a472a;
        color: white;
    }

    /* Dividers */
    .section-divider {
        border-top: 3px solid #1a472a;
        margin: 2rem 0;
    }

    /* Smaller popover buttons */
    button[kind="secondary"] {
        font-size: 0.7rem !important;
        padding: 0.2rem 0.4rem !important;
        height: auto !important;
        line-height: 1.1 !important;
        min-height: auto !important;
    }

    /* Target popover buttons more specifically */
    div[data-testid="stPopover"] button {
        font-size: 0.7rem !important;
        padding: 0.2rem 0.4rem !important;
        height: auto !important;
        line-height: 1.1 !important;
        min-height: auto !important;
    }

    /* Larger popover panel (the window that opens after clicking the button) */
    /* NOTE: Streamlit renders the popover content in a portal, so it may not be a */
    /* descendant of div[data-testid="stPopover"]. We therefore target BaseWeb's */
    /* popover dialog globally, but only role="dialog" to avoid tooltips/menus. */
    div[data-baseweb="popover"][role="dialog"] {
        width: min(95vw, 1600px) !important;
        max-width: 95vw !important;
        max-height: 90vh !important;
        overflow: auto !important;
    }

    /* Inner wrapper should also expand */
    div[data-baseweb="popover"][role="dialog"] > div {
        width: 100% !important;
        max-width: 100% !important;
        max-height: 90vh !important;
    }

    /* Ensure dataframe inside the dialog uses full width */
    div[data-baseweb="popover"][role="dialog"] [data-testid="stDataFrame"] {
        width: 100% !important;
        max-width: 100% !important;
    }
</style>
""", unsafe_allow_html=True)


# =============================================================================
# CACHED DATA FUNCTIONS
# =============================================================================

@st.cache_resource
def get_sheets_client() -> GoogleSheetsClient:
    """Get cached Google Sheets client."""
    return GoogleSheetsClient()


@st.cache_data(ttl=300)  # Cache for 5 minutes
def load_worksheet_data(sheet_name: str, view_type: Optional[str] = None, year: Optional[int] = None) -> pd.DataFrame:
    """Load data from a specific worksheet with caching."""
    # #region agent log - Track worksheet loading (inside cache = cache miss)
    ws_start = time.time()
    debug_log("load_worksheet_data:CACHE_MISS", f"Loading sheet: {sheet_name}",
              {"sheet": sheet_name, "view_type": view_type, "year": year}, "A")
    # #endregion
    client = get_sheets_client()
    result = client.read_worksheet(sheet_name, view_type=view_type, year=year)
    # #region agent log
    debug_log("load_worksheet_data:DONE", f"Loaded {len(result)} rows in {time.time()-ws_start:.2f}s",
              {"sheet": sheet_name, "rows": len(result), "duration_s": round(time.time()-ws_start, 2)}, "A")
    # #endregion
    return result


@st.cache_data(ttl=300)
def get_available_sheets(year: Optional[int] = None, view_type: Optional[str] = None) -> List[str]:
    """Get list of available worksheets."""
    # #region agent log - Track sheet listing (inside cache = cache miss)
    sheets_start = time.time()
    debug_log("get_available_sheets:CACHE_MISS", f"Listing sheets for {view_type} {year}",
              {"view_type": view_type, "year": year}, "A")
    # #endregion
    client = get_sheets_client()
    result = client.list_worksheets(view_type=view_type, year=year)
    # #region agent log
    debug_log("get_available_sheets:DONE", f"Found {len(result)} sheets in {time.time()-sheets_start:.2f}s",
              {"count": len(result), "sheets": result, "duration_s": round(time.time()-sheets_start, 2)}, "A")
    # #endregion
    return result


def get_sheets_for_year(year: int, sheet_type: str = "Signé") -> List[str]:
    """Get all sheets of a type for a specific year."""
    view_type_map = {
        "Signé": "signe",
        "Envoyé": "envoye",
        "État au": "etat"
    }
    view_type = view_type_map.get(sheet_type, "signe")
    return get_available_sheets(year=year, view_type=view_type)


def load_year_data(year: int, sheet_type: str = "Signé") -> pd.DataFrame:
    """Load and combine all monthly sheets for a year."""
    # #region agent log - Track year data loading
    year_start = time.time()
    debug_log("load_year_data:START", f"Loading {sheet_type} {year}",
              {"year": year, "sheet_type": sheet_type}, "B")
    # #endregion

    view_type_map = {
        "Signé": "signe",
        "Envoyé": "envoye",
        "État au": "etat"
    }
    view_type = view_type_map.get(sheet_type, "signe")
    sheets = get_sheets_for_year(year, sheet_type)

    #if not sheets:
        #st.warning(f"No sheets found for {sheet_type} {year}")
        # #region agent log
        #debug_log("load_year_data:NO_SHEETS", f"No sheets found for {sheet_type} {year}", {}, "B")
        # #endregion
        #return pd.DataFrame()

    # #region agent log
    debug_log("load_year_data:SHEETS_FOUND", f"Found {len(sheets)} sheets",
              {"sheets": sheets, "count": len(sheets)}, "B")
    # #endregion
    print(f"Loading data from {len(sheets)} sheets: {sheets}")

    dfs = []
    for sheet in sheets:
        try:
            df = load_worksheet_data(sheet, view_type=view_type, year=year)
            if not df.empty:
                # Ensure column names are unique (handle duplicates)
                if df.columns.duplicated().any():
                    # Deduplicate column names by appending .1, .2, etc.
                    new_columns = []
                    seen = {}
                    for col in df.columns:
                        if col in seen:
                            seen[col] += 1
                            new_columns.append(f"{col}.{seen[col]}")
                        else:
                            seen[col] = 0
                            new_columns.append(col)
                    df.columns = new_columns
                    print(f"⚠ Sheet '{sheet}' had duplicate columns, renamed them")

                df['source_sheet'] = sheet
                dfs.append(df)
                print(f"✓ Loaded {len(df)} rows from sheet: {sheet}")
            else:
                print(f"⚠ Sheet '{sheet}' is empty, skipping")
        except Exception as e:
            print(f"✗ Error loading sheet '{sheet}': {e}")
            # Continue with other sheets instead of failing completely

    if not dfs:
        #st.warning(f"No data loaded from any sheets for {sheet_type} {year}")
        return pd.DataFrame()

    # Ensure all DataFrames have the same columns before concatenation
    # Get union of all columns
    all_columns = set()
    for df in dfs:
        all_columns.update(df.columns)
    all_columns = sorted(list(all_columns))

    # Align all DataFrames to have the same columns
    aligned_dfs = []
    for df in dfs:
        # Add missing columns with NaN values
        for col in all_columns:
            if col not in df.columns:
                df[col] = None
        # Reorder columns to match all_columns order
        df = df[all_columns]
        aligned_dfs.append(df)

    result = pd.concat(aligned_dfs, ignore_index=True)
    print(f"✓ Combined {len(result)} total rows from {len(dfs)} sheets")
    # #region agent log
    debug_log("load_year_data:DONE", f"Combined {len(result)} rows in {time.time()-year_start:.2f}s",
              {"total_rows": len(result), "sheets_loaded": len(dfs), "duration_s": round(time.time()-year_start, 2)}, "B")
    # #endregion
    return result


# =============================================================================
# DATA PROCESSING HELPERS
# =============================================================================

def parse_numeric_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Convert amount and financial columns to numeric."""
    if df.empty:
        return df

    df = df.copy()  # Avoid SettingWithCopyWarning
    numeric_cols = ['amount', 'probability', 'discount', 'vat']
    financial_cols = [c for c in df.columns if 'Montant' in c]

    for col in numeric_cols + financial_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

    return df


def calculate_weighted_amount(df: pd.DataFrame) -> pd.DataFrame:
    """Calculate weighted (pondéré) amount based on probability."""
    if df.empty:
        return df

    df = df.copy()
    if 'amount' in df.columns and 'probability' in df.columns:
        # Probability is typically 0-100, convert to factor
        prob_factor = df['probability'].fillna(50) / 100
        df['amount_pondere'] = df['amount'] * prob_factor
    else:
        df['amount_pondere'] = df.get('amount', 0)

    return df


def normalize_typologie_for_css(typologie: str) -> str:
    """
    Normalize typologie string for CSS class lookup.

    Converts typologie names to safe CSS class names:
    - Lowercase
    - Remove accents
    - Produce kebab-case (hyphen-separated) to match `.metric-card-<name>` CSS selectors

    Examples:
    - 'DV(Travaux)' -> 'dv-travaux'
    - 'Travaux Vincent' -> 'travaux-vincent'
    - 'TS' -> 'ts'
    """
    if not typologie:
        return 'typologie-default'

    # Lowercase
    normalized = typologie.lower()

    # Remove accents (basic replacements)
    replacements = {
        'é': 'e', 'è': 'e', 'ê': 'e', 'ë': 'e',
        'à': 'a', 'â': 'a', 'ä': 'a',
        'ù': 'u', 'û': 'u', 'ü': 'u',
        'ô': 'o', 'ö': 'o',
        'î': 'i', 'ï': 'i',
        'ç': 'c'
    }
    for old, new in replacements.items():
        normalized = normalized.replace(old, new)

    # Replace separators/special chars with hyphens
    normalized = (
        normalized
        .replace('(', '-')   # DV(Travaux) -> dv-travaux
        .replace(')', '')
        .replace(' ', '-')
        .replace('_', '-')
        .replace('/', '-')
        .replace('&', '-')
    )

    # Collapse duplicate hyphens
    while '--' in normalized:
        normalized = normalized.replace('--', '-')

    # Remove leading/trailing hyphens
    normalized = normalized.strip('-')

    return normalized if normalized else 'typologie-default'


# DEPRECATED: get_reporting_typologie and split_typologies are replaced by
# typologie_allocation module. Keeping for backward compatibility during migration.
# TODO: Remove after full migration
def get_reporting_typologie(row: pd.Series) -> str:
    """DEPRECATED: Use typologie_allocation.allocate_typologie_for_row instead."""
    from src.processing.typologie_allocation import allocate_typologie_for_row
    tags, primary = allocate_typologie_for_row(row)
    return primary or ''


def split_typologies(df: pd.DataFrame) -> pd.DataFrame:
    """
    DEPRECATED: This function is kept for backward compatibility but should not be used.
    Use typologie_allocation.allocate_typologie_for_row instead.

    This function now returns the original DataFrame unchanged, as the new allocation
    logic handles typologies differently (primary tag for amounts, all tags for counts).
    """
    # Return original DataFrame - new logic doesn't expand rows
    return df.copy()


def calculate_ts_total(df: pd.DataFrame) -> float:
    """Calculate total amount for TS projects."""
    if df.empty or 'title' not in df.columns:
        return 0.0

    mask = df['title'].astype(str).str.contains('TS', case=False, na=False)
    return df.loc[mask, 'amount'].sum() if 'amount' in df.columns else 0.0


def get_bu_amounts(df: pd.DataFrame, include_weighted: bool = False) -> Dict[str, Dict[str, float]]:
    """Get total and weighted amounts per BU."""
    if df.empty or 'cf_bu' not in df.columns:
        return {}

    result = {}
    for bu in BU_ORDER:
        bu_df = df[df['cf_bu'] == bu]
        result[bu] = {
            'total': bu_df['amount'].sum() if 'amount' in bu_df.columns else 0,
            'count': len(bu_df)
        }
        if include_weighted and 'amount_pondere' in bu_df.columns:
            result[bu]['pondere'] = bu_df['amount_pondere'].sum()

    return result


def get_typologie_amounts(df: pd.DataFrame, include_weighted: bool = False) -> Dict[str, Dict[str, float]]:
    """
    Get total and weighted amounts per typologie using new allocation logic.

    Uses primary typologie for amounts, all tags for counts.
    """
    if df.empty or 'cf_typologie_de_devis' not in df.columns:
        return {}

    result = {}
    count_dict = {}  # Track counts separately (all tags)
    amount_dict = {}  # Track amounts (primary only)
    pondere_dict = {}  # Track weighted amounts (primary only)

    for _, row in df.iterrows():
        tags, primary = allocate_typologie_for_row(row)

        # Count: increment for all tags
        for tag in tags:
            if tag not in count_dict:
                count_dict[tag] = 0
            count_dict[tag] += 1

        # Amount: add to primary only
        if primary:
            amount = float(row.get('amount', 0) or 0)
            if primary not in amount_dict:
                amount_dict[primary] = 0.0
            amount_dict[primary] += amount

            if include_weighted:
                pondere = float(row.get('amount_pondere', 0) or 0)
                if primary not in pondere_dict:
                    pondere_dict[primary] = 0.0
                pondere_dict[primary] += pondere

    # Combine into result structure
    all_typologies = set(count_dict.keys()) | set(amount_dict.keys())
    for typ in all_typologies:
        result[typ] = {
            'total': amount_dict.get(typ, 0.0),
            'count': count_dict.get(typ, 0)
        }
        if include_weighted:
            result[typ]['pondere'] = pondere_dict.get(typ, 0.0)

    return result


def get_typologie_amounts_for_bu(
    df: pd.DataFrame,
    bu: str,
    include_weighted: bool = False
) -> Dict[str, Dict[str, float]]:
    """
    Get typologie amounts for a specific BU, with zero-filled output for all mapped typologies.

    Special handling:
    - TS(typologie): Shows amounts from ALL rows where primary typologie is 'TS',
      regardless of BU (so it appears under MAINTENANCE even if BU=TRAVAUX due to title rule)
    - Other typologies: Only count rows where both BU matches AND typologie is in tags/primary

    Args:
        df: DataFrame with cf_bu and cf_typologie_de_devis columns
        bu: Business Unit name (CONCEPTION, TRAVAUX, MAINTENANCE, AUTRE)
        include_weighted: Whether to include weighted amounts

    Returns:
        Dictionary mapping typologie names to {total, count, pondere?} dicts.
        Always includes all typologies from BU_TO_TYPOLOGIES[bu], with 0 values if absent.
    """
    # Get typologies mapped to this BU
    typologies = BU_TO_TYPOLOGIES.get(bu, [])

    # Initialize zero-filled result
    result = {typ: {'total': 0.0, 'count': 0, 'pondere': 0.0} if include_weighted else {'total': 0.0, 'count': 0}
              for typ in typologies}

    if df.empty or 'cf_bu' not in df.columns or 'cf_typologie_de_devis' not in df.columns:
        return result

    for _, row in df.iterrows():
        tags, primary = allocate_typologie_for_row(row)
        row_bu = str(row.get('cf_bu', '')).strip()

        for typ in typologies:
            # Special case: TS under MAINTENANCE - count from ALL rows where primary is TS
            if typ == 'TS' and bu == 'MAINTENANCE':
                if primary == 'TS':
                    # Count: TS in tags
                    if 'TS' in tags:
                        result[typ]['count'] += 1
                    # Amount: primary is TS
                    amount = float(row.get('amount', 0) or 0)
                    result[typ]['total'] += amount
                    if include_weighted:
                        pondere = float(row.get('amount_pondere', 0) or 0)
                        result[typ]['pondere'] += pondere
            else:
                # Normal case: BU must match
                if row_bu != bu:
                    continue

                # Count: typ in tags
                if typ in tags:
                    result[typ]['count'] += 1

                # Amount: primary matches typ
                if primary == typ:
                    amount = float(row.get('amount', 0) or 0)
                    result[typ]['total'] += amount
                    if include_weighted:
                        pondere = float(row.get('amount_pondere', 0) or 0)
                        result[typ]['pondere'] += pondere

    return result


def filter_projects_for_typologie_bu(
    df: pd.DataFrame,
    bu: str,
    typ: str
) -> pd.DataFrame:
    """
    Filter projects that belong to a specific typologie within a BU.

    Matches the counting logic from get_typologie_amounts_for_bu:
    - TS special case: if typ=='TS' and bu=='MAINTENANCE', include all rows where primary=='TS'
    - Otherwise: include rows where row_bu==bu and typ in tags

    Args:
        df: DataFrame with cf_bu and cf_typologie_de_devis columns
        bu: Business Unit name
        typ: Typologie name

    Returns:
        Filtered DataFrame with matching projects
    """
    if df.empty or 'cf_bu' not in df.columns or 'cf_typologie_de_devis' not in df.columns:
        return pd.DataFrame()

    matching_indices = []

    for idx, row in df.iterrows():
        tags, primary = allocate_typologie_for_row(row)
        row_bu = str(row.get('cf_bu', '')).strip()

        # Special case: TS under MAINTENANCE
        if typ == 'TS' and bu == 'MAINTENANCE':
            if primary == 'TS' and 'TS' in tags:
                matching_indices.append(idx)
        else:
            # Normal case: BU must match and typ in tags
            if row_bu == bu and typ in tags:
                matching_indices.append(idx)

    if not matching_indices:
        return pd.DataFrame()

    return df.loc[matching_indices].copy()


def filter_projects_for_typologie_bu_production(
    df: pd.DataFrame,
    production_year: int,
    bu: str,
    typ: str
) -> pd.DataFrame:
    """
    Filter projects for a specific typologie within a BU for production year.

    Matches the counting logic from get_production_typologie_amounts_for_bu.

    Args:
        df: DataFrame with cf_bu, cf_typologie_de_devis and production year columns
        production_year: Target production year
        bu: Business Unit name
        typ: Typologie name

    Returns:
        Filtered DataFrame with matching projects
    """
    if df.empty or 'cf_typologie_de_devis' not in df.columns:
        return pd.DataFrame()

    total_col = f"Montant Total {production_year}"
    if total_col not in df.columns:
        return pd.DataFrame()

    has_bu = 'cf_bu' in df.columns
    matching_indices = []

    for idx, row in df.iterrows():
        tags, primary = allocate_typologie_for_row(row)
        row_bu = str(row.get('cf_bu', 'AUTRE')).strip() if has_bu else 'AUTRE'

        # Check if this row has production in this year
        total_amount = float(row.get(total_col, 0) or 0)
        if total_amount <= 0:
            continue

        # Special case: TS under MAINTENANCE
        if typ == 'TS' and bu == 'MAINTENANCE':
            if primary == 'TS' and 'TS' in tags:
                matching_indices.append(idx)
        else:
            # Normal case: BU must match and typ in tags
            if row_bu == bu and typ in tags:
                matching_indices.append(idx)

    if not matching_indices:
        return pd.DataFrame()

    return df.loc[matching_indices].copy()


def get_ts_typologie_total(df: pd.DataFrame, include_weighted: bool = False) -> Dict[str, float]:
    """
    Calculate TS(typologie) total - based on primary typologie == 'TS' (from tag or title).

    Args:
        df: DataFrame with cf_typologie_de_devis column
        include_weighted: Whether to include weighted amount

    Returns:
        Dictionary with 'total', 'count', and optionally 'pondere'
    """
    if df.empty or 'cf_typologie_de_devis' not in df.columns:
        return {'total': 0.0, 'count': 0, 'pondere': 0.0} if include_weighted else {'total': 0.0, 'count': 0}

    total = 0.0
    count = 0
    pondere = 0.0

    for _, row in df.iterrows():
        tags, primary = allocate_typologie_for_row(row)

        if primary == 'TS':
            total += float(row.get('amount', 0) or 0)
            if include_weighted:
                pondere += float(row.get('amount_pondere', 0) or 0)

        if 'TS' in tags:
            count += 1

    result = {
        'total': total,
        'count': count
    }

    if include_weighted:
        result['pondere'] = pondere

    return result


def extract_month_from_sheet(sheet_name: str) -> Tuple[Optional[str], Optional[int]]:
    """Extract month name and number from sheet name."""
    for month_name, month_num in MONTH_ORDER.items():
        if month_name in sheet_name:
            return month_name, month_num
    return None, None


def get_monthly_data(df: pd.DataFrame, include_weighted: bool = False) -> pd.DataFrame:
    """Aggregate data by month and BU."""
    if df.empty or 'source_sheet' not in df.columns:
        return pd.DataFrame()

    monthly_records = []

    for sheet in df['source_sheet'].unique():
        month_name, month_num = extract_month_from_sheet(sheet)
        if not month_name:
            continue

        sheet_df = df[df['source_sheet'] == sheet]

        # Aggregate by BU
        for bu in BU_ORDER:
            bu_df = sheet_df[sheet_df['cf_bu'] == bu] if 'cf_bu' in sheet_df.columns else pd.DataFrame()
            record = {
                'month_num': month_num,
                'month': month_name,
                'bu': bu,
                'amount': bu_df['amount'].sum() if not bu_df.empty else 0
            }
            if include_weighted and 'amount_pondere' in sheet_df.columns:
                record['amount_pondere'] = bu_df['amount_pondere'].sum() if not bu_df.empty else 0
            monthly_records.append(record)

    if not monthly_records:
        return pd.DataFrame()

    return pd.DataFrame(monthly_records).sort_values(['month_num', 'bu'])


def get_monthly_data_by_typologie(df: pd.DataFrame, top_n: int = 6, include_weighted: bool = False) -> Tuple[pd.DataFrame, List[str]]:
    """
    Aggregate data by month and Typologie (top N typologies).

    Returns:
        Tuple of (DataFrame with monthly data, list of top typologies)
    """
    if df.empty or 'source_sheet' not in df.columns or 'cf_typologie_de_devis' not in df.columns:
        return pd.DataFrame(), []

    # First, determine top N typologies by total amount (using primary allocation)
    type_totals = {}
    for _, row in df.iterrows():
        tags, primary = allocate_typologie_for_row(row)
        if primary:
            amount = float(row.get('amount', 0) or 0)
            if primary not in type_totals:
                type_totals[primary] = 0.0
            type_totals[primary] += amount

    sorted_types = sorted(type_totals.items(), key=lambda x: x[1], reverse=True)
    top_typologies = [typ for typ, _ in sorted_types[:top_n]]

    monthly_records = []

    for sheet in df['source_sheet'].unique():
        month_name, month_num = extract_month_from_sheet(sheet)
        if not month_name:
            continue

        sheet_df = df[df['source_sheet'] == sheet]

        # Aggregate by top typologies (using primary allocation)
        for typ in top_typologies:
            amount_sum = 0.0
            pondere_sum = 0.0

            for _, row in sheet_df.iterrows():
                tags, primary = allocate_typologie_for_row(row)
                if primary == typ:
                    amount_sum += float(row.get('amount', 0) or 0)
                    if include_weighted:
                        pondere_sum += float(row.get('amount_pondere', 0) or 0)

            record = {
                'month_num': month_num,
                'month': month_name,
                'typologie': typ,
                'amount': amount_sum
            }
            if include_weighted:
                record['amount_pondere'] = pondere_sum
            monthly_records.append(record)

    if not monthly_records:
        return pd.DataFrame(), []

    return pd.DataFrame(monthly_records).sort_values(['month_num', 'typologie']), top_typologies


def get_quarterly_totals(df: pd.DataFrame, year: int, include_pondere: bool = False) -> Dict[str, Dict[str, float]]:
    """
    Extract quarterly totals from DataFrame columns.

    Args:
        df: DataFrame with quarterly columns
        year: Year to extract quarters for
        include_pondere: Whether to include weighted amounts

    Returns:
        Dictionary with structure: {'Q1': {'total': float, 'pondere': float}, ...}
    """
    if df.empty:
        return {}

    result = {}
    quarters = ['Q1', 'Q2', 'Q3', 'Q4']

    for quarter in quarters:
        total_col = f'Montant Total {quarter}_{year}'
        pondere_col = f'Montant Pondéré {quarter}_{year}'

        quarter_data = {'total': 0.0}

        if total_col in df.columns:
            quarter_data['total'] = df[total_col].sum()

        if include_pondere and pondere_col in df.columns:
            quarter_data['pondere'] = df[pondere_col].sum()

        result[quarter] = quarter_data

    return result


def get_quarterly_by_bu(df: pd.DataFrame, year: int, include_pondere: bool = False) -> Dict[str, Dict[str, Dict[str, float]]]:
    """
    Extract quarterly amounts grouped by Business Unit.

    Args:
        df: DataFrame with quarterly columns and cf_bu column
        year: Year to extract quarters for
        include_pondere: Whether to include weighted amounts

    Returns:
        Nested dictionary: {'CONCEPTION': {'Q1': {'total': float, 'pondere': float}, ...}, ...}
    """
    if df.empty or 'cf_bu' not in df.columns:
        return {}

    result = {}
    quarters = ['Q1', 'Q2', 'Q3', 'Q4']

    # Get all unique BUs from data (default missing to AUTRE)
    data_bus = set(df['cf_bu'].fillna('AUTRE').unique())

    # Process all BUs in BU_ORDER first
    for bu in BU_ORDER:
        bu_df = df[df['cf_bu'].fillna('AUTRE') == bu]
        if bu_df.empty:
            continue

        bu_data = {}
        for quarter in quarters:
            total_col = f'Montant Total {quarter}_{year}'
            pondere_col = f'Montant Pondéré {quarter}_{year}'

            quarter_data = {'total': 0.0}

            if total_col in bu_df.columns:
                quarter_data['total'] = bu_df[total_col].sum()

            if include_pondere and pondere_col in bu_df.columns:
                quarter_data['pondere'] = bu_df[pondere_col].sum()

            bu_data[quarter] = quarter_data

        if any(bu_data[q]['total'] > 0 for q in quarters):
            result[bu] = bu_data

    # Also include any BUs not in BU_ORDER
    other_bus = data_bus - set(BU_ORDER)
    for bu in other_bus:
        if bu and str(bu) != 'nan':
            bu_df = df[df['cf_bu'].fillna('AUTRE') == bu]
            if not bu_df.empty:
                bu_data = {}
                for quarter in quarters:
                    total_col = f'Montant Total {quarter}_{year}'
                    pondere_col = f'Montant Pondéré {quarter}_{year}'

                    quarter_data = {'total': 0.0}

                    if total_col in bu_df.columns:
                        quarter_data['total'] = bu_df[total_col].sum()

                    if include_pondere and pondere_col in bu_df.columns:
                        quarter_data['pondere'] = bu_df[pondere_col].sum()

                    bu_data[quarter] = quarter_data

                if any(bu_data[q]['total'] > 0 for q in quarters):
                    result[bu] = bu_data

    return result


# =============================================================================
# OBJECTIVES HELPER FUNCTIONS
# =============================================================================

def calculate_realized_by_month(
    df: pd.DataFrame,
    dimension: str,
    key: str,
    month_num: int
) -> float:
    """
    Calculate realized amount for a specific month, dimension, and key.

    Args:
        df: DataFrame with source_sheet column
        dimension: "bu" or "typologie"
        key: BU name or typologie name
        month_num: Month number (1-12)

    Returns:
        Sum of amounts for that month/dimension/key
    """
    if df.empty or 'source_sheet' not in df.columns:
        return 0.0

    # Get month name from MONTH_MAP
    month_name = MONTH_MAP.get(month_num, "")
    if not month_name:
        return 0.0

    # Filter by month using extract_month_from_sheet logic
    month_df = pd.DataFrame()
    for sheet in df['source_sheet'].unique():
        sheet_month_name, sheet_month_num = extract_month_from_sheet(sheet)
        if sheet_month_num == month_num:
            month_df = pd.concat([month_df, df[df['source_sheet'] == sheet]], ignore_index=True)

    if month_df.empty:
        return 0.0

    # Use pondéré if available (for Envoyé), otherwise use amount (for Signé)
    amount_col = 'amount_pondere' if 'amount_pondere' in month_df.columns else 'amount'

    # Filter by dimension
    if dimension == "bu":
        if 'cf_bu' not in month_df.columns:
            return 0.0
        filtered = month_df[month_df['cf_bu'] == key]
        return filtered[amount_col].sum() if not filtered.empty else 0.0
    elif dimension == "typologie":
        if 'cf_typologie_de_devis' not in month_df.columns:
            return 0.0
        # Use new allocation logic: amount goes to primary only
        total = 0.0
        for _, row in month_df.iterrows():
            tags, primary = allocate_typologie_for_row(row)
            if primary == key:
                total += float(row.get(amount_col, 0) or 0)
        return total
    else:
        return 0.0


def calculate_realized_for_quarter(
    df: pd.DataFrame,
    dimension: str,
    key: str,
    quarter: str
) -> float:
    """
    Calculate realized amount for a quarter (sum of 3 months).

    Args:
        df: DataFrame with source_sheet column
        dimension: "bu" or "typologie"
        key: BU name or typologie name
        quarter: "Q1", "Q2", "Q3", or "Q4"

    Returns:
        Sum of realized amounts for the 3 months in that quarter
    """
    quarter_months = {
        "Q1": [1, 2, 3],
        "Q2": [4, 5, 6],
        "Q3": [7, 8, 9],
        "Q4": [10, 11, 12]
    }

    if quarter not in quarter_months:
        return 0.0

    total = 0.0
    for month_num in quarter_months[quarter]:
        total += calculate_realized_by_month(df, dimension, key, month_num)

    return total


def calculate_realized_for_year(
    df: pd.DataFrame,
    dimension: str,
    key: str
) -> float:
    """
    Calculate realized amount for a full year.

    Args:
        df: DataFrame with source_sheet column
        dimension: "bu" or "typologie"
        key: BU name or typologie name

    Returns:
        Sum of realized amounts for all available months
    """
    total = 0.0
    for month_num in range(1, 13):
        total += calculate_realized_by_month(df, dimension, key, month_num)

    return total


# =============================================================================
# PRODUCTION-YEAR OBJECTIVES HELPER FUNCTIONS
# =============================================================================

def calculate_realized_by_production_year(
    df: pd.DataFrame,
    production_year: int,
    dimension: str,
    key: str,
    use_pondere: bool = False
) -> float:
    """
    Calculate realized amount for a production year from production columns.

    Uses 'Montant Total {production_year}' or 'Montant Pondéré {production_year}' columns.

    Args:
        df: DataFrame with production-year financial columns
        production_year: Target production year (e.g., 2026)
        dimension: "bu" or "typologie"
        key: BU name or typologie name
        use_pondere: Whether to use weighted amounts (for Envoyé)

    Returns:
        Sum of production-year amounts for that dimension/key
    """
    if df.empty:
        return 0.0

    amount_col = f'Montant Pondéré {production_year}' if use_pondere else f'Montant Total {production_year}'

    if amount_col not in df.columns:
        return 0.0

    # Filter by dimension
    if dimension == "bu":
        if 'cf_bu' not in df.columns:
            return 0.0
        filtered = df[df['cf_bu'] == key]
        return filtered[amount_col].sum() if not filtered.empty else 0.0
    elif dimension == "typologie":
        if 'cf_typologie_de_devis' not in df.columns:
            return 0.0
        # Use new allocation logic: amount goes to primary only
        total = 0.0
        for _, row in df.iterrows():
            tags, primary = allocate_typologie_for_row(row)
            if primary == key:
                total += float(row.get(amount_col, 0) or 0)
        return total
    else:
        return 0.0


def calculate_realized_by_production_quarter(
    df: pd.DataFrame,
    production_year: int,
    quarter: str,
    dimension: str,
    key: str,
    use_pondere: bool = False
) -> float:
    """
    Calculate realized amount for a production quarter from production columns.

    Uses 'Montant Total Q{1-4}_{production_year}' or 'Montant Pondéré Q{1-4}_{production_year}' columns.

    Args:
        df: DataFrame with production-year financial columns
        production_year: Target production year (e.g., 2026)
        quarter: "Q1", "Q2", "Q3", or "Q4"
        dimension: "bu" or "typologie"
        key: BU name or typologie name
        use_pondere: Whether to use weighted amounts (for Envoyé)

    Returns:
        Sum of production-quarter amounts for that dimension/key
    """
    if df.empty:
        return 0.0

    amount_col = f'Montant Pondéré {quarter}_{production_year}' if use_pondere else f'Montant Total {quarter}_{production_year}'

    if amount_col not in df.columns:
        return 0.0

    # Filter by dimension (same logic as production year)
    if dimension == "bu":
        if 'cf_bu' not in df.columns:
            return 0.0
        filtered = df[df['cf_bu'] == key]
        return filtered[amount_col].sum() if not filtered.empty else 0.0
    elif dimension == "typologie":
        if 'cf_typologie_de_devis' not in df.columns:
            return 0.0
        # Handle split typologies
        total = 0.0
        for _, row in df.iterrows():
            typo_str = str(row.get('cf_typologie_de_devis', ''))
            if not typo_str or typo_str.lower() == 'nan':
                continue
            typologies = [t.strip() for t in typo_str.replace(',', ' ').split()]
            if key in typologies:
                num_typos = len(typologies)
                if num_typos > 0:
                    total += (row[amount_col] or 0) / num_typos
        return total
    else:
        return 0.0


def calculate_realized_by_signature_period(
    df: pd.DataFrame,
    production_year: int,
    period_idx: int,
    dimension: str,
    key: str,
    use_pondere: bool = False
) -> float:
    """
    Calculate production-year contribution from deals signed in a specific accounting period.

    Filters by signing period (using source_sheet month mapping) and sums production-year amounts.

    Args:
        df: DataFrame with production-year columns and source_sheet
        production_year: Target production year
        period_idx: Accounting period index (0-10, where 6 = Juil+Août)
        dimension: "bu" or "typologie"
        key: BU name or typologie name
        use_pondere: Whether to use weighted amounts

    Returns:
        Sum of production-year amounts for deals signed in that period
    """
    if df.empty or 'source_sheet' not in df.columns:
        return 0.0

    # Get month numbers for this accounting period
    period_months = get_months_for_accounting_period(period_idx)

    # Filter by signing period
    period_df = pd.DataFrame()
    for sheet in df['source_sheet'].unique():
        _, month_num = extract_month_from_sheet(sheet)
        if month_num and month_num in period_months:
            period_df = pd.concat([period_df, df[df['source_sheet'] == sheet]], ignore_index=True)

    if period_df.empty:
        return 0.0

    # Now sum production-year amounts from this filtered set
    return calculate_realized_by_production_year(period_df, production_year, dimension, key, use_pondere)


def _format_realized_with_carryover(realized_total: float, realized_prev_years: float) -> str:
    """
    Format realized amount with carryover info in parentheses.
    """
    realized_total = float(realized_total or 0.0)
    realized_prev_years = float(realized_prev_years or 0.0)
    return f"{realized_total:,.0f}€ (dont {realized_prev_years:,.0f}€ années précéd.)"


def _filter_df_for_dimension(df: pd.DataFrame, dimension: str, key: str) -> pd.DataFrame:
    """
    Filter a dataframe for BU dimension. Typologie dimension is handled row-wise (split),
    so for typologie we return the full df and apply split logic when summing.
    """
    if df.empty:
        return df
    if dimension == "bu":
        if "cf_bu" not in df.columns:
            return df.iloc[0:0]
        return df[df["cf_bu"] == key]
    return df


def _sum_split_typologie_column(df: pd.DataFrame, amount_col: str, typologie_key: str) -> float:
    """
    Sum a numeric column for a given typologie using new allocation logic.

    Amount goes to primary typologie only (no splitting).
    """
    if df.empty or amount_col not in df.columns or "cf_typologie_de_devis" not in df.columns:
        return 0.0

    total = 0.0
    for _, row in df.iterrows():
        tags, primary = allocate_typologie_for_row(row)
        if primary == typologie_key:
            total += float(row.get(amount_col, 0) or 0)
    return total


def calculate_production_month_with_carryover(
    df: pd.DataFrame,
    production_year: int,
    month_num: int,
    dimension: str,
    key: str,
    use_pondere: bool = False,
) -> tuple[float, float]:
    """
    Calculate production-month realized amount with carryover split.

    For a given production month, uses the quarter column divided by 3 to ensure
    Jan + Feb + Mar = Q1, etc.

    Args:
        df: DataFrame with production-year financial columns
        production_year: Target production year (e.g., 2026)
        month_num: Production month number (1-12)
        dimension: "bu" or "typologie"
        key: BU name or typologie name
        use_pondere: Whether to use weighted amounts (for Envoyé)

    Returns:
        Tuple of (total, prev_years_part) where:
        - total: Production amount for this month (all signed_years)
        - prev_years_part: Production amount from previous-year signings
    """
    if df.empty:
        return 0.0, 0.0

    quarter = get_quarter_for_month(month_num)
    quarter_col = (
        f"Montant Pondéré {quarter}_{production_year}"
        if use_pondere
        else f"Montant Total {quarter}_{production_year}"
    )

    if quarter_col not in df.columns:
        return 0.0, 0.0

    has_signed_year = "signed_year" in df.columns
    prev_df = df[df["signed_year"] < production_year] if has_signed_year else df.iloc[0:0]

    # Compute total (all signed_years) - divide quarter by 3 for monthly amount
    if dimension == "bu":
        work = _filter_df_for_dimension(df, dimension, key)
        total = float(work[quarter_col].sum() or 0.0) / 3.0
        prev_work = _filter_df_for_dimension(prev_df, dimension, key)
        prev_total = float(prev_work[quarter_col].sum() or 0.0) / 3.0
    else:
        # typologie
        total = _sum_split_typologie_column(df, quarter_col, key) / 3.0
        prev_total = _sum_split_typologie_column(prev_df, quarter_col, key) / 3.0

    return float(total), float(prev_total)


def calculate_production_period_with_carryover(
    df: pd.DataFrame,
    production_year: int,
    period_idx: int,
    dimension: str,
    key: str,
    use_pondere: bool = False,
) -> tuple[float, float]:
    """
    Calculate production-period realized amount with carryover split.

    Sums production-month amounts for all months in the accounting period.
    For Juil+Août (period 6), this naturally becomes 2/3 of Q3.

    Args:
        df: DataFrame with production-year financial columns
        production_year: Target production year (e.g., 2026)
        period_idx: Accounting period index (0-10)
        dimension: "bu" or "typologie"
        key: BU name or typologie name
        use_pondere: Whether to use weighted amounts (for Envoyé)

    Returns:
        Tuple of (total, prev_years_part) where:
        - total: Production amount for this accounting period (all signed_years)
        - prev_years_part: Production amount from previous-year signings
    """
    if df.empty:
        return 0.0, 0.0

    period_months = get_months_for_accounting_period(period_idx)
    if not period_months:
        return 0.0, 0.0

    total = 0.0
    prev_total = 0.0

    for month_num in period_months:
        month_total, month_prev = calculate_production_month_with_carryover(
            df, production_year, month_num, dimension, key, use_pondere
        )
        total += month_total
        prev_total += month_prev

    return float(total), float(prev_total)


def calculate_production_amount_with_carryover(
    df: pd.DataFrame,
    production_year: int,
    amount_col: str,
    dimension: str,
    key: str,
) -> tuple[float, float]:
    """
    Sum a production amount column for a dimension/key, returning:
    - total (all signed_years)
    - prev_years (signed_year < production_year)
    """
    if df.empty or amount_col not in df.columns:
        return 0.0, 0.0

    has_signed_year = "signed_year" in df.columns
    prev_df = df[df["signed_year"] < production_year] if has_signed_year else df.iloc[0:0]

    if dimension == "bu":
        work = _filter_df_for_dimension(df, dimension, key)
        prev_work = _filter_df_for_dimension(prev_df, dimension, key)
        return float(work[amount_col].sum() or 0.0), float(prev_work[amount_col].sum() or 0.0)

    # typologie
    total = _sum_split_typologie_column(df, amount_col, key)
    prev_total = _sum_split_typologie_column(prev_df, amount_col, key)
    return float(total), float(prev_total)


# =============================================================================
# PURE SIGNATURE CALCULATION HELPERS (signing-time based, no production split)
# =============================================================================

def calculate_pure_signature_for_month(
    df: pd.DataFrame,
    signed_year: int,
    month_num: int,
    dimension: str,
    key: str,
    use_pondere: bool = False,
) -> tuple[float, float]:
    """
    Calculate pure signature amount for a signing month (raw amount, no production split).

    Args:
        df: DataFrame with source_sheet and amount columns
        signed_year: Year of signatures to include (filters signed_year == signed_year)
        month_num: Signing month number (1-12)
        dimension: "bu" or "typologie"
        key: BU name or typologie name
        use_pondere: If True, return (brut, pondere); if False, return (brut, 0.0)

    Returns:
        Tuple of (brut, pondere) where:
        - brut: Raw amount signed in that month
        - pondere: Weighted amount (only if use_pondere=True and column exists)
    """
    if df.empty or "source_sheet" not in df.columns:
        return 0.0, 0.0

    # Filter to signed_year == signed_year
    has_signed_year = "signed_year" in df.columns
    if has_signed_year:
        df = df[df["signed_year"] == signed_year]
        if df.empty:
            return 0.0, 0.0

    # Filter by signing month using source_sheet
    month_df = pd.DataFrame()
    for sheet in df["source_sheet"].unique():
        _, sheet_month_num = extract_month_from_sheet(sheet)
        if sheet_month_num == month_num:
            month_df = pd.concat([month_df, df[df["source_sheet"] == sheet]], ignore_index=True)

    if month_df.empty:
        return 0.0, 0.0

    # Compute brut (raw amount)
    if dimension == "bu":
        month_df = _filter_df_for_dimension(month_df, dimension, key)
        brut = float(month_df["amount"].sum() or 0.0) if "amount" in month_df.columns else 0.0
    else:
        # typologie - use primary allocation
        brut = 0.0
        for _, row in month_df.iterrows():
            tags, primary = allocate_typologie_for_row(row)
            if primary == key:
                brut += float(row.get("amount", 0) or 0)

    # Compute pondere if requested
    pondere = 0.0
    if use_pondere:
        if "amount_pondere" in month_df.columns:
            if dimension == "bu":
                pondere = float(month_df["amount_pondere"].sum() or 0.0)
            else:
                for _, row in month_df.iterrows():
                    tags, primary = allocate_typologie_for_row(row)
                    if primary == key:
                        pondere += float(row.get("amount_pondere", 0) or 0)
        elif "probability" in month_df.columns and "amount" in month_df.columns:
            # Compute pondere from probability if column missing
            if dimension == "bu":
                for _, row in month_df.iterrows():
                    prob = float(row.get("probability", 50) or 50) / 100.0
                    pondere += float(row.get("amount", 0) or 0) * prob
            else:
                for _, row in month_df.iterrows():
                    tags, primary = allocate_typologie_for_row(row)
                    if primary == key:
                        prob = float(row.get("probability", 50) or 50) / 100.0
                        pondere += float(row.get("amount", 0) or 0) * prob

    return float(brut), float(pondere)


def calculate_pure_signature_for_quarter(
    df: pd.DataFrame,
    signed_year: int,
    quarter: str,
    dimension: str,
    key: str,
    use_pondere: bool = False,
) -> tuple[float, float]:
    """
    Calculate pure signature amount for a signing quarter (raw amount, no production split).

    Args:
        df: DataFrame with source_sheet and amount columns
        signed_year: Year of signatures to include
        quarter: "Q1", "Q2", "Q3", or "Q4"
        dimension: "bu" or "typologie"
        key: BU name or typologie name
        use_pondere: If True, return (brut, pondere); if False, return (brut, 0.0)

    Returns:
        Tuple of (brut, pondere)
    """
    quarter_months = {
        "Q1": [1, 2, 3],
        "Q2": [4, 5, 6],
        "Q3": [7, 8, 9],
        "Q4": [10, 11, 12]
    }

    if quarter not in quarter_months:
        return 0.0, 0.0

    brut_total = 0.0
    pondere_total = 0.0

    for month_num in quarter_months[quarter]:
        brut, pondere = calculate_pure_signature_for_month(
            df, signed_year, month_num, dimension, key, use_pondere
        )
        brut_total += brut
        pondere_total += pondere

    return float(brut_total), float(pondere_total)


def calculate_pure_signature_for_year(
    df: pd.DataFrame,
    signed_year: int,
    dimension: str,
    key: str,
    use_pondere: bool = False,
) -> tuple[float, float]:
    """
    Calculate pure signature amount for a signing year (raw amount, no production split).

    Args:
        df: DataFrame with source_sheet and amount columns
        signed_year: Year of signatures to include
        dimension: "bu" or "typologie"
        key: BU name or typologie name
        use_pondere: If True, return (brut, pondere); if False, return (brut, 0.0)

    Returns:
        Tuple of (brut, pondere)
    """
    if df.empty:
        return 0.0, 0.0

    # Filter to signed_year == signed_year
    has_signed_year = "signed_year" in df.columns
    if has_signed_year:
        df = df[df["signed_year"] == signed_year]
        if df.empty:
            return 0.0, 0.0

    # Compute brut (raw amount) - all rows in this year
    if dimension == "bu":
        df = _filter_df_for_dimension(df, dimension, key)
        brut = float(df["amount"].sum() or 0.0) if "amount" in df.columns else 0.0
    else:
        # typologie - use primary allocation
        brut = 0.0
        for _, row in df.iterrows():
            tags, primary = allocate_typologie_for_row(row)
            if primary == key:
                brut += float(row.get("amount", 0) or 0)

    # Compute pondere if requested
    pondere = 0.0
    if use_pondere:
        if "amount_pondere" in df.columns:
            if dimension == "bu":
                pondere = float(df["amount_pondere"].sum() or 0.0)
            else:
                for _, row in df.iterrows():
                    tags, primary = allocate_typologie_for_row(row)
                    if primary == key:
                        pondere += float(row.get("amount_pondere", 0) or 0)
        elif "probability" in df.columns and "amount" in df.columns:
            # Compute pondere from probability if column missing
            if dimension == "bu":
                for _, row in df.iterrows():
                    prob = float(row.get("probability", 50) or 50) / 100.0
                    pondere += float(row.get("amount", 0) or 0) * prob
            else:
                for _, row in df.iterrows():
                    tags, primary = allocate_typologie_for_row(row)
                    if primary == key:
                        prob = float(row.get("probability", 50) or 50) / 100.0
                        pondere += float(row.get("amount", 0) or 0) * prob

    return float(brut), float(pondere)


def plot_objectives_line_chart(
    year: int,
    metric: str,
    dimension: str,
    key: str,
    df: pd.DataFrame,
    use_pondere: bool = False,
    show_pure: bool = False
) -> go.Figure:
    """
    Create a line chart comparing realized vs objectives by month.
    Uses production-month realized series (Jan-Dec only) including carryover.
    If key is "all", show cumulative total vs total objective and individual lines.

    Args:
        year: Production year
        metric: "envoye" or "signe"
        dimension: "bu" or "typologie"
        key: BU name or typologie name (or "all")
        df: DataFrame with production-year columns
        use_pondere: Whether to use weighted amounts (for Envoyé)
        show_pure: Whether to show pure signature lines
    """
    months = MONTH_NAMES
    fig = go.Figure()

    if key == "all":
        # All view: show cumulative realized vs cumulative objective, and individual lines
        realized_total = [0.0] * 12
        objective_total = [0.0] * 12
        pure_brut_total = [0.0] * 12
        pure_pondere_total = [0.0] * 12

        items = BU_ORDER if dimension == "bu" else EXPECTED_TYPOLOGIES

        # Add individual realized lines (thinner)
        for item in items:
            item_realized = []
            for month_num in range(1, 13):
                # Use production-month realized (includes carryover)
                val, _ = calculate_production_month_with_carryover(
                    df, year, month_num, dimension, item, use_pondere
                )
                item_realized.append(val)
                realized_total[month_num-1] += val
                objective_total[month_num-1] += objective_for_month(year, metric, dimension, item, month_num)

                # Pure signature for this month
                if show_pure:
                    brut, pond = calculate_pure_signature_for_month(
                        df, year, month_num, dimension, item, use_pondere
                    )
                    pure_brut_total[month_num-1] += brut
                    if use_pondere:
                        pure_pondere_total[month_num-1] += pond

            # Get color
            if dimension == "bu":
                color = BU_COLORS.get(item, '#808080')
            else:
                color = TYPOLOGIE_COLORS.get(item, TYPOLOGIE_DEFAULT_COLOR)

            # Individual item line
            fig.add_trace(go.Scatter(
                x=months,
                y=item_realized,
                mode='lines',
                name=f'{item}',
                line=dict(width=1.5, color=color),
                opacity=0.6
            ))

        # Cumulative/Total lines (thick)
        fig.add_trace(go.Scatter(
            x=months,
            y=realized_total,
            mode='lines+markers',
            name='Total Réalisé (Production)',
            line=dict(color='#2d5a3f', width=4),
            marker=dict(size=10)
        ))

        # Add Cumulative Realized line
        cumulative_realized = np.cumsum(realized_total)
        fig.add_trace(go.Scatter(
            x=months,
            y=cumulative_realized,
            mode='lines',
            name='Cumul Réalisé',
            line=dict(color='#27ae60', width=3, dash='dot'),
            yaxis='y2'
        ))

        fig.add_trace(go.Scatter(
            x=months,
            y=objective_total,
            mode='lines',
            name='Total Objectif',
            line=dict(color='#e74c3c', width=3, dash='dash')
        ))

        # Pure signature lines (if enabled)
        if show_pure:
            fig.add_trace(go.Scatter(
                x=months,
                y=pure_brut_total,
                mode='lines+markers',
                name='Pur (brut)',
                line=dict(color='#3498db', width=2, dash='dot'),
                marker=dict(size=6, symbol='circle'),
                opacity=0.7
            ))
            if use_pondere:
                fig.add_trace(go.Scatter(
                    x=months,
                    y=pure_pondere_total,
                    mode='lines+markers',
                    name='Pur (pondéré)',
                    line=dict(color='#9b59b6', width=2, dash='dot'),
                    marker=dict(size=6, symbol='square'),
                    opacity=0.7
                ))

        title = f"Toutes les {dimension.upper()}s - {metric.upper()} : Réalisé vs Objectif"
    else:
        # Single item view
        realized = []
        objectives = []
        pure_brut = []
        pure_pondere = []

        for month_num in range(1, 13):
            # Use production-month realized (includes carryover)
            realized_val, _ = calculate_production_month_with_carryover(
                df, year, month_num, dimension, key, use_pondere
            )
            objective_val = objective_for_month(year, metric, dimension, key, month_num)
            realized.append(realized_val)
            objectives.append(objective_val)

            # Pure signature for this month
            if show_pure:
                brut, pond = calculate_pure_signature_for_month(
                    df, year, month_num, dimension, key, use_pondere
                )
                pure_brut.append(brut)
                if use_pondere:
                    pure_pondere.append(pond)

        # Realized line
        fig.add_trace(go.Scatter(
            x=months,
            y=realized,
            mode='lines+markers',
            name='Réalisé (Production)',
            line=dict(color='#2d5a3f', width=3),
            marker=dict(size=8)
        ))

        # Objective line (dashed)
        fig.add_trace(go.Scatter(
            x=months,
            y=objectives,
            mode='lines+markers',
            name='Objectif',
            line=dict(color='#e74c3c', width=2, dash='dash'),
            marker=dict(size=6, symbol='diamond')
        ))

        # Pure signature lines (if enabled)
        if show_pure:
            fig.add_trace(go.Scatter(
                x=months,
                y=pure_brut,
                mode='lines+markers',
                name='Pur (brut)',
                line=dict(color='#3498db', width=2, dash='dot'),
                marker=dict(size=6, symbol='circle'),
                opacity=0.7
            ))
            if use_pondere:
                fig.add_trace(go.Scatter(
                    x=months,
                    y=pure_pondere,
                    mode='lines+markers',
                    name='Pur (pondéré)',
                    line=dict(color='#9b59b6', width=2, dash='dot'),
                    marker=dict(size=6, symbol='square'),
                    opacity=0.7
                ))

        title = f"{key} - {metric.upper()} : Réalisé vs Objectif"

    fig.update_layout(
        title=title,
        xaxis_title="Mois",
        yaxis_title="CA (€)",
        hovermode='x unified',
        height=500,
        legend=dict(x=1.02, y=1, xanchor='left')
    )

    if key == "all":
        fig.update_layout(
            yaxis2=dict(
                title="Cumul (€)",
                overlaying='y',
                side='right',
                showgrid=False
            )
        )

    return fig


def get_quarterly_by_typologie(df: pd.DataFrame, year: int, include_pondere: bool = False) -> Dict[str, Dict[str, Dict[str, float]]]:
    """
    Extract quarterly amounts grouped by Typologie.

    Args:
        df: DataFrame with quarterly columns and cf_typologie_de_devis column
        year: Year to extract quarters for
        include_pondere: Whether to include weighted amounts

    Returns:
        Nested dictionary: {'DV': {'Q1': {'total': float, 'pondere': float}, ...}, ...}
    """
    if df.empty or 'cf_typologie_de_devis' not in df.columns:
        return {}

    quarters = ['Q1', 'Q2', 'Q3', 'Q4']
    result = {}

    for _, row in df.iterrows():
        tags, primary = allocate_typologie_for_row(row)

        if not primary:
            continue

        if primary not in result:
            result[primary] = {q: {'total': 0.0, 'pondere': 0.0} for q in quarters}

        for quarter in quarters:
            total_col = f'Montant Total {quarter}_{year}'
            pondere_col = f'Montant Pondéré {quarter}_{year}'

            total_val = float(row.get(total_col, 0) or 0)
            result[primary][quarter]['total'] += total_val

            if include_pondere:
                pondere_val = float(row.get(pondere_col, 0) or 0)
                result[primary][quarter]['pondere'] += pondere_val

    return result


# =============================================================================
# PRODUCTION YEAR DATA FUNCTIONS (À Produire)
# =============================================================================

def get_production_year_totals(df: pd.DataFrame, production_year: int, include_pondere: bool = False) -> Dict[str, float]:
    """
    Get total amounts for a specific production year.

    Uses 'Montant Total {production_year}' and 'Montant Pondéré {production_year}' columns.

    Args:
        df: DataFrame with financial columns
        production_year: Target production year (e.g., 2025, 2026, 2027)
        include_pondere: Whether to include weighted amounts

    Returns:
        Dictionary with 'total', 'pondere' (if requested), and 'count'
    """
    if df.empty:
        return {'total': 0.0, 'pondere': 0.0, 'count': 0}

    total_col = f'Montant Total {production_year}'
    pondere_col = f'Montant Pondéré {production_year}'

    result = {'total': 0.0, 'count': 0}

    if total_col in df.columns:
        # Filter rows with production in this year
        mask = df[total_col] > 0
        result['total'] = df[total_col].sum()
        result['count'] = mask.sum()

    if include_pondere and pondere_col in df.columns:
        result['pondere'] = df[pondere_col].sum()

    return result


def get_production_bu_amounts(df: pd.DataFrame, production_year: int, include_pondere: bool = False) -> Dict[str, Dict[str, float]]:
    """
    Get production year amounts grouped by Business Unit.

    Args:
        df: DataFrame with financial columns and cf_bu column
        production_year: Target production year
        include_pondere: Whether to include weighted amounts

    Returns:
        Dictionary: {'CONCEPTION': {'total': float, 'pondere': float, 'count': int}, ...}
    """
    if df.empty or 'cf_bu' not in df.columns:
        return {}

    total_col = f'Montant Total {production_year}'
    pondere_col = f'Montant Pondéré {production_year}'

    if total_col not in df.columns:
        return {}

    result = {}

    for bu in BU_ORDER:
        bu_df = df[df['cf_bu'] == bu]
        if bu_df.empty:
            result[bu] = {'total': 0.0, 'pondere': 0.0, 'count': 0}
            continue

        total = bu_df[total_col].sum() if total_col in bu_df.columns else 0.0
        count = (bu_df[total_col] > 0).sum() if total_col in bu_df.columns else 0

        bu_result = {'total': total, 'count': count}

        if include_pondere and pondere_col in bu_df.columns:
            bu_result['pondere'] = bu_df[pondere_col].sum()

        result[bu] = bu_result

    return result


def get_production_typologie_amounts(df: pd.DataFrame, production_year: int, include_pondere: bool = False) -> Dict[str, Dict[str, float]]:
    """
    Get production year amounts grouped by Typologie (with split handling).

    Args:
        df: DataFrame with financial columns and cf_typologie_de_devis column
        production_year: Target production year
        include_pondere: Whether to include weighted amounts

    Returns:
        Dictionary: {'DV': {'total': float, 'pondere': float, 'count': int}, ...}
    """
    if df.empty or 'cf_typologie_de_devis' not in df.columns:
        return {}

    total_col = f'Montant Total {production_year}'
    pondere_col = f'Montant Pondéré {production_year}'

    if total_col not in df.columns:
        return {}

    result = {}
    count_dict = {}  # Track counts separately (all tags)
    amount_dict = {}  # Track amounts (primary only)
    pondere_dict = {}  # Track weighted amounts (primary only)

    for _, row in df.iterrows():
        tags, primary = allocate_typologie_for_row(row)

        if not primary:
            continue

        # Count: increment for all tags
        for tag in tags:
            if tag not in count_dict:
                count_dict[tag] = 0
            count_dict[tag] += 1

        # Amount: add to primary only
        total_amount = float(row.get(total_col, 0) or 0)
        if primary not in amount_dict:
            amount_dict[primary] = 0.0
        amount_dict[primary] += total_amount

        if include_pondere:
            pondere_amount = float(row.get(pondere_col, 0) or 0)
            if primary not in pondere_dict:
                pondere_dict[primary] = 0.0
            pondere_dict[primary] += pondere_amount

    # Combine into result structure
    all_typologies = set(count_dict.keys()) | set(amount_dict.keys())
    for typ in all_typologies:
        result[typ] = {
            'total': amount_dict.get(typ, 0.0),
            'pondere': pondere_dict.get(typ, 0.0) if include_pondere else 0.0,
            'count': count_dict.get(typ, 0)
        }

    return result


def get_production_ts_total(df: pd.DataFrame, production_year: int) -> float:
    """
    Calculate total production amount for TS projects for a specific year.

    Args:
        df: DataFrame with financial columns and title column
        production_year: Target production year

    Returns:
        Sum of production amounts for TS projects
    """
    if df.empty or 'title' not in df.columns:
        return 0.0

    total_col = f'Montant Total {production_year}'

    if total_col not in df.columns:
        return 0.0

    mask = df['title'].astype(str).str.contains('TS', case=False, na=False)
    return df.loc[mask, total_col].sum()


def detect_production_years(df: pd.DataFrame) -> List[int]:
    """
    Detect available production years from DataFrame columns.

    Looks for columns like "Montant Total 2025" and returns extracted years.
    """
    if df is None or df.empty:
        return []

    years: List[int] = []
    for col in df.columns:
        m = re.fullmatch(r"Montant Total (\d{4})", str(col).strip())
        if not m:
            continue
        try:
            years.append(int(m.group(1)))
        except ValueError:
            continue

    return sorted(set(years))


def calculate_production_allocation_summary(df: pd.DataFrame) -> Dict[str, float]:
    """
    Compute reconciliation between raw amount and production-year columns.

    Returns:
      - amount_total: sum(df['amount'])
      - allocated_total: sum of all detected "Montant Total {year}" columns
      - unallocated_total: max(amount_total - allocated_total, 0)
    """
    if df is None or df.empty:
        return {"amount_total": 0.0, "allocated_total": 0.0, "unallocated_total": 0.0}

    amount_total = float(df["amount"].sum()) if "amount" in df.columns else 0.0

    years = detect_production_years(df)
    allocated_total = 0.0
    for y in years:
        col = f"Montant Total {y}"
        if col in df.columns:
            allocated_total += float(pd.to_numeric(df[col], errors="coerce").fillna(0).sum())

    unallocated_total = max(amount_total - allocated_total, 0.0)
    return {
        "amount_total": amount_total,
        "allocated_total": allocated_total,
        "unallocated_total": unallocated_total,
    }


def _to_timestamp_safe(val: Any) -> Optional[pd.Timestamp]:
    """Best-effort conversion to pandas Timestamp (handles '', None, already-Timestamp)."""
    try:
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return pd.NaT
        if isinstance(val, pd.Timestamp):
            return val
        s = str(val).strip()
        if not s or s.lower() in {"nat", "none"}:
            return pd.NaT
        return pd.to_datetime(s[:10], errors="coerce")
    except Exception:
        return pd.NaT


def build_production_diagnostics(df: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """
    Build per-row diagnostics for production allocation.

    Returns:
      - diag_df: original rows + allocated_total_row, unallocated_row, reason
      - meta: dict with summary stats
    """
    if df is None or df.empty:
        return pd.DataFrame(), {"reasons": {}, "detected_years": [], "max_detected_year": None}

    work = df.copy()

    detected_years = detect_production_years(work)
    max_detected_year = max(detected_years) if detected_years else None

    # Ensure numeric base amount
    if "amount" in work.columns:
        work["amount"] = pd.to_numeric(work["amount"], errors="coerce").fillna(0)
    else:
        work["amount"] = 0.0

    # Row-wise allocated sum across all detected production year columns
    alloc_cols = [f"Montant Total {y}" for y in detected_years if f"Montant Total {y}" in work.columns]
    if alloc_cols:
        for c in alloc_cols:
            work[c] = pd.to_numeric(work[c], errors="coerce").fillna(0)
        work["allocated_total_row"] = work[alloc_cols].sum(axis=1)
    else:
        work["allocated_total_row"] = 0.0

    work["unallocated_row"] = (work["amount"] - work["allocated_total_row"]).clip(lower=0)

    # Date parsing for reasons
    start_series = work["projet_start"] if "projet_start" in work.columns else pd.Series([pd.NaT] * len(work))
    stop_series = work["projet_stop"] if "projet_stop" in work.columns else pd.Series([pd.NaT] * len(work))
    work["_start_ts"] = start_series.apply(_to_timestamp_safe)
    work["_stop_ts"] = stop_series.apply(_to_timestamp_safe)

    def _reason(row: pd.Series) -> str:
        if float(row.get("unallocated_row", 0) or 0) <= 1.0:
            return ""

        start = row.get("_start_ts", pd.NaT)
        stop = row.get("_stop_ts", pd.NaT)

        if pd.isna(start):
            return "projet_start manquant"
        if pd.notna(stop) and stop < start:
            return "dates incohérentes (stop < start)"

        if max_detected_year is not None and pd.notna(stop) and int(stop.year) > int(max_detected_year):
            return f"hors fenêtre (fin projet {int(stop.year)} > {int(max_detected_year)})"

        return "non réparti (cause à investiguer)"

    work["reason"] = work.apply(_reason, axis=1)

    reasons = work.loc[work["unallocated_row"] > 1.0, "reason"].value_counts().to_dict()
    meta = {
        "detected_years": detected_years,
        "max_detected_year": max_detected_year,
        "reasons": reasons,
        "unallocated_total": float(work["unallocated_row"].sum()),
        "unallocated_rows": int((work["unallocated_row"] > 1.0).sum()),
    }

    return work, meta


def load_aggregated_production_data(production_year: int, sheet_type: str) -> pd.DataFrame:
    """
    Load data from all relevant years for a production target.

    For "À produire 2026", this aggregates:
    - Data from SPREADSHEET_SIGNE_2025 with Montant Total 2026 > 0
    - Data from SPREADSHEET_SIGNE_2026 with Montant Total 2026 > 0

    Args:
        production_year: Target production year (e.g., 2026)
        sheet_type: Type of sheet ("Signé", "Envoyé", "État au")

    Returns:
        Combined DataFrame with all relevant production data
    """
    all_data = []

    # Determine which years might have data for this production year
    # Revenue engine tracks Y, Y+1, Y+2, so we need to check Y, Y-1, Y-2
    years_to_check = [production_year - 2, production_year - 1, production_year]

    total_col = f'Montant Total {production_year}'

    for year in years_to_check:
        if year < 2024:  # Don't check years before system existed
            continue

        try:
            df = load_year_data(year, sheet_type)
            if not df.empty and total_col in df.columns:
                # Only keep rows with production in target year
                df_filtered = df[df[total_col] > 0].copy()
                if not df_filtered.empty:
                    df_filtered['signed_year'] = year
                    all_data.append(df_filtered)
                    print(f"  ✓ Loaded {len(df_filtered)} rows from {sheet_type} {year} for production year {production_year}")
        except Exception as e:
            print(f"  ⚠ Could not load {sheet_type} {year}: {e}")
            continue

    if not all_data:
        return pd.DataFrame()

    # Combine all data
    # Ensure all DataFrames have same columns
    all_columns = set()
    for df in all_data:
        all_columns.update(df.columns)
    all_columns = sorted(list(all_columns))

    aligned_dfs = []
    for df in all_data:
        for col in all_columns:
            if col not in df.columns:
                df[col] = None
        df = df[all_columns]
        aligned_dfs.append(df)

    result = pd.concat(aligned_dfs, ignore_index=True)
    print(f"  ✓ Combined {len(result)} total rows for production year {production_year}")

    return result


# =============================================================================
# PRODUCTION YEAR CHART FUNCTIONS
# =============================================================================

def plot_production_bu_bar(df: pd.DataFrame, production_year: int, title: str = "Montant par BU", show_count: bool = True) -> go.Figure:
    """
    Create a horizontal bar chart by BU for production year amounts.

    Args:
        df: DataFrame with financial columns
        production_year: Target production year
        title: Chart title
        show_count: Whether to show project counts in labels

    Returns:
        Plotly Figure
    """
    if df.empty or 'cf_bu' not in df.columns:
        return go.Figure()

    total_col = f'Montant Total {production_year}'

    if total_col not in df.columns:
        return go.Figure()

    # Aggregate by BU
    bu_data = df.groupby('cf_bu')[total_col].sum().reset_index()
    bu_data.columns = ['cf_bu', 'amount']

    # Add count (projects with production in this year)
    bu_counts = df[df[total_col] > 0].groupby('cf_bu').size().reset_index(name='count')
    bu_data = bu_data.merge(bu_counts, on='cf_bu', how='left').fillna(0)

    bu_data = bu_data[bu_data['amount'] > 0].sort_values('amount', ascending=False)

    if bu_data.empty:
        return go.Figure()

    # Map colors
    colors = [BU_COLORS.get(bu, BU_COLORS['AUTRE']) for bu in bu_data['cf_bu']]

    # Labels with counts
    if show_count:
        labels = [f"{row['cf_bu']} ({int(row['count'])})" for _, row in bu_data.iterrows()]
    else:
        labels = bu_data['cf_bu'].tolist()

    fig = go.Figure(go.Bar(
        x=labels,
        y=bu_data['amount'],
        marker_color=colors,
        text=[f"{v:,.0f}€" for v in bu_data['amount']],
        textposition='outside'
    ))

    fig.update_layout(
        title=dict(text=f"{title} - {production_year}", x=0.5),
        xaxis_title="",
        yaxis_title="Montant (€)",
        showlegend=False,
        margin=dict(t=60, b=60, l=60, r=40),
        height=350
    )

    return fig


def plot_production_typologie_bar(df: pd.DataFrame, production_year: int, title: str = "Montant par Typologie", show_count: bool = True) -> go.Figure:
    """
    Create a vertical bar chart by Typologie for production year amounts.

    Args:
        df: DataFrame with financial columns
        production_year: Target production year
        title: Chart title
        show_count: Whether to show project counts in labels

    Returns:
        Plotly Figure
    """
    if df.empty or 'cf_typologie_de_devis' not in df.columns:
        return go.Figure()

    # Get typologie amounts with split handling
    type_amounts = get_production_typologie_amounts(df, production_year, include_pondere=False)

    if not type_amounts:
        return go.Figure()

    # Convert to DataFrame for plotting
    type_data = pd.DataFrame([
        {'cf_typologie_de_devis': typ, 'amount': data['total'], 'count': data['count']}
        for typ, data in type_amounts.items()
    ])

    type_data = type_data[type_data['amount'] > 0].sort_values('amount', ascending=False)

    if type_data.empty:
        return go.Figure()

    # Colors - use dictionary lookup with fallback to gray for unknown types
    colors = [TYPOLOGIE_COLORS.get(row['cf_typologie_de_devis'], TYPOLOGIE_DEFAULT_COLOR) for _, row in type_data.iterrows()]

    # Labels with counts
    if show_count:
        labels = [f"{row['cf_typologie_de_devis']} ({int(row['count'])})" for _, row in type_data.iterrows()]
    else:
        labels = type_data['cf_typologie_de_devis'].tolist()

    fig = go.Figure(go.Bar(
        x=labels,
        y=type_data['amount'],
        marker_color=colors,
        text=[f"{v:,.0f}€" for v in type_data['amount']],
        textposition='outside'
    ))

    fig.update_layout(
        title=dict(text=f"{title} - {production_year}", x=0.5),
        xaxis_title="",
        yaxis_title="Montant (€)",
        showlegend=False,
        margin=dict(t=60, b=80, l=60, r=40),
        height=350
    )

    return fig


# =============================================================================
# PRODUCTION TABS UI COMPONENTS
# =============================================================================

def create_production_bu_kpi_row(
    df: pd.DataFrame,
    production_year: int,
    bu_amounts: Dict[str, Dict[str, float]],
    show_pondere: bool = False,
    key_prefix: str = ""
) -> None:
    """
    Create a row of BU-colored KPI cards for production year amounts with popovers.

    Args:
        df: DataFrame with production year columns
        production_year: Target production year
        bu_amounts: Dictionary from get_production_bu_amounts()
        show_pondere: Whether to show Total / Pondéré format
        key_prefix: Unique prefix for widget keys
    """
    cols = st.columns(len(BU_ORDER))
    total_col = f'Montant Total {production_year}'

    for i, bu in enumerate(BU_ORDER):
        with cols[i]:
            amounts = bu_amounts.get(bu, {'total': 0, 'pondere': 0, 'count': 0})
            total = amounts.get('total', 0)
            count = amounts.get('count', 0)

            if show_pondere:
                pondere = amounts.get('pondere', 0)
                value = f"{total:,.0f}€ / {pondere:,.0f}€"
            else:
                value = f"{total:,.0f}€"

            label = f"{bu} ({count} projets)"
            create_kpi_card(label, value, "💼", bu)

            # Add popover with project list
            if not df.empty and 'cf_bu' in df.columns and total_col in df.columns:
                bu_projects = df[(df['cf_bu'] == bu) & (df[total_col] > 0)].copy()
                if not bu_projects.empty:
                    render_projects_popover(
                        "🔎 Voir projets",
                        bu_projects,
                        show_pondere=show_pondere,
                        header_text=f"Projets · Production {production_year} · BU={bu}"
                    )


def create_production_typologie_kpi_row(type_amounts: Dict[str, Dict[str, float]], show_pondere: bool = False, max_items: int = 5) -> None:
    """Create a row of Typologie-colored KPI cards for production year amounts."""
    if not type_amounts:
        st.info("Aucune typologie disponible")
        return

    # Sort by total and take top N
    sorted_types = sorted(type_amounts.items(), key=lambda x: x[1]['total'], reverse=True)[:max_items]
    num_cols = min(len(sorted_types), max_items)
    cols = st.columns(max(num_cols, 1))

    for i, (typ, amounts) in enumerate(sorted_types):
        with cols[i]:
            total = amounts.get('total', 0)
            count = amounts.get('count', 0)

            if show_pondere:
                pondere = amounts.get('pondere', 0)
                value = f"{total:,.0f}€ / {pondere:,.0f}€"
            else:
                value = f"{total:,.0f}€"

            # Use normalization helper for CSS class
            css_class = normalize_typologie_for_css(typ)

            label = f"{typ} ({int(count)} projets)"
            create_kpi_card(label, value, "🏷️", css_class)


def render_single_production_view(
    df: pd.DataFrame,
    production_year: int,
    show_pondere: bool = False,
    key_prefix: str = ""
) -> None:
    """
    Render a single production year view with KPIs and charts.

    This renders the content for one "À produire {year}" tab.

    Args:
        df: DataFrame with production year columns
        production_year: Target year (e.g., 2025, 2026, 2027)
        show_pondere: Whether to show weighted amounts (for Envoyé/État views)
        key_prefix: Unique prefix for Streamlit widget keys
    """
    total_col = f'Montant Total {production_year}'
    pondere_col = f'Montant Pondéré {production_year}'

    # Check if we have data for this production year
    if df.empty or total_col not in df.columns:
        st.info(f"Aucune donnée de production disponible pour {production_year}")
        return

    # Get totals for this production year
    totals = get_production_year_totals(df, production_year, include_pondere=show_pondere)

    if totals['total'] == 0:
        st.info(f"Aucun montant à produire pour {production_year}")
        return

    # === KPIs ===
    st.markdown(f'<div class="section-header">📊 Indicateurs Clés - À produire {production_year}</div>', unsafe_allow_html=True)

    # Calculate monthly average for this production year (12 months default)
    production_monthly_avg = totals['total'] / 12.0

    if show_pondere:
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            create_kpi_card("CA à produire", f"{totals['total']:,.0f}€", "💰", "default")
        with col2:
            create_kpi_card("CA Pondéré", f"{totals.get('pondere', 0):,.0f}€", "⚖️", "default")
        with col3:
            create_kpi_card("Projets concernés", f"{totals['count']}", "📁", "default")
            # Add popover for projects
            if not df.empty and total_col in df.columns:
                projects = df[df[total_col] > 0].copy()
                if not projects.empty:
                    render_projects_popover(
                        "🔎 Voir projets",
                        projects,
                        show_pondere=show_pondere,
                        header_text=f"Projets · Production {production_year}"
                    )
        with col4:
            create_kpi_card("Moyenne mensuelle", f"{production_monthly_avg:,.0f}€", "📅", "default")
    else:
        col1, col2, col3 = st.columns(3)
        with col1:
            create_kpi_card("CA à produire", f"{totals['total']:,.0f}€", "💰", "default")
        with col2:
            create_kpi_card("Projets concernés", f"{totals['count']}", "📁", "default")
            # Add popover for projects
            if not df.empty and total_col in df.columns:
                projects = df[df[total_col] > 0].copy()
                if not projects.empty:
                    render_projects_popover(
                        "🔎 Voir projets",
                        projects,
                        show_pondere=show_pondere,
                        header_text=f"Projets · Production {production_year}"
                    )
        with col3:
            create_kpi_card("Moyenne mensuelle", f"{production_monthly_avg:,.0f}€", "📅", "default")

    st.markdown("<br>", unsafe_allow_html=True)

    # === BU Amounts ===
    st.markdown('<div class="section-header">💼 Montants par Business Unit</div>', unsafe_allow_html=True)
    bu_amounts = get_production_bu_amounts(df, production_year, include_pondere=show_pondere)
    create_production_bu_kpi_row(df, production_year, bu_amounts, show_pondere=show_pondere, key_prefix=key_prefix)

    st.markdown("<br>", unsafe_allow_html=True)

    # === Typologie Amounts (BU-Grouped) ===
    st.markdown('<div class="section-header">🏷️ Montants par Typologie (groupés par BU)</div>', unsafe_allow_html=True)
    create_bu_grouped_typologie_blocks_production(df, production_year=production_year, show_pondere=show_pondere)

    st.markdown("<br>", unsafe_allow_html=True)

    # === Charts ===
    # Vertical layout for charts
    st.markdown("#### Montant par Business Unit")
    fig_bu = plot_production_bu_bar(df, production_year, "Montant par BU")
    st.plotly_chart(fig_bu, use_container_width=True, key=f"{key_prefix}_prod_bu_bar_{production_year}", config={})

    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown("#### Montant par Typologie")
    fig_type = plot_production_typologie_bar(df, production_year, "Montant par Typologie")
    st.plotly_chart(fig_type, use_container_width=True, key=f"{key_prefix}_prod_type_bar_{production_year}", config={})

    # === Quarterly Breakdown ===
    st.markdown("<br>", unsafe_allow_html=True)
    with st.expander(f"📅 Vue Trimestrielle - CA par Trimestre {production_year}", expanded=False):
        display_quarterly_breakdown(df, production_year, show_pondere=show_pondere)


def render_production_tabs(
    df: pd.DataFrame,
    selected_year: int,
    show_pondere: bool = False,
    key_prefix: str = "global"
) -> None:
    """
    Render the "À produire" tabs with KPIs, BU/Typologie breakdowns, and charts.

    Creates tabs for production years: selected_year, selected_year+1, selected_year+2

    Args:
        df: DataFrame with production year columns
        selected_year: Base year selected in sidebar
        show_pondere: Whether to show weighted amounts
        key_prefix: Unique prefix for Streamlit widget keys
    """
    # Prefer the years that actually exist in this dataset (prevents hiding e.g. 2028 columns).
    # Cap at +3 years from selected_year (Rule 4: track up to +3 years).
    # Fall back to the legacy UI (selected_year..+2) if we can't detect anything.
    detected_years = detect_production_years(df)
    max_year = selected_year + 3  # Rule 4: track up to +3 years

    if detected_years:
        # Filter to only show years within the +3 window
        years_with_data: List[int] = []
        for y in detected_years:
            if y <= max_year:  # Cap at +3 years
                col = f"Montant Total {y}"
                if col in df.columns and float(pd.to_numeric(df[col], errors="coerce").fillna(0).sum()) > 0:
                    years_with_data.append(y)
        production_years = years_with_data or [y for y in detected_years if y <= max_year]
    else:
        # Default: show selected_year through selected_year + 3
        production_years = [selected_year, selected_year + 1, selected_year + 2, selected_year + 3]

    legacy_years = [selected_year, selected_year + 1, selected_year + 2]
    if detected_years and set(detected_years) != set(legacy_years):
        filtered_detected = [y for y in detected_years if y <= max_year]
        #st#.caption(
         #   "ℹ️ Années détectées dans les colonnes de production (jusqu'à +3 ans): "
          #  + ", ".join(str(y) for y in filtered_detected)
        #)

    # Reconciliation warning: explains why CA Total may not equal the sum of production years.
    alloc = calculate_production_allocation_summary(df)
    if alloc["unallocated_total"] > 1.0:
        st.warning(
            "⚠️ Une partie du CA n'est pas répartie par année de production "
            "(dates projet manquantes/incohérentes ou hors fenêtre de calcul). "
            f"Non réparti: {alloc['unallocated_total']:,.0f}€."
        )
        with st.expander("🩺 Diagnostic du CA non réparti", expanded=False):
            diag_df, meta = build_production_diagnostics(df)

            st.markdown(
                f"- **CA Total**: {alloc['amount_total']:,.0f}€\n"
                f"- **CA réparti (sommes des colonnes 'Montant Total YYYY')**: {alloc['allocated_total']:,.0f}€\n"
                f"- **CA non réparti**: {alloc['unallocated_total']:,.0f}€\n"
                f"- **Lignes concernées**: {meta.get('unallocated_rows', 0)}"
            )

            # Show flagged rows (dates_rule_applied)
            if 'dates_rule_applied' in df.columns:
                flagged_count = int(df['dates_rule_applied'].sum()) if df['dates_rule_applied'].dtype == bool else 0
                if flagged_count > 0:
                    st.markdown("---")
                    st.markdown(f"**📋 Lignes avec règles de dates appliquées (Rules 1-3): {flagged_count}**")

                    flagged_df = df[df['dates_rule_applied'] == True].copy()
                    if 'dates_rule' in flagged_df.columns:
                        rule_counts = flagged_df['dates_rule'].value_counts().to_dict()
                        st.markdown("**Répartition des règles appliquées:**")
                        for rule, count in rule_counts.items():
                            st.write(f"- {rule}: {count} lignes")

                    flagged_cols = [
                        c for c in [
                            "id", "title", "company_name", "cf_bu",
                            "projet_start", "projet_stop", "date",
                            "dates_effective_start", "dates_effective_stop",
                            "dates_rule", "amount", "source_sheet",
                        ]
                        if c in flagged_df.columns
                    ]
                    if flagged_cols:
                        # Show top 50 flagged rows
                        top_flagged = flagged_df.sort_values("amount", ascending=False).head(50)
                        if not top_flagged.empty:
                            st.markdown("**Top 50 devis avec règles de dates appliquées (par montant):**")
                            st.dataframe(top_flagged[flagged_cols], use_container_width=True, hide_index=True)

            if meta.get("reasons"):
                st.markdown("---")
                st.markdown("**Répartition des causes (heuristique):**")
                for k, v in meta["reasons"].items():
                    st.write(f"- {k}: {v}")

            if not diag_df.empty:
                cols = [
                    c for c in [
                        "id", "title", "company_name", "cf_bu",
                        "projet_start", "projet_stop",
                        "amount", "allocated_total_row", "unallocated_row",
                        "reason", "source_sheet",
                    ]
                    if c in diag_df.columns
                ]
                top = diag_df[diag_df["unallocated_row"] > 1.0].copy()
                top = top.sort_values("unallocated_row", ascending=False).head(50)
                if not top.empty:
                    st.markdown("---")
                    st.markdown("**Top 50 devis non répartis (par montant):**")
                    st.dataframe(top[cols], use_container_width=True, hide_index=True)
                else:
                    st.info("Aucune ligne non répartie détectée.")
    tab_names = [f"À produire {y}" for y in production_years]

    prod_tabs = st.tabs(tab_names)

    for i, prod_year in enumerate(production_years):
        with prod_tabs[i]:
            render_single_production_view(
                df=df,
                production_year=prod_year,
                show_pondere=show_pondere,
                key_prefix=f"{key_prefix}_{i}"
            )


# =============================================================================
# UI COMPONENTS
# =============================================================================

def create_kpi_card(label: str, value: str, icon: str = "📊", bu_class: str = "default") -> None:
    """Create a styled KPI card with BU-specific coloring."""
    css_class = f"metric-card metric-card-{bu_class.lower()}"
    st.markdown(f"""
    <div class="{css_class}">
        <div style="font-size: 1.5rem;">{icon}</div>
        <div class="metric-value">{value}</div>
        <div class="metric-label">{label}</div>
    </div>
    """, unsafe_allow_html=True)


def build_furious_url(proposal_id: str) -> str:
    """
    Build Furious CRM URL from proposal ID.

    Args:
        proposal_id: The proposal ID from Furious

    Returns:
        Full URL to the proposal in Furious, or empty string if no ID
    """
    if not proposal_id or pd.isna(proposal_id):
        return ''
    return f"https://merciraymond.furious-squad.com/compta.php?view=5&cherche={proposal_id}"


def prepare_projects_table(df: pd.DataFrame, *, show_pondere: bool = False) -> pd.DataFrame:
    """
    Prepare a minimal projects table for display in popover.

    Args:
        df: DataFrame with project data
        show_pondere: Whether to include weighted amount column

    Returns:
        DataFrame with minimal columns formatted for display
    """
    if df.empty:
        return pd.DataFrame()

    # Select minimal columns
    display_cols = ['title', 'company_name', 'amount']
    if show_pondere and 'amount_pondere' in df.columns:
        display_cols.append('amount_pondere')
    display_cols.extend(['probability', 'date', 'projet_start', 'projet_stop', 'cf_bu', 'cf_typologie_de_devis'])
    if 'id' in df.columns:
        display_cols.append('id')

    # Filter to only existing columns
    display_cols = [c for c in display_cols if c in df.columns]

    # Create a copy for formatting
    result_df = df[display_cols].copy()

    # Format date columns to DD/MM/YYYY
    date_cols = ['date', 'projet_start', 'projet_stop']
    for col in date_cols:
        if col in result_df.columns:
            result_df[col] = pd.to_datetime(result_df[col], errors='coerce')
            result_df[col] = result_df[col].apply(
                lambda x: x.strftime('%d/%m/%Y') if pd.notna(x) else ''
            )

    # Format amount columns
    if 'amount' in result_df.columns:
        result_df['amount'] = result_df['amount'].apply(lambda x: f"{float(x):,.0f}€" if pd.notna(x) else '')
    if 'amount_pondere' in result_df.columns:
        result_df['amount_pondere'] = result_df['amount_pondere'].apply(
            lambda x: f"{float(x):,.0f}€" if pd.notna(x) else ''
        )

    # Format probability
    if 'probability' in result_df.columns:
        result_df['probability'] = result_df['probability'].apply(
            lambda x: f"{float(x):.0f}%" if pd.notna(x) else ''
        )

    # Create furious_url column if id exists
    if 'id' in result_df.columns:
        result_df['furious_url'] = result_df['id'].apply(build_furious_url)
        # Reorder to put furious_url after id
        cols = result_df.columns.tolist()
        if 'id' in cols and 'furious_url' in cols:
            id_idx = cols.index('id')
            cols.remove('furious_url')
            cols.insert(id_idx + 1, 'furious_url')
            result_df = result_df[cols]

    return result_df


def render_projects_popover(
    trigger_label: str,
    projects_df: pd.DataFrame,
    *,
    show_pondere: bool = False,
    header_text: Optional[str] = None
) -> None:
    """
    Render a popover with project list table.

    Args:
        trigger_label: Label for the popover trigger button (must be unique)
        projects_df: DataFrame with project data
        show_pondere: Whether to show weighted amounts
        header_text: Optional header text to display in popover
    """
    if projects_df.empty:
        with st.popover(trigger_label, use_container_width=True):
            st.info("Aucun projet disponible")
        return

    prepared_df = prepare_projects_table(projects_df, show_pondere=show_pondere)

    # Build column config for dataframe
    column_config = {}

    # Configure furious_url as LinkColumn if present
    if 'furious_url' in prepared_df.columns:
        column_config['furious_url'] = st.column_config.LinkColumn(
            "🔗 Furious",
            help="Ouvrir dans Furious CRM",
            max_chars=100
        )

    # Configure amount columns
    if 'amount' in prepared_df.columns:
        column_config['amount'] = st.column_config.TextColumn("Montant", width="medium")
    if 'amount_pondere' in prepared_df.columns:
        column_config['amount_pondere'] = st.column_config.TextColumn("Montant Pondéré", width="medium")

    # Configure other columns
    if 'title' in prepared_df.columns:
        column_config['title'] = st.column_config.TextColumn("Titre", width="large")
    if 'company_name' in prepared_df.columns:
        column_config['company_name'] = st.column_config.TextColumn("Client", width="medium")
    if 'probability' in prepared_df.columns:
        column_config['probability'] = st.column_config.TextColumn("Probabilité", width="small")

    with st.popover(trigger_label, use_container_width=True):
        if header_text:
            st.markdown(f"**{header_text}**")
        st.markdown(f"**{len(projects_df)} projet(s)**")
        st.dataframe(
            prepared_df,
            use_container_width=True,
            hide_index=True,
            column_config=column_config if column_config else None
        )


def create_bu_kpi_row(
    df: pd.DataFrame,
    bu_amounts: Dict[str, Dict[str, float]],
    show_pondere: bool = False,
    show_count: bool = True,
    key_prefix: str = ""
) -> None:
    """Create a row of BU-colored KPI cards with project counts and popovers."""
    cols = st.columns(len(BU_ORDER))

    for i, bu in enumerate(BU_ORDER):
        with cols[i]:
            amounts = bu_amounts.get(bu, {'total': 0, 'pondere': 0, 'count': 0})
            total = amounts.get('total', 0)
            count = amounts.get('count', 0)

            if show_pondere:
                pondere = amounts.get('pondere', 0)
                value = f"{total:,.0f}€ / {pondere:,.0f}€"
                label = f"{bu} ({count} projets)"
            else:
                value = f"{total:,.0f}€"
                label = f"{bu} ({count} projets)"

            create_kpi_card(label, value, "💼", bu)

            # Add popover with project list
            if not df.empty and 'cf_bu' in df.columns:
                bu_projects = df[df['cf_bu'] == bu].copy()
                if not bu_projects.empty:
                    popover_key = f"{key_prefix}_bu_{bu}" if key_prefix else f"bu_{bu}"
                    render_projects_popover(
                        "🔎 Voir projets",
                        bu_projects,
                        show_pondere=show_pondere,
                        header_text=f"Projets · BU={bu}"
                    )


def create_typologie_kpi_row(type_amounts: Dict[str, Dict[str, float]], show_pondere: bool = False, max_items: int = 5) -> None:
    """Create a row of Typologie-colored KPI cards."""
    if not type_amounts:
        st.info("Aucune typologie disponible")
        return

    # Sort by total and take top N
    sorted_types = sorted(type_amounts.items(), key=lambda x: x[1]['total'], reverse=True)[:max_items]
    num_cols = min(len(sorted_types), max_items)
    cols = st.columns(max(num_cols, 1))

    for i, (typ, amounts) in enumerate(sorted_types):
        with cols[i]:
            total = amounts.get('total', 0)
            count = amounts.get('count', 0)

            if show_pondere:
                pondere = amounts.get('pondere', 0)
                value = f"{total:,.0f}€ / {pondere:,.0f}€"
            else:
                value = f"{total:,.0f}€"

            # Use normalization helper for CSS class
            css_class = normalize_typologie_for_css(typ)

            label = f"{typ} ({int(count)} projets)"
            create_kpi_card(label, value, "🏷️", css_class)


def create_bu_grouped_typologie_blocks(
    df: pd.DataFrame,
    show_pondere: bool = False
) -> None:
    """
    Create BU-grouped typologie KPI blocks showing dependency.

    For each BU, displays a header and all mapped typologies as KPI cards,
    even if they have 0€ / 0 projets.

    Args:
        df: DataFrame with cf_bu and cf_typologie_de_devis columns
        show_pondere: Whether to show weighted amounts
    """
    if df.empty:
        st.info("Aucune donnée disponible")
        return

    for bu in BU_ORDER:
        # Get typologies for this BU
        typologies = BU_TO_TYPOLOGIES.get(bu, [])
        if not typologies:
            continue

        # Get typologie amounts for this BU
        type_amounts = get_typologie_amounts_for_bu(df, bu, include_weighted=show_pondere)

        # BU header - simple colored title text (matching screenshot style)
        bu_color = BU_COLORS.get(bu, '#808080')
        st.markdown(
            f'<div style="font-weight: 700; color: {bu_color}; font-size: 1.1rem; margin: 1rem 0 0.5rem 0; text-transform: uppercase;">'
            f'{bu}</div>',
            unsafe_allow_html=True
        )

        # Create KPI cards for all typologies in this BU
        num_typologies = len(typologies)
        cols = st.columns(num_typologies)

        for i, typ in enumerate(typologies):
            with cols[i]:
                amounts = type_amounts.get(typ, {'total': 0.0, 'count': 0, 'pondere': 0.0})
                total = amounts.get('total', 0.0)
                count = amounts.get('count', 0)

                if show_pondere:
                    pondere = amounts.get('pondere', 0.0)
                    value = f"{total:,.0f}€ / {pondere:,.0f}€"
                else:
                    value = f"{total:,.0f}€"

                label = f"{typ} ({int(count)} projets)"
                # Use BU-themed cards (matches screenshot style and avoids CSS mismatch issues)
                create_kpi_card(label, value, "🏷️", bu.lower())

                # Add popover with project list
                typ_projects = filter_projects_for_typologie_bu(df, bu, typ)
                if not typ_projects.empty:
                    render_projects_popover(
                        "🔎 Voir projets",
                        typ_projects,
                        show_pondere=show_pondere,
                        header_text=f"Projets · BU={bu} · Typologie={typ}"
                    )

        st.markdown("<br>", unsafe_allow_html=True)


def get_production_typologie_amounts_for_bu(
    df: pd.DataFrame,
    production_year: int,
    bu: str,
    include_pondere: bool = False
) -> Dict[str, Dict[str, float]]:
    """
    Get production-year typologie amounts for a specific BU, with zero-filled output.

    This is the production-year equivalent of `get_typologie_amounts_for_bu()`.

    Special handling:
    - TS(typologie) under MAINTENANCE: counts ALL rows where primary typologie is 'TS',
      regardless of BU (so it can appear even when BU=TRAVAUX due to title rule).

    Args:
        df: DataFrame with cf_bu, cf_typologie_de_devis and financial columns
        production_year: Target production year (e.g. 2026)
        bu: Business Unit bucket
        include_pondere: Whether to include weighted production amounts

    Returns:
        Dict mapping typologie -> {total, count, pondere?} with zero-filled typologies.
    """
    typologies = BU_TO_TYPOLOGIES.get(bu, [])
    total_col = f"Montant Total {production_year}"
    pondere_col = f"Montant Pondéré {production_year}"

    base = {typ: {'total': 0.0, 'count': 0, 'pondere': 0.0} if include_pondere else {'total': 0.0, 'count': 0}
            for typ in typologies}

    if df.empty or 'cf_typologie_de_devis' not in df.columns or total_col not in df.columns:
        return base

    has_bu = 'cf_bu' in df.columns

    for _, row in df.iterrows():
        tags, primary = allocate_typologie_for_row(row)
        row_bu = str(row.get('cf_bu', 'AUTRE')).strip() if has_bu else 'AUTRE'

        total_amount = float(row.get(total_col, 0) or 0)
        pondere_amount = float(row.get(pondere_col, 0) or 0) if include_pondere else 0.0

        for typ in typologies:
            # Special case: TS under MAINTENANCE - count from ALL rows where primary is TS
            if typ == 'TS' and bu == 'MAINTENANCE':
                if primary == 'TS':
                    # Count: TS in tags
                    if 'TS' in tags:
                        base[typ]['count'] += 1
                    # Amount: primary is TS
                    base[typ]['total'] += total_amount
                    if include_pondere:
                        base[typ]['pondere'] += pondere_amount
            else:
                # Normal case: BU must match
                if row_bu != bu:
                    continue

                # Count: typ in tags
                if typ in tags:
                    base[typ]['count'] += 1

                # Amount: primary matches typ
                if primary == typ:
                    base[typ]['total'] += total_amount
                    if include_pondere:
                        base[typ]['pondere'] += pondere_amount

    return base


def create_bu_grouped_typologie_blocks_production(
    df: pd.DataFrame,
    production_year: int,
    show_pondere: bool = False
) -> None:
    """
    Render BU-grouped typologie KPI blocks using production-year columns.
    """
    if df.empty:
        st.info("Aucune donnée disponible")
        return

    for bu in BU_ORDER:
        typologies = BU_TO_TYPOLOGIES.get(bu, [])
        if not typologies:
            continue

        type_amounts = get_production_typologie_amounts_for_bu(
            df=df,
            production_year=production_year,
            bu=bu,
            include_pondere=show_pondere
        )

        # BU header - simple colored title text (matching screenshot style)
        bu_color = BU_COLORS.get(bu, '#808080')
        st.markdown(
            f'<div style="font-weight: 700; color: {bu_color}; font-size: 1.1rem; margin: 1rem 0 0.5rem 0; text-transform: uppercase;">'
            f'{bu}</div>',
            unsafe_allow_html=True
        )

        cols = st.columns(len(typologies))
        for i, typ in enumerate(typologies):
            with cols[i]:
                amounts = type_amounts.get(typ, {'total': 0.0, 'count': 0, 'pondere': 0.0})
                total = amounts.get('total', 0.0)
                count = amounts.get('count', 0)

                if show_pondere:
                    pondere = amounts.get('pondere', 0.0)
                    value = f"{total:,.0f}€ / {pondere:,.0f}€"
                else:
                    value = f"{total:,.0f}€"

                label = f"{typ} ({int(count)} projets)"
                create_kpi_card(label, value, "🏷️", bu.lower())

                # Add popover with project list
                typ_projects = filter_projects_for_typologie_bu_production(df, production_year, bu, typ)
                if not typ_projects.empty:
                    render_projects_popover(
                        "🔎 Voir projets",
                        typ_projects,
                        show_pondere=show_pondere,
                        header_text=f"Projets · Production {production_year} · BU={bu} · Typologie={typ}"
                    )

        st.markdown("<br>", unsafe_allow_html=True)


def create_colored_table_html(headers: List[str], rows: List[Dict[str, str]], row_colors: Dict[str, str] = None, header_color: str = "#1a472a") -> str:
    """
    Create an HTML table with colored rows.

    Args:
        headers: List of column headers
        rows: List of dictionaries with row data
        row_colors: Dictionary mapping row identifier (first column value) to color
        header_color: Background color for header row

    Returns:
        HTML string for the table
    """
    html = '<div style="overflow-x: auto;"><table style="width: 100%; border-collapse: collapse; margin: 10px 0;">'

    # Header row
    html += f'<thead><tr style="background-color: {header_color}; color: white; font-weight: bold;">'
    for header in headers:
        html += f'<th style="padding: 10px; text-align: left; border: 1px solid #ddd;">{header}</th>'
    html += '</tr></thead>'

    # Body rows
    html += '<tbody>'
    for row in rows:
        # Get row identifier (first column value)
        row_id = list(row.values())[0] if row else None
        bg_color = row_colors.get(row_id, '#ffffff') if row_colors else '#ffffff'
        # Yellow and light colors need dark text, others use white text
        light_colors = ['#f4c430', '#ffd700', '#ffffff', '#f0f0f0', '#f5f5f5']
        text_color = '#333333' if bg_color in light_colors else '#ffffff'

        html += f'<tr style="background-color: {bg_color}; color: {text_color};">'
        for header in headers:
            value = row.get(header, '')
            html += f'<td style="padding: 8px; border: 1px solid #ddd;">{value}</td>'
        html += '</tr>'
    html += '</tbody></table></div>'

    return html


def display_quarterly_breakdown(df: pd.DataFrame, year: int, show_pondere: bool = False) -> None:
    """
    Display quarterly CA breakdown in a formatted table.

    Shows:
    - Overall section: Total CA per quarter (Q1, Q2, Q3, Q4)
    - Per BU section: CA per quarter per BU (with colors)
    - Per Typologie section: CA per quarter per Typologie (with colors)

    Args:
        df: DataFrame with quarterly columns
        year: Year to display quarters for
        show_pondere: Whether to show pondération (Total / Pondéré format)
    """
    if df.empty:
        st.info("Aucune donnée trimestrielle disponible")
        return

    # Extract quarterly data
    quarterly_totals = get_quarterly_totals(df, year, include_pondere=show_pondere)
    quarterly_by_bu = get_quarterly_by_bu(df, year, include_pondere=show_pondere)
    quarterly_by_typologie = get_quarterly_by_typologie(df, year, include_pondere=show_pondere)

    def quarterly_typologies_grouped_by_bu_mapped() -> Dict[str, Dict[str, Dict[str, Dict[str, float]]]]:
        """
        Quarterly amounts for mapped typologies grouped by BU, with proper split handling.

        Important: we apply the typology split factor to quarterly columns as well (to avoid over-counting).
        """
        quarters_local = ['Q1', 'Q2', 'Q3', 'Q4']
        result: Dict[str, Dict[str, Dict[str, Dict[str, float]]]] = {}

        # Initialize with all mapped typologies so 0s still display
        for bu in BU_ORDER:
            result[bu] = {}
            for typ in BU_TO_TYPOLOGIES.get(bu, []):
                result[bu][typ] = {q: {'total': 0.0, 'pondere': 0.0} for q in quarters_local}

        if df.empty or 'cf_typologie_de_devis' not in df.columns:
            return result

        has_bu = 'cf_bu' in df.columns

        for _, row in df.iterrows():
            tags, primary = allocate_typologie_for_row(row)

            if not primary:
                continue

            row_bu = str(row.get('cf_bu', 'AUTRE')).strip() if has_bu else 'AUTRE'

            for bu in BU_ORDER:
                if primary not in BU_TO_TYPOLOGIES.get(bu, []):
                    continue

                # TS(typologie) under MAINTENANCE ignores BU filter by design
                if not (primary == 'TS' and bu == 'MAINTENANCE'):
                    if row_bu != bu:
                        continue

                for q in quarters_local:
                    total_col = f"Montant Total {q}_{year}"
                    pondere_col = f"Montant Pondéré {q}_{year}"

                    total_val = float(row.get(total_col, 0) or 0)
                    result[bu][primary][q]['total'] += total_val

                    if show_pondere:
                        pondere_val = float(row.get(pondere_col, 0) or 0)
                        result[bu][primary][q]['pondere'] += pondere_val

        return result

    # Check if we have any quarterly data
    has_data = (any(q['total'] > 0 for q in quarterly_totals.values()) or
                len(quarterly_by_bu) > 0 or
                len(quarterly_by_typologie) > 0)

    if not has_data:
        st.info(f"Aucune donnée trimestrielle disponible pour {year}")
        return

    quarters = ['Q1', 'Q2', 'Q3', 'Q4']

    # === OVERALL SECTION ===
    st.markdown('<h4 style="color: #2c3e50;">📊 CA Total par Trimestre</h4>', unsafe_allow_html=True)

    # Create overall table (transposed to match others: Row = CA Total, Cols = Q1-Q4)
    overall_row = {'Metric': 'CA Total'}

    for quarter in quarters:
        q_data = quarterly_totals.get(quarter, {'total': 0.0})
        total = q_data.get('total', 0.0)

        if show_pondere and 'pondere' in q_data:
            pondere = q_data.get('pondere', 0.0)
            value = f"{total:,.0f}€ / {pondere:,.0f}€"
        else:
            value = f"{total:,.0f}€"

        overall_row[quarter] = value

    overall_headers = ['Metric'] + quarters
    # Use light gray background for this single row to match styling consistency
    overall_colors = {'CA Total': '#f5f5f5'}

    overall_html = create_colored_table_html(overall_headers, [overall_row], overall_colors)
    st.markdown(overall_html, unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # === PER BU SECTION ===
    st.markdown('<h4 style="color: #2c3e50;">💼 CA par Trimestre et par Business Unit</h4>', unsafe_allow_html=True)

    if not quarterly_by_bu:
        st.info("Aucune donnée par Business Unit disponible")
    else:
        # Create per-BU table with colors
        bu_rows = []
        bu_colors = {}

        for bu in BU_ORDER:
            if bu not in quarterly_by_bu:
                continue

            bu_quarters = quarterly_by_bu[bu]
            row = {'Business Unit': bu}
            bu_colors[bu] = BU_COLORS.get(bu, BU_COLORS['AUTRE'])

            for quarter in quarters:
                q_data = bu_quarters.get(quarter, {'total': 0.0})
                total = q_data.get('total', 0.0)

                if show_pondere and 'pondere' in q_data:
                    pondere = q_data.get('pondere', 0.0)
                    row[quarter] = f"{total:,.0f}€ / {pondere:,.0f}€"
                else:
                    row[quarter] = f"{total:,.0f}€"

            bu_rows.append(row)

        # Add any other BUs not in BU_ORDER
        for bu in quarterly_by_bu:
            if bu not in BU_ORDER:
                bu_quarters = quarterly_by_bu[bu]
                row = {'Business Unit': bu}
                bu_colors[bu] = BU_COLORS.get('AUTRE', '#808080')

                for quarter in quarters:
                    q_data = bu_quarters.get(quarter, {'total': 0.0})
                    total = q_data.get('total', 0.0)

                    if show_pondere and 'pondere' in q_data:
                        pondere = q_data.get('pondere', 0.0)
                        row[quarter] = f"{total:,.0f}€ / {pondere:,.0f}€"
                    else:
                        row[quarter] = f"{total:,.0f}€"

                bu_rows.append(row)

        if bu_rows:
            headers = ['Business Unit'] + quarters
            html_table = create_colored_table_html(headers, bu_rows, bu_colors)
            st.markdown(html_table, unsafe_allow_html=True)
        else:
            st.info("Aucune donnée par Business Unit disponible")

    # === NEW: TYPOLOGIES GROUPED BY BU (mapped) ===
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown('<h4 style="color: #2c3e50;">🏷️ Typologies (groupées par BU)</h4>', unsafe_allow_html=True)

    grouped = quarterly_typologies_grouped_by_bu_mapped()
    for bu in BU_ORDER:
        mapped_typs = BU_TO_TYPOLOGIES.get(bu, [])
        if not mapped_typs:
            continue

        bu_block = grouped.get(bu, {})
        if bu_block is None:
            continue

        # Build table rows (always include mapped typologies, even if 0)
        headers = ['Typologie'] + quarters
        rows: List[Dict[str, str]] = []
        for typ in mapped_typs:
            row_out: Dict[str, str] = {'Typologie': typ}
            for q in quarters:
                q_data = bu_block.get(typ, {}).get(q, {'total': 0.0, 'pondere': 0.0})
                if show_pondere:
                    row_out[q] = f"{q_data.get('total', 0.0):,.0f}€ / {q_data.get('pondere', 0.0):,.0f}€"
                else:
                    row_out[q] = f"{q_data.get('total', 0.0):,.0f}€"
            rows.append(row_out)

        # Use BU color as table header
        header_color = BU_COLORS.get(bu, "#1a472a")
        st.markdown(f'<h5 style="color: #2c3e50; margin-top: 10px;">{bu}</h5>', unsafe_allow_html=True)
        st.markdown(create_colored_table_html(headers, rows, row_colors=None, header_color=header_color), unsafe_allow_html=True)


# =============================================================================
# CHART FUNCTIONS
# =============================================================================

def plot_bu_donut(df: pd.DataFrame, title: str = "Répartition par BU", value_col: str = "amount", show_count: bool = True) -> go.Figure:
    """Create a vertical bar chart of revenue by BU with CA values and project counts displayed."""
    if df.empty or 'cf_bu' not in df.columns:
        return go.Figure()

    # Aggregate by BU
    bu_data = df.groupby('cf_bu')[value_col].sum().reset_index()

    # Add count
    bu_counts = df.groupby('cf_bu').size().reset_index(name='count')
    bu_data = bu_data.merge(bu_counts, on='cf_bu', how='left')

    bu_data = bu_data[bu_data[value_col] > 0].sort_values(value_col, ascending=False)

    # Map colors
    colors = [BU_COLORS.get(bu, BU_COLORS['AUTRE']) for bu in bu_data['cf_bu']]

    # Labels with counts
    if show_count:
        labels = [f"{row['cf_bu']} ({int(row['count'])})" for _, row in bu_data.iterrows()]
    else:
        labels = bu_data['cf_bu'].tolist()

    fig = go.Figure(go.Bar(
        x=labels,
        y=bu_data[value_col],
        marker_color=colors,
        text=[f"{v:,.0f}€" for v in bu_data[value_col]],
        textposition='outside'
    ))

    fig.update_layout(
        title=dict(text=title, x=0.5),
        xaxis_title="",
        yaxis_title="Montant (€)",
        showlegend=False,
        margin=dict(t=60, b=60, l=60, r=40),
        height=400
    )

    return fig


def plot_typologie_donut(df: pd.DataFrame, title: str = "Répartition par Typologie", value_col: str = "amount", show_count: bool = True) -> go.Figure:
    """Create a vertical bar chart of revenue by typologie using new allocation logic."""
    if df.empty or 'cf_typologie_de_devis' not in df.columns:
        return go.Figure()

    # Use new allocation logic
    type_amounts = get_typologie_amounts(df, include_weighted=(value_col == 'amount_pondere'))

    # Convert to DataFrame for plotting
    type_data = pd.DataFrame([
        {'cf_typologie_de_devis': typ, value_col: data['total'], 'count': data['count']}
        for typ, data in type_amounts.items()
    ])

    type_data = type_data[type_data[value_col] > 0].sort_values(value_col, ascending=False)

    # Use consistent color palette for typologies (gray for unknown)
    colors = [TYPOLOGIE_COLORS.get(row['cf_typologie_de_devis'], TYPOLOGIE_DEFAULT_COLOR) for _, row in type_data.iterrows()]

    # Labels with counts
    if show_count:
        labels = [f"{row['cf_typologie_de_devis']} ({int(row['count'])})" for _, row in type_data.iterrows()]
    else:
        labels = type_data['cf_typologie_de_devis'].tolist()

    fig = go.Figure(go.Bar(
        x=labels,
        y=type_data[value_col],
        marker_color=colors,
        text=[f"{v:,.0f}€" for v in type_data[value_col]],
        textposition='outside'
    ))

    fig.update_layout(
        title=dict(text=title, x=0.5),
        xaxis_title="",
        yaxis_title="Montant (€)",
        showlegend=False,
        margin=dict(t=60, b=80, l=60, r=40),
        height=400
    )

    return fig


def plot_monthly_stacked_bar_bu(df: pd.DataFrame, year: int, title: str = "Évolution mensuelle par BU") -> go.Figure:
    """
    Create a stacked bar chart by month and BU with trend lines.

    - Bars: Stacked by BU (colored) showing monthly CA
    - Line 1: Monthly total (sum of all BUs)
    - Line 2: Cumulative total
    - Line 3: Average (horizontal reference)
    """
    monthly_data = get_monthly_data(df, include_weighted=False)

    if monthly_data.empty:
        return go.Figure()

    fig = go.Figure()

    # Add stacked bars for each BU
    months_in_data = monthly_data['month'].unique()

    for bu in BU_ORDER:
        bu_data = monthly_data[monthly_data['bu'] == bu]
        if not bu_data.empty:
            fig.add_trace(go.Bar(
                name=bu,
                x=bu_data['month'],
                y=bu_data['amount'],
                marker_color=BU_COLORS.get(bu, BU_COLORS['AUTRE']),
                text=[f"{v:,.0f}€" for v in bu_data['amount']],
                textposition='inside'
            ))

    # Calculate monthly totals
    monthly_totals = monthly_data.groupby(['month_num', 'month'])['amount'].sum().reset_index()
    monthly_totals = monthly_totals.sort_values('month_num')

    # Calculate cumulative
    monthly_totals['cumulative'] = monthly_totals['amount'].cumsum()

    # Calculate average
    avg_amount = monthly_totals['amount'].mean()

    # Add Monthly Total line
    fig.add_trace(go.Scatter(
        name='Total Mensuel',
        x=monthly_totals['month'],
        y=monthly_totals['amount'],
        mode='lines+markers+text',
        line=dict(color='#1a472a', width=3),
        marker=dict(size=8),
        text=[f"{v:,.0f}€" for v in monthly_totals['amount']],
        textposition='top center'
    ))

    # Add Cumulative line
    fig.add_trace(go.Scatter(
        name='Cumul',
        x=monthly_totals['month'],
        y=monthly_totals['cumulative'],
        mode='lines+markers',
        line=dict(color='#ff6b6b', width=2, dash='dash'),
        marker=dict(size=6),
        yaxis='y2'
    ))

    # Add Average line
    fig.add_trace(go.Scatter(
        name=f'Moyenne ({avg_amount:,.0f}€)',
        x=monthly_totals['month'],
        y=[avg_amount] * len(monthly_totals),
        mode='lines',
        line=dict(color='#4ecdc4', width=2, dash='dot')
    ))

    fig.update_layout(
        title=dict(text=f"{title} - {year}", x=0.5),
        barmode='stack',
        xaxis_title="",
        yaxis_title="Montant (€)",
        yaxis2=dict(
            title="Cumul (€)",
            overlaying='y',
            side='right',
            showgrid=False
        ),
        legend=dict(orientation="h", yanchor="bottom", y=-0.3),
        margin=dict(t=60, b=100, l=60, r=60),
        height=500
    )

    return fig


def plot_monthly_stacked_bar_typologie(df: pd.DataFrame, year: int, title: str = "Évolution mensuelle par Typologie", top_n: int = 6) -> go.Figure:
    """
    Create a stacked bar chart by month and top N typologies with trend lines.

    - Bars: Stacked by Typologie (colored) showing monthly CA
    - Line 1: Monthly total (sum of top N typologies)
    - Line 2: Cumulative total
    - Line 3: Average (horizontal reference)
    """
    monthly_data, top_typologies = get_monthly_data_by_typologie(df, top_n=top_n, include_weighted=False)

    if monthly_data.empty or not top_typologies:
        return go.Figure()

    fig = go.Figure()

    # Add stacked bars for each top typologie
    for i, typ in enumerate(top_typologies):
        typ_data = monthly_data[monthly_data['typologie'] == typ]
        if not typ_data.empty:
            color = TYPOLOGIE_COLORS.get(typ, TYPOLOGIE_DEFAULT_COLOR)
            fig.add_trace(go.Bar(
                name=typ,
                x=typ_data['month'],
                y=typ_data['amount'],
                marker_color=color,
                text=[f"{v:,.0f}€" for v in typ_data['amount']],
                textposition='inside'
            ))

    # Calculate monthly totals (for top typologies only)
    monthly_totals = monthly_data.groupby(['month_num', 'month'])['amount'].sum().reset_index()
    monthly_totals = monthly_totals.sort_values('month_num')

    # Calculate cumulative
    monthly_totals['cumulative'] = monthly_totals['amount'].cumsum()

    # Calculate average
    avg_amount = monthly_totals['amount'].mean()

    # Add Monthly Total line
    fig.add_trace(go.Scatter(
        name='Total Mensuel',
        x=monthly_totals['month'],
        y=monthly_totals['amount'],
        mode='lines+markers+text',
        line=dict(color='#1a472a', width=3),
        marker=dict(size=8),
        text=[f"{v:,.0f}€" for v in monthly_totals['amount']],
        textposition='top center'
    ))

    # Add Cumulative line
    fig.add_trace(go.Scatter(
        name='Cumul',
        x=monthly_totals['month'],
        y=monthly_totals['cumulative'],
        mode='lines+markers',
        line=dict(color='#ff6b6b', width=2, dash='dash'),
        marker=dict(size=6),
        yaxis='y2'
    ))

    # Add Average line
    fig.add_trace(go.Scatter(
        name=f'Moyenne ({avg_amount:,.0f}€)',
        x=monthly_totals['month'],
        y=[avg_amount] * len(monthly_totals),
        mode='lines',
        line=dict(color='#4ecdc4', width=2, dash='dot')
    ))

    fig.update_layout(
        title=dict(text=f"{title} - {year}", x=0.5),
        barmode='stack',
        xaxis_title="",
        yaxis_title="Montant (€)",
        yaxis2=dict(
            title="Cumul (€)",
            overlaying='y',
            side='right',
            showgrid=False
        ),
        legend=dict(orientation="h", yanchor="bottom", y=-0.3),
        margin=dict(t=60, b=100, l=60, r=60),
        height=500
    )

    return fig


def plot_sent_pondere_bar(df: pd.DataFrame, year: int, title: str = "Envoyé: Total vs Pondéré") -> go.Figure:
    """
    Create a stacked bar chart showing total vs pondéré amounts.

    - Stacked bars: base = pondéré, top = (total - pondéré)
    """
    if df.empty:
        return go.Figure()

    # Calculate weighted amounts
    df = calculate_weighted_amount(df)
    monthly_data = get_monthly_data(df, include_weighted=True)

    if monthly_data.empty:
        return go.Figure()

    fig = go.Figure()

    # Aggregate by month (all BUs combined)
    monthly_agg = monthly_data.groupby(['month_num', 'month']).agg({
        'amount': 'sum',
        'amount_pondere': 'sum'
    }).reset_index().sort_values('month_num')

    # Calculate the "non-pondéré" portion (total - pondéré)
    monthly_agg['non_pondere'] = monthly_agg['amount'] - monthly_agg['amount_pondere']

    # Pondéré (base)
    fig.add_trace(go.Bar(
        name='Pondéré',
        x=monthly_agg['month'],
        y=monthly_agg['amount_pondere'],
        marker_color='#2d5a3f',
        text=[f"{v:,.0f}€" for v in monthly_agg['amount_pondere']],
        textposition='inside'
    ))

    # Non-pondéré (top)
    fig.add_trace(go.Bar(
        name='Non Pondéré',
        x=monthly_agg['month'],
        y=monthly_agg['non_pondere'],
        marker_color='#a8d5ba',
        text=[f"{v:,.0f}€" for v in monthly_agg['non_pondere']],
        textposition='inside'
    ))

    # Add total line
    fig.add_trace(go.Scatter(
        name='Total Envoyé',
        x=monthly_agg['month'],
        y=monthly_agg['amount'],
        mode='lines+markers+text',
        line=dict(color='#1a472a', width=3),
        marker=dict(size=8),
        text=[f"{v:,.0f}€" for v in monthly_agg['amount']],
        textposition='top center'
    ))

    # Add pondéré total line
    fig.add_trace(go.Scatter(
        name='Total Pondéré',
        x=monthly_agg['month'],
        y=monthly_agg['amount_pondere'],
        mode='lines+markers',
        line=dict(color='#ff6b6b', width=2, dash='dash'),
        marker=dict(size=6)
    ))

    fig.update_layout(
        title=dict(text=f"{title} - {year}", x=0.5),
        barmode='stack',
        xaxis_title="",
        yaxis_title="Montant (€)",
        legend=dict(orientation="h", yanchor="bottom", y=-0.3),
        margin=dict(t=60, b=100, l=60, r=60),
        height=450
    )

    return fig


def plot_typologie_bar(df: pd.DataFrame, title: str = "Répartition par Typologie", show_count: bool = True) -> go.Figure:
    """Create a vertical bar chart by typologie using new allocation logic."""
    if df.empty or 'cf_typologie_de_devis' not in df.columns:
        return go.Figure()

    # Use new allocation logic
    type_amounts = get_typologie_amounts(df, include_weighted=False)

    # Convert to DataFrame for plotting
    type_data = pd.DataFrame([
        {'cf_typologie_de_devis': typ, 'amount': data['total'], 'count': data['count']}
        for typ, data in type_amounts.items()
    ])

    type_data = type_data[type_data['amount'] > 0].sort_values('amount', ascending=False)

    # Use consistent color palette for typologies (gray for unknown)
    colors = [TYPOLOGIE_COLORS.get(row['cf_typologie_de_devis'], TYPOLOGIE_DEFAULT_COLOR) for _, row in type_data.iterrows()]

    # Create labels with count
    if show_count:
        labels = [f"{row['cf_typologie_de_devis']} ({int(row['count'])})" for _, row in type_data.iterrows()]
    else:
        labels = type_data['cf_typologie_de_devis'].tolist()

    fig = go.Figure(go.Bar(
        x=labels,
        y=type_data['amount'],
        marker_color=colors,
        text=[f"{v:,.0f}€" for v in type_data['amount']],
        textposition='outside'
    ))

    fig.update_layout(
        title=dict(text=title, x=0.5),
        xaxis_title="",
        yaxis_title="Montant (€)",
        showlegend=False,
        margin=dict(t=60, b=80, l=60, r=40),
        height=400
    )

    return fig


def plot_bu_bar(df: pd.DataFrame, title: str = "Montant par BU", show_count: bool = True) -> go.Figure:
    """Create a vertical bar chart by BU with project counts."""
    if df.empty or 'cf_bu' not in df.columns:
        return go.Figure()

    # Aggregate by BU
    bu_data = df.groupby('cf_bu').agg({
        'amount': 'sum'
    }).reset_index()

    # Add count
    bu_counts = df.groupby('cf_bu').size().reset_index(name='count')
    bu_data = bu_data.merge(bu_counts, on='cf_bu', how='left')

    bu_data = bu_data[bu_data['amount'] > 0].sort_values('amount', ascending=False)

    # Map colors
    colors = [BU_COLORS.get(bu, BU_COLORS['AUTRE']) for bu in bu_data['cf_bu']]

    # Labels with counts
    if show_count:
        labels = [f"{row['cf_bu']} ({int(row['count'])})" for _, row in bu_data.iterrows()]
    else:
        labels = bu_data['cf_bu'].tolist()

    fig = go.Figure(go.Bar(
        x=labels,
        y=bu_data['amount'],
        marker_color=colors,
        text=[f"{v:,.0f}€" for v in bu_data['amount']],
        textposition='outside'
    ))

    fig.update_layout(
        title=dict(text=title, x=0.5),
        xaxis_title="",
        yaxis_title="Montant (€)",
        showlegend=False,
        margin=dict(t=60, b=60, l=60, r=40),
        height=400
    )

    return fig




# =============================================================================
# MAIN APPLICATION
# =============================================================================

def style_objectives_df(df: pd.DataFrame):
    """Apply conditional formatting to objectives DataFrame."""
    def color_reste(val):
        try:
            num = float(val.replace('€', '').replace(',', ''))
            color = 'color: #27ae60;' if num <= 0 else 'color: #e74c3c;'
            return color
        except:
            return ''

    def color_percent(val):
        try:
            num = float(val.replace('%', ''))
            color = 'color: #27ae60;' if num >= 100 else 'color: #e74c3c;'
            return color
        except:
            return ''

    return df.style.applymap(color_reste, subset=['Reste']).applymap(color_percent, subset=['%'])


# =============================================================================
# HELPER FUNCTIONS FOR DONNÉES DÉTAILLÉES TAB
# =============================================================================

def parse_sheet_month_year(sheet_name: str) -> Tuple[Optional[int], Optional[int]]:
    """
    Parse month and year from sheet name.

    Handles formats:
    - "Envoyé Janvier 2026" -> (1, 2026)
    - "Signé Mars 2025" -> (3, 2025)
    - "État au 15-01-2026" -> (1, 2026)

    Args:
        sheet_name: Name of the sheet

    Returns:
        Tuple of (month, year) or (None, None) if parsing fails
    """
    if not sheet_name:
        return None, None

    # Reverse lookup for month names (month number -> month name)
    month_name_to_num = {v.lower(): k for k, v in MONTH_MAP.items()}

    # Try to parse "Envoyé {Month} {Year}" or "Signé {Month} {Year}"
    for month_name, month_num in month_name_to_num.items():
        if month_name.lower() in sheet_name.lower():
            # Extract year (4 digits)
            year_match = re.search(r'\b(20\d{2})\b', sheet_name)
            if year_match:
                year = int(year_match.group(1))
                return month_num, year

    # Try to parse "État au DD-MM-YYYY"
    if "État au" in sheet_name or "Etat au" in sheet_name:
        # Extract date part after "État au " or "Etat au "
        date_match = re.search(r'(?:État au|Etat au)\s+(\d{2}-\d{2}-\d{4})', sheet_name)
        if date_match:
            try:
                date_str = date_match.group(1)
                dt = datetime.strptime(date_str, "%d-%m-%Y")
                return dt.month, dt.year
            except ValueError:
                pass

    return None, None


def get_available_months_from_sheets(df: pd.DataFrame) -> List[Tuple[int, int]]:
    """
    Extract available (month, year) tuples from source_sheet column.

    Args:
        df: DataFrame with source_sheet column

    Returns:
        List of (month, year) tuples, sorted by year then month
    """
    if df.empty or 'source_sheet' not in df.columns:
        return []

    months_years = set()
    for sheet in df['source_sheet'].unique():
        month, year = parse_sheet_month_year(str(sheet))
        if month is not None and year is not None:
            months_years.add((month, year))

    # Sort by year then month
    return sorted(months_years, key=lambda x: (x[1], x[0]))


def get_available_quarters_from_sheets(df: pd.DataFrame) -> List[Tuple[int, int]]:
    """
    Extract available (quarter, year) tuples from source_sheet column.

    Args:
        df: DataFrame with source_sheet column

    Returns:
        List of (quarter, year) tuples, sorted by year then quarter
    """
    if df.empty or 'source_sheet' not in df.columns:
        return []

    quarters_years = set()
    for sheet in df['source_sheet'].unique():
        month, year = parse_sheet_month_year(str(sheet))
        if month is not None and year is not None:
            quarter = get_quarter_for_month(month)
            quarters_years.add((quarter, year))

    # Sort by year then quarter
    return sorted(quarters_years, key=lambda x: (x[1], x[0]))


def main():
    """Main dashboard application."""
    # #region agent log - Track script reruns
    run_id = increment_run()
    start_time = time.time()
    debug_log("app.py:main:START", f"Script rerun #{run_id}", {"run_id": run_id}, "A")
    # #endregion


    # Header
    st.markdown('<h1 class="main-header">🌿 Dashboard Commercial</h1>', unsafe_allow_html=True)
    st.markdown("*Commercial tracking & forecasting for Merci Raymond*")
    st.markdown("---")

    # Sidebar
    with st.sidebar:
        st.markdown('<h2 style="color: #1a472a; font-weight: 700; margin-bottom: 1rem;">🌿 Merci Raymond</h2>', unsafe_allow_html=True)
        st.markdown("### Filtres")

        # Year selection
        current_year = datetime.now().year
        years_available = list(range(current_year - 2, current_year + 2))
        selected_year = st.selectbox(
            "Année",
            years_available,
            index=years_available.index(current_year)
        )
        # #region agent log - Track sidebar selections
        debug_log("sidebar:YEAR_SELECTED", f"Year: {selected_year}", {"year": selected_year}, "D")
        # #endregion

        # View type
        view_type = st.radio(
            "Type de vue",
            ["Signé (Won)", "Envoyé (Sent)", "État actuel (Snapshot)"],
            index=0
        )
        # #region agent log
        debug_log("sidebar:VIEW_TYPE_SELECTED", f"View: {view_type}", {"view_type": view_type}, "D")
        # #endregion

        st.markdown("---")

        # Refresh button
        if st.button("🔄 Actualiser les données"):
            # Clear all cached data
            st.cache_data.clear()
            st.cache_resource.clear()
            st.rerun()

        st.markdown("---")
        st.markdown("##### Dernière mise à jour")
        st.markdown(f"*{datetime.now().strftime('%d/%m/%Y %H:%M')}*")

    # Determine view configuration
    is_signed = "Signé" in view_type
    is_sent = "Envoyé" in view_type
    show_pondere = is_sent or "État" in view_type  # Show pondéré for non-signed views

    # #region agent log - Track main data loading
    data_load_start = time.time()
    debug_log("main:DATA_LOAD:START", f"Loading data for {view_type}",
              {"view_type": view_type, "year": selected_year}, "B")
    # #endregion

    # Load data
    if is_signed:
        sheet_type = "Signé"
        df = load_year_data(selected_year, sheet_type)
    elif is_sent:
        sheet_type = "Envoyé"
        df = load_year_data(selected_year, sheet_type)
    else:
        # Load latest snapshot
        current_year = datetime.now().year
        client = get_sheets_client()
        snapshot_sheets = client.list_worksheets(view_type="etat", year=current_year)

        # Prefer the stable daily snapshot sheet (overwritten daily)
        if "État actuel" in snapshot_sheets:
            df = load_worksheet_data("État actuel", view_type="etat", year=current_year)
        else:
            # Fallback to the most recent "État au DD-MM-YYYY" snapshot.
            # Important: do NOT rely on lexicographic sort (DD-MM-YYYY doesn't sort correctly).
            etat_au_sheets = [s for s in snapshot_sheets if "État au" in s]
            best_name = None
            best_dt = None
            for name in etat_au_sheets:
                try:
                    # Expected format: "État au DD-MM-YYYY"
                    dt = datetime.strptime(name.replace("État au ", "").strip(), "%d-%m-%Y")
                except Exception:
                    continue
                if best_dt is None or dt > best_dt:
                    best_dt = dt
                    best_name = name

            if best_name:
                df = load_worksheet_data(best_name, view_type="etat", year=current_year)
            else:
                df = pd.DataFrame()

    # #region agent log
    debug_log("main:DATA_LOAD:DONE", f"Loaded {len(df)} rows in {time.time()-data_load_start:.2f}s",
              {"rows": len(df), "duration_s": round(time.time()-data_load_start, 2)}, "B")
    # #endregion

    # Parse and enhance data
    df = parse_numeric_columns(df)
    df = calculate_weighted_amount(df)

    if df.empty:
        st.warning(f"Aucune donnée disponible pour {selected_year}")
        return

    # Debug info (expandable in sidebar)
    #with st.sidebar.expander("🔍 Debug: Loaded Sheets"):
        #if 'source_sheet' in df.columns:
          #  loaded_sheets = df['source_sheet'].unique().tolist()
          #  st.write(f"**Sheets loaded:** {len(loaded_sheets)}")
           # for sheet in sorted(loaded_sheets):
            #    count = len(df[df['source_sheet'] == sheet])
           #     st.write(f"- {sheet}: {count} rows")
       # else:
         #   st.write("No sheet information available")

    # Calculate global metrics
    total_amount = df['amount'].sum()
    total_pondere = df['amount_pondere'].sum() if 'amount_pondere' in df.columns else total_amount
    total_count = len(df)
    bu_amounts = get_bu_amounts(df, include_weighted=show_pondere)

    # Monthly stats (using 11-period accounting: July+August count as one period)
    if 'source_sheet' in df.columns:
        # Extract month numbers from source_sheet names
        month_numbers = []
        for sheet in df['source_sheet'].unique():
            _, month_num = extract_month_from_sheet(sheet)
            if month_num:
                month_numbers.append(month_num)
        # Count unique accounting periods (July+August = one period)
        num_periods = count_unique_accounting_periods(month_numbers) if month_numbers else 1
        monthly_avg = total_amount / num_periods if num_periods > 0 else 0
        num_months = num_periods  # Use for display (shows accounting periods)
    else:
        num_months = 1
        monthly_avg = total_amount

    # Navigation with persistence (fixes tab reset bug)
    # Use both session_state and query_params for stability
    tab_options = ["📈 Vue Globale", "📅 Vue Mensuelle", "🎯 Objectifs", "📋 Données Détaillées"]
    tab_keys = ["globale", "mensuelle", "objectifs", "donnees"]

    # Get initial tab from query_params or session_state, default to first
    if "tab" in st.query_params:
        query_tab = st.query_params["tab"]
        if query_tab in tab_keys:
            default_idx = tab_keys.index(query_tab)
        else:
            default_idx = 0
    elif "main_nav" in st.session_state:
        # Fallback to session_state if query_params not set
        if st.session_state["main_nav"] in tab_keys:
            default_idx = tab_keys.index(st.session_state["main_nav"])
        else:
            default_idx = 0
    else:
        default_idx = 0

    # Create navigation control
    selected_tab_key = st.radio(
        "Navigation",
        options=tab_keys,
        format_func=lambda x: tab_options[tab_keys.index(x)],
        index=default_idx,
        key="main_nav",
        horizontal=True,
        label_visibility="collapsed"
    )

    # Persist to query_params
    if selected_tab_key != st.query_params.get("tab", ""):
        st.query_params["tab"] = selected_tab_key

    # Map to display name for rendering
    selected_tab_display = tab_options[tab_keys.index(selected_tab_key)]

    # =========================================================================
    # TAB 1: VUE GLOBALE
    # =========================================================================
    if selected_tab_key == "globale":
        st.markdown(f"### Vue Globale {selected_year}")

        # === MAIN KPIs ===
        st.markdown('<div class="section-header">📊 Indicateurs Clés</div>', unsafe_allow_html=True)

        if show_pondere:
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                create_kpi_card("CA Total", f"{total_amount:,.0f}€", "💰", "default")
            with col2:
                create_kpi_card("CA Pondéré", f"{total_pondere:,.0f}€", "⚖️", "default")
            with col3:
                create_kpi_card("Nombre de Projets", f"{total_count}", "📁", "default")
                # Add popover for all projects
                if not df.empty:
                    render_projects_popover(
                        "🔎 Voir projets",
                        df,
                        show_pondere=show_pondere,
                        header_text=f"Tous les projets · {selected_year}"
                    )
            with col4:
                create_kpi_card("Moyenne mensuelle", f"{monthly_avg:,.0f}€", "📅", "default")
        else:
            col1, col2, col3 = st.columns(3)
            with col1:
                create_kpi_card("CA Total", f"{total_amount:,.0f}€", "💰", "default")
            with col2:
                create_kpi_card("Nombre de Projets", f"{total_count}", "📁", "default")
                # Add popover for all projects
                if not df.empty:
                    render_projects_popover(
                        "🔎 Voir projets",
                        df,
                        show_pondere=show_pondere,
                        header_text=f"Tous les projets · {selected_year}"
                    )
            with col3:
                create_kpi_card("Moyenne mensuelle", f"{monthly_avg:,.0f}€", "📅", "default")

        st.markdown("<br>", unsafe_allow_html=True)

        # === BU TOTALS ===
        st.markdown('<div class="section-header">💼 Montants par Business Unit</div>', unsafe_allow_html=True)
        create_bu_kpi_row(df, bu_amounts, show_pondere=show_pondere, key_prefix="vue_globale")

        st.markdown("<br>", unsafe_allow_html=True)

        # === TYPOLOGIE TOTALS (BU-Grouped) ===
        st.markdown('<div class="section-header">🏷️ Montants par Typologie (groupés par BU)</div>', unsafe_allow_html=True)
        create_bu_grouped_typologie_blocks(df, show_pondere=show_pondere)

        # Monthly average per BU
        st.markdown("<br>", unsafe_allow_html=True)
        if num_months > 1:
            st.markdown(f"**Moyenne mensuelle:** {monthly_avg:,.0f}€ (sur {num_months} mois)")

        st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)

        # === SECTION: BY BU ===
        st.markdown('<div class="section-header">📊 Analyse par Business Unit</div>', unsafe_allow_html=True)

        # Vertical layout for charts
        fig_bu_donut = plot_bu_donut(df, "Répartition par BU")
        st.plotly_chart(fig_bu_donut, use_container_width=True, key="vue_globale_bu_donut", config={})

        st.markdown("<br>", unsafe_allow_html=True)
        if 'source_sheet' in df.columns:
            fig_monthly_bu = plot_monthly_stacked_bar_bu(df, selected_year, "Évolution mensuelle par BU")
            st.plotly_chart(fig_monthly_bu, use_container_width=True, key="vue_globale_monthly_bu", config={})
        else:
            fig_bu_bar = plot_bu_bar(df, "Montant par BU")
            st.plotly_chart(fig_bu_bar, use_container_width=True, key="vue_globale_bu_bar", config={})

        # Sent/Pondéré chart for non-signed views
        if show_pondere and 'source_sheet' in df.columns:
            st.markdown("<br>", unsafe_allow_html=True)
            st.markdown("#### Total vs Pondéré par mois")
            fig_pondere = plot_sent_pondere_bar(df, selected_year)
            st.plotly_chart(fig_pondere, use_container_width=True, key="vue_globale_pondere", config={})

        st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)

        # === SECTION: BY TYPOLOGIE ===
        st.markdown('<div class="section-header">🏷️ Analyse par Typologie</div>', unsafe_allow_html=True)

        # Vertical layout for charts
        fig_type_donut = plot_typologie_donut(df, "Répartition par Typologie")
        st.plotly_chart(fig_type_donut, use_container_width=True, key="vue_globale_type_donut", config={})

        st.markdown("<br>", unsafe_allow_html=True)
        # Use stacked bar chart like BU section - shows top 6 typologies monthly evolution
        if 'source_sheet' in df.columns:
            fig_type_stacked = plot_monthly_stacked_bar_typologie(df, selected_year, "Évolution mensuelle par Typologie", top_n=6)
            st.plotly_chart(fig_type_stacked, use_container_width=True, key="vue_globale_monthly_type", config={})
        else:
            fig_type_bar = plot_typologie_bar(df, "Détail par Typologie")
            st.plotly_chart(fig_type_bar, use_container_width=True, key="vue_globale_type_bar", config={})

        # === SECTION: À PRODUIRE TABS ===
        st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)
        st.markdown('<div class="section-header">🏭 Répartition par Année de Production</div>', unsafe_allow_html=True)
        #st.markdown("*Montants à produire par année basés sur les règles de revenue spreading*")

        # Render production tabs for Vue Globale
        # For Signé view: show total amounts only
        # For Envoyé/État views: show both Total and Pondéré
        render_production_tabs(
            df=df,
            selected_year=selected_year,
            show_pondere=show_pondere,
            key_prefix="vue_globale"
        )


    # =========================================================================
    # TAB 2: VUE MENSUELLE
    # =========================================================================
    elif selected_tab_key == "mensuelle":
        st.markdown(f"### Vue Mensuelle {selected_year}")
        # #region agent log - Track Vue Mensuelle rendering
        debug_log("tab2:VUE_MENSUELLE:ENTER", "Entering Vue Mensuelle tab", {}, "C")
        # #endregion

        if "source_sheet" in df.columns:
            months_available = df['source_sheet'].unique().tolist()
            # #region agent log - Track month selection
            debug_log("tab2:MONTH_SELECTOR:BEFORE", f"Month selector with {len(months_available)} options",
                      {"months": months_available}, "D")
            # #endregion
            selected_month = st.selectbox("Sélectionner un mois", months_available, key="month_selector")
            # #region agent log
            debug_log("tab2:MONTH_SELECTOR:AFTER", f"Selected month: {selected_month}",
                      {"selected": selected_month}, "D")
            # #endregion

            if selected_month:
                # #region agent log - Track month data filtering
                filter_start = time.time()
                debug_log("tab2:FILTER_MONTH:START", f"Filtering for {selected_month}",
                          {"selected_month": selected_month, "total_rows_before": len(df)}, "C")
                # #endregion
                month_df = df[df['source_sheet'] == selected_month]
                month_df = parse_numeric_columns(month_df)
                month_df = calculate_weighted_amount(month_df)
                # #region agent log
                debug_log("tab2:FILTER_MONTH:DONE", f"Filtered to {len(month_df)} rows in {time.time()-filter_start:.3f}s",
                          {"rows_after": len(month_df), "duration_ms": round((time.time()-filter_start)*1000, 1)}, "C")
                # #endregion

                # === HEADER KPIs ===
                st.markdown('<div class="section-header">📊 Résumé du Mois</div>', unsafe_allow_html=True)

                month_total = month_df['amount'].sum()
                month_pondere = month_df['amount_pondere'].sum() if 'amount_pondere' in month_df.columns else month_total
                month_count = len(month_df)

                # Calculate average CA per BU for this specific month
                # Include AUTRE in MAINTENANCE to avoid skewed average
                month_bu_amounts = get_bu_amounts(month_df, include_weighted=False)

                # Combine AUTRE into MAINTENANCE for average calculation
                maintenance_total = month_bu_amounts.get('MAINTENANCE', {}).get('total', 0.0)
                autre_total = month_bu_amounts.get('AUTRE', {}).get('total', 0.0)
                maintenance_combined = maintenance_total + autre_total

                # Calculate average across main BUs (CONCEPTION, TRAVAUX, MAINTENANCE+AUTRE)
                conception_total = month_bu_amounts.get('CONCEPTION', {}).get('total', 0.0)
                travaux_total = month_bu_amounts.get('TRAVAUX', {}).get('total', 0.0)

                # Count non-zero BUs for average
                bu_totals = [conception_total, travaux_total, maintenance_combined]
                non_zero_bus = [t for t in bu_totals if t > 0]
                month_avg_per_bu = sum(non_zero_bus) / len(non_zero_bus) if non_zero_bus else 0.0

                if show_pondere:
                    col1, col2, col3, col4 = st.columns(4)
                    with col1:
                        create_kpi_card("CA du mois", f"{month_total:,.0f}€", "💰", "default")
                    with col2:
                        create_kpi_card("CA Pondéré", f"{month_pondere:,.0f}€", "⚖️", "default")
                    with col3:
                        create_kpi_card("Projets", f"{month_count}", "📁", "default")
                        # Add popover for month projects
                        if not month_df.empty:
                            render_projects_popover(
                                "🔎 Voir projets",
                                month_df,
                                show_pondere=show_pondere,
                                header_text=f"Projets · {selected_month}"
                            )
                    with col4:
                        create_kpi_card("Moyenne du mois", f"{month_avg_per_bu:,.0f}€", "📅", "default")
                else:
                    col1, col2, col3 = st.columns(3)
                    with col1:
                        create_kpi_card("CA du mois", f"{month_total:,.0f}€", "💰", "default")
                    with col2:
                        create_kpi_card("Projets", f"{month_count}", "📁", "default")
                        # Add popover for month projects
                        if not month_df.empty:
                            render_projects_popover(
                                "🔎 Voir projets",
                                month_df,
                                show_pondere=show_pondere,
                                header_text=f"Projets · {selected_month}"
                            )
                    with col3:
                        create_kpi_card("Moyenne du mois", f"{month_avg_per_bu:,.0f}€", "📅", "default")

                st.markdown("<br>", unsafe_allow_html=True)

                # === BU AMOUNTS ===
                st.markdown('<div class="section-header">💼 Montants par BU</div>', unsafe_allow_html=True)
                month_bu_amounts = get_bu_amounts(month_df, include_weighted=show_pondere)
                create_bu_kpi_row(month_df, month_bu_amounts, show_pondere=show_pondere, key_prefix=f"vue_mensuelle_{selected_month.replace(' ', '_')}")

                st.markdown("<br>", unsafe_allow_html=True)

                # === TYPOLOGIE AMOUNTS (BU-Grouped) ===
                st.markdown('<div class="section-header">🏷️ Montants par Typologie (groupés par BU)</div>', unsafe_allow_html=True)
                # Use BU-grouped typologie blocks for consistency
                create_bu_grouped_typologie_blocks(month_df, show_pondere=show_pondere)

                st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)

                # === CHARTS ===
                # #region agent log - Track chart rendering
                charts_start = time.time()
                debug_log("tab2:CHARTS:START", "Starting chart rendering", {"month": selected_month}, "C")
                # #endregion

                # Vertical layout for charts
                st.markdown("#### Répartition par Business Unit")
                fig_month_bu_donut = plot_bu_donut(month_df, f"BU - {selected_month}")
                st.plotly_chart(fig_month_bu_donut, use_container_width=True, key="vue_mensuelle_bu_donut", config={})

                st.markdown("<br>", unsafe_allow_html=True)
                st.markdown("#### Répartition par Typologie")
                fig_month_type_bar = plot_typologie_bar(month_df, f"Répartition par Typologie")
                st.plotly_chart(fig_month_type_bar, use_container_width=True, key="vue_mensuelle_type_bar", config={})

                # === SECTION: À PRODUIRE TABS (Monthly) ===
                st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)
                st.markdown('<div class="section-header">🏭 Répartition par Année de Production</div>', unsafe_allow_html=True)
                st.markdown(f"*Montants à produire par année pour les signatures de {selected_month}*")

                # Render production tabs for this month's data only
                # Shows how much of this month's signed deals will be produced in each year
                render_production_tabs(
                    df=month_df,
                    selected_year=selected_year,
                    show_pondere=show_pondere,
                    key_prefix=f"vue_mensuelle_{selected_month.replace(' ', '_')}"
                )

                # #region agent log
                debug_log("tab2:CHARTS:DONE", f"Vue Mensuelle charts completed in {time.time()-charts_start:.2f}s",
                          {"month": selected_month, "duration_s": round(time.time()-charts_start, 2)}, "C")
                # #endregion
        else:
            st.info("Vue mensuelle non disponible pour l'état actuel")

    # =========================================================================
    # TAB 3: OBJECTIFS
    # =========================================================================
    elif selected_tab_key == "objectifs":
        st.markdown("### 🎯 Objectifs")

        # Check if objectives are available for this view type
        if not (is_signed or is_sent):
            # Snapshot view: objectives not available
            st.info("Les objectifs sont disponibles uniquement pour les vues Envoyé et Signé.")
            st.markdown("*Choisissez Envoyé ou Signé dans la barre latérale pour voir les objectifs.*")
        else:
            # Determine which metric to use based on sidebar selection
            if is_signed:
                metric_name = "Signé"
                metric_key = "signe"
                sheet_type = "Signé"
            elif is_sent:
                metric_name = "Envoyé"
                metric_key = "envoye"
                sheet_type = "Envoyé"
            else:
                # This should not happen, but handle gracefully
                st.error("Erreur: type de vue non reconnu pour les objectifs")
                metric_name = None
                metric_key = None
                sheet_type = None

            if metric_name and metric_key and sheet_type:
                # Load production-year aggregated data (includes previous-year signings)
                metric_df = load_aggregated_production_data(production_year=selected_year, sheet_type=sheet_type)

                if metric_df.empty:
                    st.warning(f"Aucune donnée disponible pour les objectifs {metric_name} {selected_year}")
                else:
                    # Parse numeric columns
                    metric_df = parse_numeric_columns(metric_df)
                    # For Envoyé, we use production weighted columns directly (no need to recalculate)
                    use_pondere = (metric_key == "envoye")

                    st.markdown(f"#### Objectifs {metric_name} - Production {selected_year}")
                    st.markdown(f"*Basés sur l'année de production (inclut les signatures des années précédentes)*")

                    # Show carryover breakdown
                    if 'signed_year' in metric_df.columns:
                        with st.expander("📊 Répartition par année de signature", expanded=False):
                            signed_years = sorted(metric_df['signed_year'].unique())
                            carryover_data = []
                            for signed_yr in signed_years:
                                year_df = metric_df[metric_df['signed_year'] == signed_yr]
                                amount_col = f'Montant Pondéré {selected_year}' if use_pondere else f'Montant Total {selected_year}'
                                if amount_col in year_df.columns:
                                    year_total = float(year_df[amount_col].sum() or 0.0)
                                    carryover_data.append({
                                        "Année de signature": signed_yr,
                                        "Montant (€)": year_total,
                                    })

                            if carryover_data:
                                carryover_df = pd.DataFrame(carryover_data)
                                total_production = float(carryover_df["Montant (€)"].sum() or 0.0)
                                carryover_df["%"] = carryover_df["Montant (€)"].apply(
                                    lambda v: f"{(float(v) / total_production * 100) if total_production > 0 else 0:.1f}%"
                                )
                                carryover_df["Montant"] = carryover_df["Montant (€)"].apply(lambda v: f"{float(v):,.0f}€")
                                carryover_df = carryover_df[["Année de signature", "Montant", "%"]]
                                st.dataframe(carryover_df, use_container_width=True, hide_index=True)
                                st.markdown(f"**Total production {selected_year}:** {total_production:,.0f}€")

                    # Period selector - use all accounting periods (0-10)
                    all_periods = list(range(11))  # 0-10 for all accounting periods
                    period_options = [f"{idx:02d} - {get_accounting_period_label(idx)}" for idx in all_periods]

                    current_month_num = datetime.now().month
                    current_period_idx = get_accounting_period_for_month(current_month_num)
                    default_idx = current_period_idx if current_period_idx < len(all_periods) else 0

                    selected_period_str = st.selectbox(
                        "Sélectionner une période",
                        period_options,
                        index=default_idx,
                        key=f"period_select_{metric_key}"
                    )
                    selected_period_idx = all_periods[period_options.index(selected_period_str)]
                    selected_period_label = get_accounting_period_label(selected_period_idx)

                    # =============================================================
                    # SECTION 1: PÉRIODE (Production period)
                    # =============================================================
                    st.markdown("---")
                    st.markdown(f"### 📅 Production de {selected_period_label}")

                    st.markdown(f"*Montants de production {selected_year} pour cette période comptable*")

                    # BU Table
                    st.markdown("#### Par Business Unit")
                    bu_data = []
                    for bu in BU_ORDER:
                        # Realized = production-period amount using quarter columns / 3
                        realized_total, realized_prev = calculate_production_period_with_carryover(
                            metric_df, selected_year, selected_period_idx, "bu", bu, use_pondere
                        )
                        # Objective = sum of objectives for months in this accounting period
                        period_months = get_months_for_accounting_period(selected_period_idx)
                        objective = sum(
                            objective_for_month(selected_year, metric_key, "bu", bu, m)
                            for m in period_months
                        )
                        reste = objective - realized_total
                        percent = (realized_total / objective * 100) if objective > 0 else 0.0

                        # Pure signature for this period (sum of months in period)
                        pure_brut = 0.0
                        pure_pondere = 0.0
                        for m in period_months:
                            brut, pond = calculate_pure_signature_for_month(
                                metric_df, selected_year, m, "bu", bu, use_pondere
                            )
                            pure_brut += brut
                            pure_pondere += pond

                        # Format pure column
                        if use_pondere:
                            pure_display = f"{pure_brut:,.0f}€ / {pure_pondere:,.0f}€"
                        else:
                            pure_display = f"{pure_brut:,.0f}€"

                        bu_data.append({
                            "BU": bu,
                            "Objectif": f"{objective:,.0f}€",
                            "Réalisé": _format_realized_with_carryover(realized_total, realized_prev),
                            "Pur": pure_display,
                            "Reste": f"{reste:,.0f}€",
                            "%": f"{percent:.1f}%"
                        })

                    st.dataframe(style_objectives_df(pd.DataFrame(bu_data)), use_container_width=True, hide_index=True)

                    # Typologie Table
                    st.markdown("#### Par Typologie")
                    typo_data = []
                    for typo in EXPECTED_TYPOLOGIES:
                        realized_total, realized_prev = calculate_production_period_with_carryover(
                            metric_df, selected_year, selected_period_idx, "typologie", typo, use_pondere
                        )
                        period_months = get_months_for_accounting_period(selected_period_idx)
                        objective = sum(
                            objective_for_month(selected_year, metric_key, "typologie", typo, m)
                            for m in period_months
                        )
                        reste = objective - realized_total
                        percent = (realized_total / objective * 100) if objective > 0 else 0.0

                        # Pure signature for this period
                        pure_brut = 0.0
                        pure_pondere = 0.0
                        for m in period_months:
                            brut, pond = calculate_pure_signature_for_month(
                                metric_df, selected_year, m, "typologie", typo, use_pondere
                            )
                            pure_brut += brut
                            pure_pondere += pond

                        # Format pure column
                        if use_pondere:
                            pure_display = f"{pure_brut:,.0f}€ / {pure_pondere:,.0f}€"
                        else:
                            pure_display = f"{pure_brut:,.0f}€"

                        typo_data.append({
                            "Typologie": typo,
                            "Objectif": f"{objective:,.0f}€",
                            "Réalisé": _format_realized_with_carryover(realized_total, realized_prev),
                            "Pur": pure_display,
                            "Reste": f"{reste:,.0f}€",
                            "%": f"{percent:.1f}%"
                        })

                    st.dataframe(style_objectives_df(pd.DataFrame(typo_data)), use_container_width=True, hide_index=True)

                    # =============================================================
                    # SECTION 2: TRIMESTRE (Production year)
                    # =============================================================
                    st.markdown("---")
                    st.markdown("### 📊 Trimestre de Production")

                    # Determine current quarter from selected period
                    period_months = get_months_for_accounting_period(selected_period_idx)
                    if period_months:
                        current_quarter = get_quarter_for_month(period_months[0])
                    else:
                        current_quarter = "Q1"  # Default

                    quarter_start = quarter_start_dates(selected_year)[current_quarter]
                    quarter_end = quarter_end_dates(selected_year)[current_quarter]

                    st.markdown(f"**Trimestre actuel:** {current_quarter} | **Début:** {quarter_start.strftime('%d/%m/%Y')} | **Fin:** {quarter_end.strftime('%d/%m/%Y')}")

                    # BU Table (Quarter) - using production-year columns
                    st.markdown("#### Par Business Unit (Trimestre de production)")
                    bu_quarter_data = []
                    quarter_amount_col = (
                        f"Montant Pondéré {current_quarter}_{selected_year}"
                        if use_pondere
                        else f"Montant Total {current_quarter}_{selected_year}"
                    )
                    for bu in BU_ORDER:
                        realized_total, realized_prev = calculate_production_amount_with_carryover(
                            metric_df, selected_year, quarter_amount_col, "bu", bu
                        )
                        objective = objective_for_quarter(selected_year, metric_key, "bu", bu, current_quarter)
                        reste = objective - realized_total
                        percent = (realized_total / objective * 100) if objective > 0 else 0.0

                        # Pure signature for this quarter
                        pure_brut, pure_pondere = calculate_pure_signature_for_quarter(
                            metric_df, selected_year, current_quarter, "bu", bu, use_pondere
                        )
                        if use_pondere:
                            pure_display = f"{pure_brut:,.0f}€ / {pure_pondere:,.0f}€"
                        else:
                            pure_display = f"{pure_brut:,.0f}€"

                        bu_quarter_data.append({
                            "BU": bu,
                            "Objectif": f"{objective:,.0f}€",
                            "Réalisé": _format_realized_with_carryover(realized_total, realized_prev),
                            "Pur": pure_display,
                            "Reste": f"{reste:,.0f}€",
                            "%": f"{percent:.1f}%"
                        })

                    st.dataframe(style_objectives_df(pd.DataFrame(bu_quarter_data)), use_container_width=True, hide_index=True)

                    # Typologie Table (Quarter)
                    st.markdown("#### Par Typologie (Trimestre de production)")
                    typo_quarter_data = []
                    for typo in EXPECTED_TYPOLOGIES:
                        realized_total, realized_prev = calculate_production_amount_with_carryover(
                            metric_df, selected_year, quarter_amount_col, "typologie", typo
                        )
                        objective = objective_for_quarter(selected_year, metric_key, "typologie", typo, current_quarter)
                        reste = objective - realized_total
                        percent = (realized_total / objective * 100) if objective > 0 else 0.0

                        # Pure signature for this quarter
                        pure_brut, pure_pondere = calculate_pure_signature_for_quarter(
                            metric_df, selected_year, current_quarter, "typologie", typo, use_pondere
                        )
                        if use_pondere:
                            pure_display = f"{pure_brut:,.0f}€ / {pure_pondere:,.0f}€"
                        else:
                            pure_display = f"{pure_brut:,.0f}€"

                        typo_quarter_data.append({
                            "Typologie": typo,
                            "Objectif": f"{objective:,.0f}€",
                            "Réalisé": _format_realized_with_carryover(realized_total, realized_prev),
                            "Pur": pure_display,
                            "Reste": f"{reste:,.0f}€",
                            "%": f"{percent:.1f}%"
                        })

                    st.dataframe(style_objectives_df(pd.DataFrame(typo_quarter_data)), use_container_width=True, hide_index=True)

                    # =============================================================
                    # SECTION 3: ANNÉE (Production year)
                    # =============================================================
                    st.markdown("---")
                    st.markdown("### 📈 Année de Production")

                    # BU Table (Year) - using production-year columns
                    st.markdown("#### Par Business Unit (Année de production)")
                    bu_year_data = []
                    year_amount_col = (
                        f"Montant Pondéré {selected_year}"
                        if use_pondere
                        else f"Montant Total {selected_year}"
                    )
                    for bu in BU_ORDER:
                        realized_total, realized_prev = calculate_production_amount_with_carryover(
                            metric_df, selected_year, year_amount_col, "bu", bu
                        )
                        objective = objective_for_year(selected_year, metric_key, "bu", bu)
                        reste = objective - realized_total
                        percent = (realized_total / objective * 100) if objective > 0 else 0.0

                        # Pure signature for this year
                        pure_brut, pure_pondere = calculate_pure_signature_for_year(
                            metric_df, selected_year, "bu", bu, use_pondere
                        )
                        if use_pondere:
                            pure_display = f"{pure_brut:,.0f}€ / {pure_pondere:,.0f}€"
                        else:
                            pure_display = f"{pure_brut:,.0f}€"

                        bu_year_data.append({
                            "BU": bu,
                            "Objectif": f"{objective:,.0f}€",
                            "Réalisé": _format_realized_with_carryover(realized_total, realized_prev),
                            "Pur": pure_display,
                            "Reste": f"{reste:,.0f}€",
                            "%": f"{percent:.1f}%"
                        })

                    st.dataframe(style_objectives_df(pd.DataFrame(bu_year_data)), use_container_width=True, hide_index=True)

                    # Typologie Table (Year)
                    st.markdown("#### Par Typologie (Année de production)")
                    typo_year_data = []
                    for typo in EXPECTED_TYPOLOGIES:
                        realized_total, realized_prev = calculate_production_amount_with_carryover(
                            metric_df, selected_year, year_amount_col, "typologie", typo
                        )
                        objective = objective_for_year(selected_year, metric_key, "typologie", typo)
                        reste = objective - realized_total
                        percent = (realized_total / objective * 100) if objective > 0 else 0.0

                        # Pure signature for this year
                        pure_brut, pure_pondere = calculate_pure_signature_for_year(
                            metric_df, selected_year, "typologie", typo, use_pondere
                        )
                        if use_pondere:
                            pure_display = f"{pure_brut:,.0f}€ / {pure_pondere:,.0f}€"
                        else:
                            pure_display = f"{pure_brut:,.0f}€"

                        typo_year_data.append({
                            "Typologie": typo,
                            "Objectif": f"{objective:,.0f}€",
                            "Réalisé": _format_realized_with_carryover(realized_total, realized_prev),
                            "Pur": pure_display,
                            "Reste": f"{reste:,.0f}€",
                            "%": f"{percent:.1f}%"
                        })

                    st.dataframe(style_objectives_df(pd.DataFrame(typo_year_data)), use_container_width=True, hide_index=True)

                    # =============================================================
                    # LINE CHARTS
                    # =============================================================
                    st.markdown("---")
                    st.markdown("### 📉 Évolution Mensuelle")

                    # Checkbox to show/hide pure signature lines
                    show_pure_lines = st.checkbox(
                        "Afficher les courbes Pur (brut/pondéré)",
                        value=False,
                        key=f"show_pure_{metric_key}"
                    )

                    # Vertical layout for charts
                    st.markdown("#### Par Business Unit")
                    bu_options = ["Toutes les BUs"] + BU_ORDER
                    selected_bu_display = st.selectbox(
                        "Sélectionner un BU",
                        bu_options,
                        key=f"bu_select_{metric_key}"
                    )
                    selected_bu_key = "all" if selected_bu_display == "Toutes les BUs" else selected_bu_display

                    fig_bu = plot_objectives_line_chart(
                        selected_year, metric_key, "bu", selected_bu_key, metric_df,
                        use_pondere=use_pondere, show_pure=show_pure_lines
                    )
                    st.plotly_chart(fig_bu, use_container_width=True, key=f"obj_bu_chart_{metric_key}")

                    st.markdown("<br>", unsafe_allow_html=True)
                    st.markdown("#### Par Typologie")
                    typo_options = ["Toutes les Typologies"] + EXPECTED_TYPOLOGIES
                    selected_typo_display = st.selectbox(
                        "Sélectionner une Typologie",
                        typo_options,
                        key=f"typo_select_{metric_key}"
                    )
                    selected_typo_key = "all" if selected_typo_display == "Toutes les Typologies" else selected_typo_display

                    fig_typo = plot_objectives_line_chart(
                        selected_year, metric_key, "typologie", selected_typo_key, metric_df,
                        use_pondere=use_pondere, show_pure=show_pure_lines
                    )
                    st.plotly_chart(fig_typo, use_container_width=True, key=f"obj_typo_chart_{metric_key}")

    # =========================================================================
    # TAB 4: DONNÉES DÉTAILLÉES
    # =========================================================================
    elif selected_tab_key == "donnees":
        st.markdown("### Données Détaillées")

        # Filters
        col1, col2, col3 = st.columns(3)

        with col1:
            bu_filter = []
            if 'cf_bu' in df.columns:
                bu_filter = st.multiselect(
                    "Filtrer par BU",
                    options=df['cf_bu'].unique().tolist(),
                    default=[],
                    key="bu_filter"
                )

        with col2:
            # Time period filter
            time_filter_type = st.radio(
                "Période",
                ["Toute l'année", "Par mois", "Par trimestre"],
                key="time_filter_type",
                horizontal=True
            )

            selected_month = None
            selected_quarter = None

            if time_filter_type == "Par mois":
                # Get available months from source_sheet
                available_months = get_available_months_from_sheets(df)
                if available_months:
                    # Filter to selected year
                    year_months = [(m, y) for m, y in available_months if y == selected_year]
                    if year_months:
                        # Create display labels
                        month_labels = [f"{MONTH_MAP.get(m, '')} {y}" for m, y in year_months]
                        month_options = list(range(len(month_labels)))
                        selected_idx = st.selectbox(
                            "Mois",
                            month_options,
                            format_func=lambda i: month_labels[i],
                            key="month_filter"
                        )
                        selected_month = year_months[selected_idx][0] if selected_idx < len(year_months) else None
                    else:
                        st.info(f"Aucun mois disponible pour {selected_year}")
                else:
                    st.info("Aucun mois disponible dans les données")

            elif time_filter_type == "Par trimestre":
                # Get available quarters from source_sheet
                available_quarters = get_available_quarters_from_sheets(df)
                if available_quarters:
                    # Filter to selected year
                    year_quarters = [(q, y) for q, y in available_quarters if y == selected_year]
                    if year_quarters:
                        # Get unique quarters for the year
                        unique_quarters = sorted(set(q for q, y in year_quarters))
                        quarter_labels = [f"Q{q}" for q in unique_quarters]
                        selected_quarter = st.selectbox(
                            "Trimestre",
                            unique_quarters,
                            format_func=lambda q: f"Q{q}",
                            key="quarter_filter"
                        )
                    else:
                        st.info(f"Aucun trimestre disponible pour {selected_year}")
                else:
                    st.info("Aucun trimestre disponible dans les données")

        with col3:
            amount_range = st.slider(
                "Plage de montant (€)",
                min_value=0,
                max_value=int(df['amount'].max()) if not df.empty else 100000,
                value=(0, int(df['amount'].max()) if not df.empty else 100000),
                key="amount_range"
            )

        # Apply filters
        filtered_df = df.copy()

        if bu_filter:
            filtered_df = filtered_df[filtered_df['cf_bu'].isin(bu_filter)]

        filtered_df = filtered_df[
            (filtered_df['amount'] >= amount_range[0]) &
            (filtered_df['amount'] <= amount_range[1])
        ]

        # Apply time-based filter
        if time_filter_type == "Par mois" and selected_month is not None:
            # Filter by month and year from source_sheet
            month_mask = pd.Series([False] * len(filtered_df), index=filtered_df.index)
            if 'source_sheet' in filtered_df.columns:
                for idx, sheet in filtered_df['source_sheet'].items():
                    month, year = parse_sheet_month_year(str(sheet))
                    if month == selected_month and year == selected_year:
                        month_mask[idx] = True
            filtered_df = filtered_df[month_mask]

        elif time_filter_type == "Par trimestre" and selected_quarter is not None:
            # Filter by quarter and year from source_sheet
            # Q1: months 1-3, Q2: months 4-6, Q3: months 7-9, Q4: months 10-12
            quarter_months = {
                1: [1, 2, 3],
                2: [4, 5, 6],
                3: [7, 8, 9],
                4: [10, 11, 12]
            }.get(selected_quarter, [])

            quarter_mask = pd.Series([False] * len(filtered_df), index=filtered_df.index)
            if 'source_sheet' in filtered_df.columns:
                for idx, sheet in filtered_df['source_sheet'].items():
                    month, year = parse_sheet_month_year(str(sheet))
                    if month in quarter_months and year == selected_year:
                        quarter_mask[idx] = True
            filtered_df = filtered_df[quarter_mask]

        # Note: "Toute l'année" doesn't need additional filtering

        # Format date columns for display (DD/MM/YYYY format)
        display_df = filtered_df.copy()
        date_cols_to_format = ['date', 'projet_start', 'projet_stop']
        for col in date_cols_to_format:
            if col in display_df.columns:
                # Convert to datetime if not already
                display_df[col] = pd.to_datetime(display_df[col], errors='coerce')
                # Format as DD/MM/YYYY, handle NaT as empty string
                display_df[col] = display_df[col].apply(
                    lambda x: x.strftime('%d/%m/%Y') if pd.notna(x) else ''
                )

        # Display columns
        display_cols = ['title', 'company_name', 'amount']
        if show_pondere:
            display_cols.append('amount_pondere')
        # Add date columns
        display_cols.extend(['date', 'projet_start', 'projet_stop'])
        # Add remaining columns
        display_cols.extend(['cf_bu', 'cf_typologie_de_devis', 'probability', 'statut'])
        # Filter to only include columns that exist in the dataframe
        display_cols = [c for c in display_cols if c in display_df.columns]

        st.dataframe(
            display_df[display_cols].sort_values('amount', ascending=False),
            width='stretch',
            hide_index=True
        )

        # Export buttons
        st.markdown("---")
        col1, col2 = st.columns(2)

        with col1:
            # Use display_df for CSV export (already has formatted dates)
            csv = display_df[display_cols].to_csv(index=False)
            st.download_button(
                label="📥 Exporter en CSV",
                data=csv,
                file_name=f"myrium_export_{selected_year}.csv",
                mime="text/csv",
                key="csv_export"
            )



if __name__ == "__main__":
    # #region agent log - Track script execution
    script_start = time.time()
    debug_log("script:START", "Script execution starting", {}, "A")
    # #endregion
    main()
    # #region agent log
    debug_log("script:END", f"Script execution completed in {time.time()-script_start:.2f}s",
              {"total_duration_s": round(time.time()-script_start, 2)}, "A")
    # #endregion
