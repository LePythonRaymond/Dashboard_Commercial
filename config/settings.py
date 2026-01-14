"""
Myrium Configuration Settings

Loads environment variables and defines application constants.
"""

import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Resolve paths relative to the `myrium/` package root so scripts work regardless of cwd.
MYRIUM_ROOT = Path(__file__).resolve().parent.parent


def _resolve_path(path_str: str) -> str:
    """
    Resolve a possibly-relative path against the myrium package root.
    """
    if not path_str:
        return path_str
    p = Path(path_str)
    if p.is_absolute():
        return str(p)
    return str((MYRIUM_ROOT / p).resolve())


@dataclass
class Settings:
    """Application settings loaded from environment variables."""

    # Furious API
    furious_api_url: str = field(
        default_factory=lambda: os.getenv("FURIOUS_API_URL", "https://merciraymond.furious-squad.com/api/v2")
    )
    furious_username: str = field(default_factory=lambda: os.getenv("FURIOUS_USERNAME", ""))
    furious_password: str = field(default_factory=lambda: os.getenv("FURIOUS_PASSWORD", ""))

    # Google Sheets - OAuth 2.0
    google_oauth_credentials_path: str = field(
        default_factory=lambda: _resolve_path(
            os.getenv("GOOGLE_OAUTH_CREDENTIALS_PATH", "config/credentials/oauth_credentials.json")
        )
    )
    google_oauth_token_path: str = field(
        default_factory=lambda: _resolve_path(
            os.getenv("GOOGLE_OAUTH_TOKEN_PATH", "config/credentials/oauth_token.json")
        )
    )
    # Legacy single spreadsheet (deprecated, use get_spreadsheet_id instead)
    spreadsheet_id: str = field(default_factory=lambda: os.getenv("SPREADSHEET_ID", ""))

    def get_spreadsheet_id(self, view_type: str, year: int) -> str:
        """
        Get spreadsheet ID for a specific view type and year.

        View types: 'etat', 'envoye', 'signe'

        Args:
            view_type: Type of view ('etat', 'envoye', 'signe')
            year: Year (e.g., 2025, 2026)

        Returns:
            Spreadsheet ID from environment variable
        """
        env_var = f"SPREADSHEET_{view_type.upper()}_{year}"
        spreadsheet_id = os.getenv(env_var, "")

        # Fallback to legacy single spreadsheet if new format not found
        if not spreadsheet_id and self.spreadsheet_id:
            return self.spreadsheet_id

        return spreadsheet_id

    # SMTP Email
    smtp_host: str = field(default_factory=lambda: os.getenv("SMTP_HOST", "smtp.gmail.com"))
    smtp_port: int = field(default_factory=lambda: int(os.getenv("SMTP_PORT", "587")))
    smtp_user: str = field(default_factory=lambda: os.getenv("SMTP_USER", ""))
    smtp_password: str = field(default_factory=lambda: os.getenv("SMTP_PASSWORD", ""))

    # Notion
    notion_api_key: str = field(default_factory=lambda: os.getenv("NOTION_API_KEY", ""))
    notion_database_id: str = field(default_factory=lambda: os.getenv("NOTION_DATABASE_ID", ""))
    # Notion databases for alerts
    notion_weird_database_id: str = field(default_factory=lambda: os.getenv("NOTION_WEIRD_DATABASE_ID", ""))
    notion_followup_database_id: str = field(default_factory=lambda: os.getenv("NOTION_FOLLOWUP_DATABASE_ID", ""))
    # Notion database for TRAVAUX projection
    notion_travaux_projection_database_id: str = field(default_factory=lambda: os.getenv("NOTION_TRAVAUX_PROJECTION_DATABASE_ID", ""))

    # API Request Settings
    api_timeout: int = 30
    api_max_retries: int = 3
    proposals_page_limit: int = 250


# Status Configuration
STATUS_WON: List[str] = [
    'gagné',
    'gagne',
    'signé',
    'signe',
    'gagnés et finis',
    'gagnés en cours'
]

STATUS_WAITING: List[str] = [
    'brief',
    'en cours',
    'envoyée(s) attente réponse',
    'envoyée(s) en attente de réponse'
]

# VIP Commercials List (for alert routing)
VIP_COMMERCIALS: List[str] = [
    'clemence',
    'vincent.delavarende',
    'anne-valerie',
    'guillaume',
    'julien.jonis',
    'zoelie',
    'adelaide.patureau'
]

# Email mapping: owner identifier -> email address
# If an owner is not in this mapping, it will use: {owner}@merciraymond.com
OWNER_EMAIL_MAP: Dict[str, str] = {
    'clemence': 'clemence@merciraymond.com',
    'vincent.delavarende': 'vincent.delavarende@merciraymond.com',
    'anne-valerie': 'anne-valerie@merciraymond.com',
    'guillaume': 'guillaume@merciraymond.fr',
    'julien.jonis': 'julien.jonis@merciraymond.com',
    'zoelie': 'taddeo.carpinelli@merciraymond.com',
    'adelaide.patureau': 'adelaide.patureau@merciraymond.com',
    'unassigned': 'taddeo.carpinelli@merciraymond.com',  # Default for unassigned proposals
}

# French Month Mapping
MONTH_MAP: dict = {
    1: "Janvier",
    2: "Fevrier",
    3: "Mars",
    4: "Avril",
    5: "Mai",
    6: "Juin",
    7: "Juillet",
    8: "Aout",
    9: "Septembre",
    10: "Octobre",
    11: "Novembre",
    12: "Decembre"
}

# Business Unit Mappings
BU_MAINTENANCE_KEYWORDS = ["MAINTENANCE", "ENTRETIEN"]
BU_TRAVAUX_KEYWORDS = ["TRAVAUX", "CHANTIER"]
BU_CONCEPTION_KEYWORDS = ["CONCEPTION", "ETUDE"]

# Revenue Engine Thresholds (for CONCEPTION)
CONCEPTION_THRESHOLD_LOW = 15000
CONCEPTION_THRESHOLD_HIGH = 30000

# Alert Configuration
ALERT_AMOUNT_THRESHOLD = 1000  # Flag proposals under 1k
ALERT_FOLLOWUP_DAYS_FORWARD = 60  # Look ahead window

# TRAVAUX Projection Configuration
TRAVAUX_PROJECTION_PROBABILITY_THRESHOLD = 50  # Minimum probability for projection
TRAVAUX_PROJECTION_DATE_WINDOW = 30  # Days for proposal date filter
TRAVAUX_PROJECTION_START_WINDOW = 120  # Days for project start date filter (~4 months)

# Excluded Owners (proposals from these owners will be filtered out)
EXCLUDED_OWNERS: List[str] = [
    'eloi.pujet',
    'eloi',
    'pujet'
]


# Singleton instance
settings = Settings()
