"""
Typologie Allocation Helper Module

Provides deterministic typologie allocation logic based on cf_typologie_de_devis column.
Replaces equal-split logic with primary-tag allocation rules.

Rules:
1. TS ownership: TS card stays under MAINTENANCE, but amount is "owned" by TRAVAUX
2. TS detection: Project is TS if cf_typologie_de_devis contains TS OR title contains TS (no double counting)
3. Multi-tags: Project counted in each tag's count, but amount goes to primary tag only
   - Animation is lowest priority: if multiple tags, amount goes to first non-Animation tag
   - If all tags are Animation, amount goes to Animation
"""

import re
from typing import Any, List, Optional, Tuple
import pandas as pd


def parse_typologie_list(raw: Any) -> List[str]:
    """
    Parse typologie string into list of tags.

    Handles:
    - Comma-separated values: "Conception DV, Conception Paysage, Maintenance Animation"
    - Single tag (no comma): "Travaux Direct" (kept intact, not split on whitespace)
    - NaN/None/empty strings
    - "Non défini" treated as empty

    IMPORTANT: Only splits on commas to preserve multi-word tags like "Conception DV".

    Args:
        raw: Raw value from DataFrame (can be string, list, NaN, None)

    Returns:
        List of cleaned typologie tags (empty list if invalid)
    """
    if pd.isna(raw) or raw is None:
        return []

    if isinstance(raw, list):
        # Handle list input (from API)
        tags = [str(v).strip() for v in raw if v and str(v).strip()]
        return [t for t in tags if t and t.lower() != 'nan']

    raw_str = str(raw).strip()

    if not raw_str or raw_str.lower() in ('nan', 'none', 'non défini', 'non defini'):
        return []

    # Split ONLY by comma to preserve multi-word tags (e.g., "Conception DV", "Travaux Direct")
    if ',' in raw_str:
        tags = [t.strip() for t in raw_str.split(',')]
    else:
        # No comma: treat entire string as single tag (preserves multi-word tags)
        tags = [raw_str]

    # Filter out empty strings and 'nan'
    return [t for t in tags if t and t.lower() != 'nan']


def title_has_ts(title: Any) -> bool:
    """
    Check if title contains TS (case-insensitive, word boundary or parentheses).

    Matches:
    - "TS" as word boundary: "Project TS", "TS Project", "TS-123"
    - "(TS)" in parentheses

    Args:
        title: Title string from DataFrame

    Returns:
        True if title contains TS, False otherwise
    """
    if pd.isna(title) or title is None:
        return False

    title_str = str(title).upper()

    # Check for word boundary TS or (TS)
    if re.search(r'\bTS\b', title_str) or '(TS)' in title_str:
        return True

    return False


def detect_ts(tags: List[str], title: Any) -> bool:
    """
    Detect if project is TS based on tags or title.

    Args:
        tags: List of typologie tags
        title: Project title

    Returns:
        True if TS detected (in tags or title), False otherwise
    """
    # Check tags - support both old 'TS' and new 'Maintenance TS'
    if any(tag.upper() == 'TS' or tag.upper() == 'MAINTENANCE TS' or 'TS' in tag.upper() for tag in tags):
        return True

    # Check title
    if title_has_ts(title):
        return True

    return False


def inject_ts_tag(tags: List[str], title: Any) -> List[str]:
    """
    Add Maintenance TS tag if detected from title, avoiding duplicates.

    Args:
        tags: List of typologie tags
        title: Project title

    Returns:
        List of tags with Maintenance TS added if detected (no duplicates)
    """
    result = tags.copy()

    # Check if TS already in tags (support both old 'TS' and new 'Maintenance TS')
    has_ts_tag = any(tag.upper() == 'TS' or tag.upper() == 'MAINTENANCE TS' or 'TS' in tag.upper() for tag in result)

    # Check if title has TS
    if title_has_ts(title) and not has_ts_tag:
        result.append('Maintenance TS')

    return result


def choose_primary_typologie(tags: List[str]) -> Optional[str]:
    """
    Choose primary typologie for amount allocation.

    Rules:
    1. If Maintenance TS (or TS) in tags → Maintenance TS is primary
    2. If multiple tags → choose first non-Maintenance Animation tag
    3. If all tags are Maintenance Animation → Maintenance Animation is primary
    4. If single tag → that tag is primary

    Args:
        tags: List of typologie tags (should already have TS injected if needed)

    Returns:
        Primary typologie string, or None if no tags
    """
    if not tags:
        return None

    # Rule 1: Maintenance TS has highest priority (support both old 'TS' and new 'Maintenance TS')
    for tag in tags:
        tag_upper = tag.upper()
        if tag_upper == 'TS' or tag_upper == 'MAINTENANCE TS' or 'TS' in tag_upper:
            # Return the actual tag name (preserve case), but normalize old 'TS' to 'Maintenance TS'
            if tag_upper == 'TS':
                return 'Maintenance TS'
            return tag

    # Rule 2 & 3: Multiple tags - find first non-Maintenance Animation
    if len(tags) > 1:
        for tag in tags:
            tag_upper = tag.upper()
            # Skip Maintenance Animation (support both old 'Animation' and new 'Maintenance Animation')
            if tag_upper != 'ANIMATION' and tag_upper != 'MAINTENANCE ANIMATION' and 'ANIMATION' not in tag_upper:
                return tag
        # All tags are Maintenance Animation
        return tags[0]

    # Rule 4: Single tag
    return tags[0]


def allocate_typologie_for_row(
    row: pd.Series,
    typologie_col: str = 'cf_typologie_de_devis',
    title_col: str = 'title'
) -> Tuple[List[str], Optional[str]]:
    """
    Allocate typologie for a single DataFrame row.

    Returns both tags_for_count and primary_for_amount.

    Args:
        row: DataFrame row (Series)
        typologie_col: Column name for typologie (default: 'cf_typologie_de_devis')
        title_col: Column name for title (default: 'title')

    Returns:
        Tuple of (tags_for_count, primary_for_amount):
        - tags_for_count: List of typologie tags (for counting)
        - primary_for_amount: Single typologie tag (for amount allocation)
    """
    # Parse typologie column
    raw_typologie = row.get(typologie_col, '')
    tags = parse_typologie_list(raw_typologie)

    # Inject TS if detected from title
    title = row.get(title_col, '')
    tags = inject_ts_tag(tags, title)

    # Choose primary typologie
    primary = choose_primary_typologie(tags)

    # Deduplicate tags for count (avoid "DV, DV" counting twice)
    tags_deduplicated = []
    seen = set()
    for tag in tags:
        tag_upper = tag.upper()
        if tag_upper not in seen:
            tags_deduplicated.append(tag)
            seen.add(tag_upper)

    return tags_deduplicated, primary
