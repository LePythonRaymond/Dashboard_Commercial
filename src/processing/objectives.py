"""
Objectives Module

Hardcoded monthly/quarterly/yearly objectives for CA Envoyé and CA Signé,
by Business Unit and Typologie.
"""

from typing import Dict, List, Literal, Optional
from datetime import date, datetime
from calendar import monthrange
import warnings

# Type aliases
ObjectiveMetric = Literal["envoye", "signe"]
ObjectiveDimension = Literal["bu", "typologie"]

# Expected BU keys (must match dashboard BU_ORDER)
EXPECTED_BUS = ['CONCEPTION', 'TRAVAUX', 'MAINTENANCE', 'AUTRE']

# Expected Typologie keys (union of all BU_TO_TYPOLOGIES)
EXPECTED_TYPOLOGIES = [
    'Conception Concours', 'Conception DV', 'Conception Paysage',  # CONCEPTION
    'Travaux Direct', 'Travaux DV', 'Travaux Conception',  # TRAVAUX
    'Maintenance TS', 'Maintenance Entretien', 'Maintenance Animation',  # MAINTENANCE
    'Autre'  # AUTRE
]

# =============================================================================
# HELPER FUNCTIONS FOR 11-MONTH DISTRIBUTION
# =============================================================================

def generate_11_month_distribution(annual_total: float = None, monthly_amount: float = None) -> List[float]:
    """
    Generate 12-month list with 11-month accounting (July+August concatenated as one period).

    Rules:
    - August (month 8) = 0
    - July (month 7) = normal_month (NOT doubled - July+August together = one accounting period)
    - All other months = normal_month

    Args:
        annual_total: Total annual objective (used to calculate normal_month if monthly_amount not provided)
        monthly_amount: Optional fixed monthly amount (for TS which is per-month)

    Returns:
        List of 12 monthly values (August will be 0, all others including July = normal_month)
    """
    if monthly_amount is not None:
        # Fixed monthly amount (e.g., TS)
        normal_month = monthly_amount
    else:
        # Calculate normal month from annual total (distributed over 11 months)
        if annual_total is None:
            raise ValueError("Either annual_total or monthly_amount must be provided")
        normal_month = annual_total / 11.0

    months = [normal_month] * 12
    months[6] = normal_month  # July (0-indexed: month 7 = index 6) - NOT doubled
    months[7] = 0.0  # August (0-indexed: month 8 = index 7) - zero

    return months


# =============================================================================
# OBJECTIVES DATA (Hardcoded)
# =============================================================================
# Structure: OBJECTIVES[year][metric][dimension][key] = [12 monthly values]
# Example: OBJECTIVES[2026]["envoye"]["bu"]["CONCEPTION"] = [10000, 12000, ...]

OBJECTIVES: Dict[int, Dict[ObjectiveMetric, Dict[ObjectiveDimension, Dict[str, List[float]]]]] = {
    2025: {
        "envoye": {
            "bu": {
                "CONCEPTION": [300000] * 12,  # TODO: Fill with actual objectives
                "TRAVAUX": [1000000] * 12,
                "MAINTENANCE": [200000] * 12,
                "AUTRE": [0.0] * 12,
            },
            "typologie": {
                "Conception DV": [100000] * 12,
                "Conception Paysage": [1000000] * 12,
                "Conception Concours": [100000] * 12,
                "Travaux DV": [300000] * 12,
                "Travaux Direct": [400000] * 12,
                "Travaux Conception": [300000] * 12,
                "Maintenance TS": [75000] * 12,
                "Maintenance Entretien": [75000] * 12,
                "Maintenance Animation": [50000] * 12,
                "Autre": [0.0] * 12,
            }
        },
        "signe": {
            "bu": {
                "CONCEPTION": [70000] * 12,
                "TRAVAUX": [300000] * 12,
                "MAINTENANCE": [60000] * 12,
                "AUTRE": [0.0] * 12,
            },
            "typologie": {
                "Conception DV": [20000] * 12,
                "Conception Paysage": [30000] * 12,
                "Conception Concours": [20000] * 12,
                "Travaux DV": [100000] * 12,
                "Travaux Direct": [100000] * 12,
                "Travaux Conception": [100000] * 12,
                "Maintenance TS": [25000] * 12,
                "Maintenance Entretien": [25000] * 12,
                "Maintenance Animation": [10000] * 12,
                "Autre": [0.0] * 12,
            }
        }
    },
    2026: {
        "signe": {
            "typologie": {
                # CONCEPTION
                "Conception DV": generate_11_month_distribution(50000),
                "Conception Concours": generate_11_month_distribution(100000),
                "Conception Paysage": generate_11_month_distribution(650000),
                # TRAVAUX
                "Travaux DV": generate_11_month_distribution(1000000),
                "Travaux Conception": generate_11_month_distribution(500000),
                "Travaux Direct": generate_11_month_distribution(1500000),
                # MAINTENANCE
                "Maintenance Entretien": generate_11_month_distribution(495000),
                "Maintenance TS": generate_11_month_distribution(137500),  # 137,500 per year
                "Maintenance Animation": generate_11_month_distribution(50000),  # 50,000 per year
                # AUTRE
                "Autre": [0.0] * 12,
            },
            "bu": {
                # BU totals: CONCEPTION (800k), TRAVAUX (3M), MAINTENANCE (682.5k)
                # Computed as sum of typologies and stored directly
                "CONCEPTION": generate_11_month_distribution(800000),  # Conception DV 50k + Conception Concours 100k + Conception Paysage 650k
                "TRAVAUX": generate_11_month_distribution(3000000),     # Travaux DV 1M + Travaux Conception 500k + Travaux Direct 1.5M
                "MAINTENANCE": generate_11_month_distribution(682500), # Maintenance Entretien 495k + Maintenance TS 137.5k + Maintenance Animation 50k
                "AUTRE": [0.0] * 12,
            }
        },
        "envoye": {
            # Envoyé objectives = Signed objectives (same monthly distributions)
            "typologie": {
                "DV": generate_11_month_distribution(50000),
                "Concours": generate_11_month_distribution(100000),
                "Paysage": generate_11_month_distribution(650000),
                "DV(Travaux)": generate_11_month_distribution(1000000),
                "Travaux conception": generate_11_month_distribution(500000),
                "Travaux Vincent": generate_11_month_distribution(1500000),
                "Entretien": generate_11_month_distribution(495000),
                "TS": generate_11_month_distribution(137500),  # 137,500 per year
                "Animation": generate_11_month_distribution(50000),  # 50,000 per year
                "Autre": [0.0] * 12,
            },
            "bu": {
                # BU totals: CONCEPTION (800k), TRAVAUX (3M), MAINTENANCE (682.5k)
                # Same as signe (Envoyé = Signé)
                "CONCEPTION": generate_11_month_distribution(800000),
                "TRAVAUX": generate_11_month_distribution(3000000),
                "MAINTENANCE": generate_11_month_distribution(682500),
                "AUTRE": [0.0] * 12,
            }
        }
    },
    2027: {
        "envoye": {
            "bu": {
                "CONCEPTION": [300000] * 12,
                "TRAVAUX": [1000000] * 12,
                "MAINTENANCE": [200000] * 12,
                "AUTRE": [0.0] * 12,
            },
            "typologie": {
                "Conception DV": [100000] * 12,
                "Conception Paysage": [1000000] * 12,
                "Conception Concours": [100000] * 12,
                "Travaux DV": [300000] * 12,
                "Travaux Direct": [400000] * 12,
                "Travaux Conception": [300000] * 12,
                "Maintenance TS": [75000] * 12,
                "Maintenance Entretien": [75000] * 12,
                "Maintenance Animation": [50000] * 12,
                "Autre": [0.0] * 12,
            }
        },
        "signe": {
            "bu": {
                "CONCEPTION": [70000] * 12,
                "TRAVAUX": [300000] * 12,
                "MAINTENANCE": [60000] * 12,
                "AUTRE": [0.0] * 12,
            },
            "typologie": {
                "Conception DV": [20000] * 12,
                "Conception Paysage": [30000] * 12,
                "Conception Concours": [20000] * 12,
                "Travaux DV": [100000] * 12,
                "Travaux Direct": [100000] * 12,
                "Travaux Conception": [100000] * 12,
                "Maintenance TS": [25000] * 12,
                "Maintenance Entretien": [25000] * 12,
                "Maintenance Animation": [10000] * 12,
                "Autre": [0.0] * 12,
            }
        }
    }
}


def validate_objectives() -> List[str]:
    """
    Validate objectives structure and return list of warnings/errors.

    Returns:
        List of warning/error messages (empty if all valid)
    """
    issues = []

    for year, year_data in OBJECTIVES.items():
        for metric in ["envoye", "signe"]:
            if metric not in year_data:
                issues.append(f"Year {year}: Missing metric '{metric}'")
                continue

            for dimension in ["bu", "typologie"]:
                if dimension not in year_data[metric]:
                    issues.append(f"Year {year}, {metric}: Missing dimension '{dimension}'")
                    continue

                expected_keys = EXPECTED_BUS if dimension == "bu" else EXPECTED_TYPOLOGIES
                for key in expected_keys:
                    if key not in year_data[metric][dimension]:
                        issues.append(f"Year {year}, {metric}, {dimension}: Missing key '{key}'")
                    else:
                        values = year_data[metric][dimension][key]
                        if len(values) != 12:
                            issues.append(
                                f"Year {year}, {metric}, {dimension}, {key}: "
                                f"Expected 12 values, got {len(values)}"
                            )

    return issues


# Validate on import
_validation_issues = validate_objectives()
if _validation_issues:
    warnings.warn(
        f"Objectives validation issues found:\n" + "\n".join(_validation_issues),
        UserWarning
    )


# =============================================================================
# ACCOUNTING PERIOD HELPERS (11-month accounting: July+August merged)
# =============================================================================

ACCOUNTING_PERIODS = [
    "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
    "Juil+Août", "Septembre", "Octobre", "Novembre", "Décembre"
]

ACCOUNTING_PERIOD_MONTH_MAP = {
    1: 0,   # Janvier -> period 0
    2: 1,   # Février -> period 1
    3: 2,   # Mars -> period 2
    4: 3,   # Avril -> period 3
    5: 4,   # Mai -> period 4
    6: 5,   # Juin -> period 5
    7: 6,   # Juillet -> period 6 (Juil+Août)
    8: 6,   # Août -> period 6 (Juil+Août)
    9: 7,   # Septembre -> period 7
    10: 8,  # Octobre -> period 8
    11: 9,  # Novembre -> period 9
    12: 10  # Décembre -> period 10
}


def get_accounting_period_for_month(month_num: int) -> int:
    """
    Get accounting period index (0-10) for a calendar month (1-12).

    July and August both map to period 6 (Juil+Août).

    Args:
        month_num: Calendar month number (1-12)

    Returns:
        Accounting period index (0-10)
    """
    return ACCOUNTING_PERIOD_MONTH_MAP.get(month_num, 0)


def get_accounting_period_label(period_idx: int) -> str:
    """
    Get label for an accounting period index.

    Args:
        period_idx: Accounting period index (0-10)

    Returns:
        Period label (e.g., "Janvier", "Juil+Août")
    """
    if 0 <= period_idx < len(ACCOUNTING_PERIODS):
        return ACCOUNTING_PERIODS[period_idx]
    return "Inconnu"


def get_months_for_accounting_period(period_idx: int) -> List[int]:
    """
    Get calendar month numbers (1-12) that belong to an accounting period.

    Args:
        period_idx: Accounting period index (0-10)

    Returns:
        List of month numbers (e.g., [7, 8] for period 6)
    """
    result = []
    for month_num, period in ACCOUNTING_PERIOD_MONTH_MAP.items():
        if period == period_idx:
            result.append(month_num)
    return sorted(result)


def count_unique_accounting_periods(month_numbers: List[int]) -> int:
    """
    Count unique accounting periods from a list of month numbers.

    July and August count as one period.

    Args:
        month_numbers: List of calendar month numbers (1-12)

    Returns:
        Number of unique accounting periods
    """
    unique_periods = set()
    for month_num in month_numbers:
        period_idx = get_accounting_period_for_month(month_num)
        unique_periods.add(period_idx)
    return len(unique_periods)


def get_quarter_for_month(month_num: int) -> str:
    """
    Get quarter identifier for a month number.

    Args:
        month_num: Month number (1-12)

    Returns:
        Quarter identifier ("Q1", "Q2", "Q3", or "Q4")
    """
    if month_num in [1, 2, 3]:
        return "Q1"
    elif month_num in [4, 5, 6]:
        return "Q2"
    elif month_num in [7, 8, 9]:
        return "Q3"
    elif month_num in [10, 11, 12]:
        return "Q4"
    else:
        raise ValueError(f"Invalid month number: {month_num}")


def quarter_start_dates(year: int) -> Dict[str, date]:
    """
    Get quarter start dates for a year.

    Args:
        year: Year

    Returns:
        Dictionary mapping quarter to start date: {"Q1": date(2026, 1, 1), ...}
    """
    return {
        "Q1": date(year, 1, 1),
        "Q2": date(year, 4, 1),
        "Q3": date(year, 7, 1),
        "Q4": date(year, 10, 1)
    }


def quarter_end_dates(year: int) -> Dict[str, date]:
    """
    Get quarter end dates for a year.

    Args:
        year: Year

    Returns:
        Dictionary mapping quarter to end date: {"Q1": date(2026, 3, 31), ...}
    """
    return {
        "Q1": date(year, 3, 31),
        "Q2": date(year, 6, 30),
        "Q3": date(year, 9, 30),
        "Q4": date(year, 12, 31)
    }


def last_day_of_month(year: int, month: int) -> int:
    """
    Get the last day number for a given year and month.

    Args:
        year: Year
        month: Month number (1-12)

    Returns:
        Last day of month (28-31)
    """
    return monthrange(year, month)[1]


def objective_for_month(
    year: int,
    metric: ObjectiveMetric,
    dimension: ObjectiveDimension,
    key: str,
    month_num: int
) -> float:
    """
    Get objective for a specific month.

    Args:
        year: Year
        metric: "envoye" or "signe"
        dimension: "bu" or "typologie"
        key: BU name or typologie name
        month_num: Month number (1-12)

    Returns:
        Objective value for that month (0.0 if not found)
    """
    if year not in OBJECTIVES:
        return 0.0

    if metric not in OBJECTIVES[year]:
        return 0.0

    if dimension not in OBJECTIVES[year][metric]:
        return 0.0

    if key not in OBJECTIVES[year][metric][dimension]:
        return 0.0

    values = OBJECTIVES[year][metric][dimension][key]
    if month_num < 1 or month_num > 12:
        return 0.0

    return values[month_num - 1]  # Convert to 0-indexed


def objective_for_quarter(
    year: int,
    metric: ObjectiveMetric,
    dimension: ObjectiveDimension,
    key: str,
    quarter: str
) -> float:
    """
    Get objective for a quarter (sum of 3 months).

    Args:
        year: Year
        metric: "envoye" or "signe"
        dimension: "bu" or "typologie"
        key: BU name or typologie name
        quarter: "Q1", "Q2", "Q3", or "Q4"

    Returns:
        Sum of objectives for the 3 months in that quarter
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
        total += objective_for_month(year, metric, dimension, key, month_num)

    # Avoid float drift from 11-month distributions (e.g. 50000/11) by rounding.
    return round(total, 2)


def objective_for_year(
    year: int,
    metric: ObjectiveMetric,
    dimension: ObjectiveDimension,
    key: str
) -> float:
    """
    Get objective for a full year (sum of 12 months).

    Args:
        year: Year
        metric: "envoye" or "signe"
        dimension: "bu" or "typologie"
        key: BU name or typologie name

    Returns:
        Sum of objectives for all 12 months
    """
    total = 0.0
    for month_num in range(1, 13):
        total += objective_for_month(year, metric, dimension, key, month_num)

    # Avoid float drift from 11-month distributions (e.g. 50000/11) by rounding.
    return round(total, 2)


def get_all_objectives_for_dimension(
    year: int,
    metric: ObjectiveMetric,
    dimension: ObjectiveDimension
) -> Dict[str, List[float]]:
    """
    Get all objectives for a dimension (all BUs or all typologies).

    Args:
        year: Year
        metric: "envoye" or "signe"
        dimension: "bu" or "typologie"

    Returns:
        Dictionary mapping key to list of 12 monthly values
    """
    if year not in OBJECTIVES:
        return {}

    if metric not in OBJECTIVES[year]:
        return {}

    if dimension not in OBJECTIVES[year][metric]:
        return {}

    return OBJECTIVES[year][metric][dimension].copy()
