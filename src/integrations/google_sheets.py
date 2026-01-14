"""
Google Sheets Integration Module

Handles reading and writing data to Google Sheets using gspread
with OAuth 2.0 authentication.
"""

import gspread
import gspread.exceptions
import pandas as pd
from typing import Dict, List, Any, Optional
from pathlib import Path
import traceback
import json
import os
import tempfile
from gspread.utils import ValueRenderOption

from config.settings import settings
from src.processing.views import ViewResult, ViewsOutput
from src.processing.cleaner import DataCleaner


class GoogleSheetsClient:
    """
    Client for Google Sheets operations.

    Uses OAuth 2.0 credentials for authentication.
    Handles worksheet creation, data writing, and summary appending.
    """

    SCOPES = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive'
    ]

    # Colors for formatting (RGB 0-1) - Matching dashboard colors
    # BU Colors
    BU_COLORS = {
        'CONCEPTION': {'red': 0.176, 'green': 0.353, 'blue': 0.247},   # #2d5a3f Green
        'TRAVAUX': {'red': 0.957, 'green': 0.769, 'blue': 0.188},      # #f4c430 Yellow
        'MAINTENANCE': {'red': 0.482, 'green': 0.294, 'blue': 0.580},  # #7b4b94 Purple
        'AUTRE': {'red': 0.502, 'green': 0.502, 'blue': 0.502},        # #808080 Gray
    }

    # Typologie Colors - Matching dashboard
    TYPOLOGIE_COLORS = {
        'DV': {'red': 0.906, 'green': 0.435, 'blue': 0.318},           # #e76f51 Coral Red
        'Animation': {'red': 0.165, 'green': 0.616, 'blue': 0.561},    # #2a9d8f Ocean Teal
        'Paysage': {'red': 0.565, 'green': 0.745, 'blue': 0.427},      # #90be6d Light Green
        'Concours': {'red': 0.902, 'green': 0.224, 'blue': 0.275},     # #e63946 Bright Red
        'DV(Travaux)': {'red': 0.969, 'green': 0.498, 'blue': 0.0},   # #f77f00 Orange
        'Travaux Vincent': {'red': 0.988, 'green': 0.749, 'blue': 0.286},  # #fcbf49 Golden Yellow
        'Travaux conception': {'red': 0.988, 'green': 0.639, 'blue': 0.067},  # #fca311 Amber
        'TS': {'red': 0.608, 'green': 0.349, 'blue': 0.714},           # #9b59b6 Purple
        'Entretien': {'red': 0.204, 'green': 0.596, 'blue': 0.859},   # #3498db Blue
        'Toiture': {'red': 0.149, 'green': 0.275, 'blue': 0.325},      # #264653 Dark Teal
        'Intérieur': {'red': 0.957, 'green': 0.635, 'blue': 0.380},    # #f4a261 Orange
        'Etude': {'red': 0.831, 'green': 0.647, 'blue': 0.647},        # #d4a5a5 Pink/Salmon
        'Potager': {'red': 0.369, 'green': 0.376, 'blue': 0.808},      # #5e60ce Purple
        'Formation': {'red': 0.282, 'green': 0.792, 'blue': 0.894},    # #48cae4 Light Blue
        'Autre': {'red': 0.502, 'green': 0.502, 'blue': 0.502},        # #808080 Gray
        'Non': {'red': 0.502, 'green': 0.502, 'blue': 0.502},          # #808080 Gray (Non défini)
        'défini': {'red': 0.502, 'green': 0.502, 'blue': 0.502},       # #808080 Gray (défini)
    }

    # Other colors
    COLORS = {
        'HEADER': {'red': 0.576, 'green': 0.769, 'blue': 0.49},        # Light Green (original)
        'TOTAL_TS': {'red': 0.482, 'green': 0.294, 'blue': 0.580},     # #7b4b94 Purple
        'WHITE': {'red': 1.0, 'green': 1.0, 'blue': 1.0},
        'DEFAULT': {'red': 0.502, 'green': 0.502, 'blue': 0.502},      # Gray
        'YEAR_SEPARATOR': {'red': 0.9, 'green': 0.9, 'blue': 0.9},     # Light gray for separators
    }

    def __init__(
        self,
        credentials_path: Optional[str] = None,
        token_path: Optional[str] = None,
        spreadsheet_id: Optional[str] = None
    ):
        """
        Initialize the Google Sheets client.

        Args:
            credentials_path: Path to OAuth client credentials JSON. Defaults to settings.
            token_path: Path to store OAuth token (will be created on first auth). Defaults to settings.
            spreadsheet_id: Optional specific spreadsheet ID (for backward compatibility).
                          If not provided, will use get_spreadsheet_id() method.
        """
        self.credentials_path = credentials_path or settings.google_oauth_credentials_path
        self.token_path = token_path or settings.google_oauth_token_path
        self._default_spreadsheet_id = spreadsheet_id  # For backward compatibility

        self._client: Optional[gspread.Client] = None
        self._spreadsheets: Dict[str, gspread.Spreadsheet] = {}  # Cache by spreadsheet_id

        # Columns to drop before writing (internal processing columns)
        self.cleaner = DataCleaner()
        self._internal_cols = self.cleaner.get_internal_columns()

    def _authenticate(self) -> gspread.Client:
        """
        Authenticate with Google using OAuth 2.0 credentials.

        On first run, will open browser for user authorization.
        Token is cached for subsequent runs.

        Returns:
            Authenticated gspread client
        """
        # ---------------------------------------------------------------------
        # Streamlit Cloud / Secrets support for OAuth JSON (no JSON files in repo)
        #
        # If you use OAuth (installed app) with a refresh token, you can store the
        # JSON contents of both files in Streamlit secrets and we will write them
        # to temporary files at runtime, then pass those paths to gspread.oauth().
        #
        # Supported secrets (preferred):
        # - [google_oauth]
        #     credentials_json = """{...}"""  # content of oauth_credentials.json
        #     token_json       = """{...}"""  # content of oauth_token.json
        #
        # Also supported:
        # - GOOGLE_OAUTH_CREDENTIALS_JSON = """{...}"""
        # - GOOGLE_OAUTH_TOKEN_JSON       = """{...}"""
        # ---------------------------------------------------------------------
        try:
            import streamlit as st  # type: ignore

            oauth_credentials: Optional[str] = None
            oauth_token: Optional[str] = None

            if hasattr(st, "secrets"):
                if "google_oauth" in st.secrets:
                    oauth_block = st.secrets["google_oauth"]
                    # block can be dict-like (TOML table) or a JSON string
                    if isinstance(oauth_block, str):
                        oauth_block = json.loads(oauth_block)
                    oauth_credentials = oauth_block.get("credentials_json") or oauth_block.get("credentials")
                    oauth_token = oauth_block.get("token_json") or oauth_block.get("token")
                oauth_credentials = oauth_credentials or st.secrets.get("GOOGLE_OAUTH_CREDENTIALS_JSON")
                oauth_token = oauth_token or st.secrets.get("GOOGLE_OAUTH_TOKEN_JSON")

            if oauth_credentials and oauth_token:
                # Write temp files (Streamlit Cloud FS is ephemeral; /tmp is fine)
                tmp_dir = Path(tempfile.gettempdir()) / "myrium_gspread_oauth"
                tmp_dir.mkdir(parents=True, exist_ok=True)

                cred_path = tmp_dir / "oauth_credentials.json"
                token_path = tmp_dir / "oauth_token.json"

                # If secrets were provided as a dict-like object, convert to JSON
                if not isinstance(oauth_credentials, str):
                    oauth_credentials = json.dumps(oauth_credentials)
                if not isinstance(oauth_token, str):
                    oauth_token = json.dumps(oauth_token)

                cred_path.write_text(oauth_credentials, encoding="utf-8")
                token_path.write_text(oauth_token, encoding="utf-8")

                return gspread.oauth(
                    credentials_filename=str(cred_path),
                    authorized_user_filename=str(token_path),
                )
        except Exception:
            # If anything goes wrong here, fall through to other auth methods.
            pass

        # ---------------------------------------------------------------------
        # Streamlit Cloud / Secrets support (no JSON files committed to git)
        #
        # Preferred for Streamlit Community Cloud: Service Account credentials,
        # because the OAuth browser flow is not practical in a hosted environment.
        #
        # Supported secrets:
        # - TOML table: [google_service_account] ... (dict-like)
        # - Stringified JSON: GOOGLE_SERVICE_ACCOUNT_JSON = """{...}"""
        # ---------------------------------------------------------------------
        service_account_info: Optional[dict] = None

        # 1) Try Streamlit secrets (safe when not running under Streamlit)
        try:
            import streamlit as st  # type: ignore

            if hasattr(st, "secrets"):
                if "google_service_account" in st.secrets:
                    raw = st.secrets["google_service_account"]
                    # `raw` can be a mapping (TOML table) or a JSON string
                    if isinstance(raw, str):
                        service_account_info = json.loads(raw)
                    else:
                        service_account_info = dict(raw)
                elif "GOOGLE_SERVICE_ACCOUNT_JSON" in st.secrets:
                    service_account_info = json.loads(st.secrets["GOOGLE_SERVICE_ACCOUNT_JSON"])
        except Exception:
            service_account_info = None

        # 2) Fallback: allow service account JSON via environment variable too
        if service_account_info is None:
            env_sa = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
            if env_sa:
                try:
                    service_account_info = json.loads(env_sa)
                except Exception:
                    service_account_info = None

        if service_account_info:
            try:
                return gspread.service_account_from_dict(service_account_info, scopes=self.SCOPES)
            except Exception as e:
                raise RuntimeError(
                    "Failed to authenticate Google Sheets using service account from secrets/env. "
                    "Double-check that the secret contains a valid service account key JSON and that "
                    "the target spreadsheets are shared with the service account email."
                ) from e

        # ---------------------------------------------------------------------
        # Legacy local/VPS flow: OAuth client credentials file + cached token file
        # ---------------------------------------------------------------------
        if not Path(self.credentials_path).exists():
            raise FileNotFoundError(
                f"OAuth credentials file not found: {self.credentials_path}\n"
                "Please download OAuth client credentials from Google Cloud Console:\n"
                "1. Go to https://console.cloud.google.com/apis/credentials\n"
                "2. Create OAuth 2.0 Client ID (Desktop app)\n"
                "3. Download JSON and save to config/credentials/oauth_credentials.json\n"
                "See SETUP_OAUTH.md for detailed instructions."
            )

        # Use gspread's OAuth flow
        # This will open browser on first run for authorization, then cache token
        # Subsequent runs will use the cached token automatically
        client = gspread.oauth(
            credentials_filename=self.credentials_path,
            authorized_user_filename=self.token_path
        )

        return client

    @property
    def client(self) -> gspread.Client:
        """Get or create authenticated client."""
        if self._client is None:
            self._client = self._authenticate()
        return self._client

    def get_spreadsheet(self, spreadsheet_id: str) -> gspread.Spreadsheet:
        """
        Get or open a spreadsheet by ID (with caching).

        Args:
            spreadsheet_id: Google Sheets spreadsheet ID

        Returns:
            gspread Spreadsheet object
        """
        if spreadsheet_id not in self._spreadsheets:
            self._spreadsheets[spreadsheet_id] = self.client.open_by_key(spreadsheet_id)
        return self._spreadsheets[spreadsheet_id]

    def get_or_create_spreadsheet(self, name: str, spreadsheet_id: Optional[str] = None) -> gspread.Spreadsheet:
        """
        Get existing spreadsheet by ID or create new one by name.

        Args:
            name: Spreadsheet name (if creating new)
            spreadsheet_id: Existing spreadsheet ID (if using existing)

        Returns:
            gspread Spreadsheet object
        """
        if spreadsheet_id:
            return self.get_spreadsheet(spreadsheet_id)

        # Try to find existing spreadsheet by name
        try:
            spreadsheet = self.client.open(name)
            print(f"  Found existing spreadsheet: {name}")
            return spreadsheet
        except gspread.SpreadsheetNotFound:
            # Create new spreadsheet
            try:
                spreadsheet = self.client.create(name)
                print(f"  Created new spreadsheet: {name}")
                return spreadsheet
            except Exception as e:
                error_msg = f"Failed to create spreadsheet '{name}': {str(e)}"
                print(f"  ✗ {error_msg}")
                raise Exception(error_msg) from e

    def get_or_create_worksheet(
        self,
        spreadsheet: gspread.Spreadsheet,
        name: str,
        rows: int = 2000,
        cols: int = 60
    ) -> gspread.Worksheet:
        """
        Get existing worksheet or create new one in the specified spreadsheet.

        Args:
            spreadsheet: gspread Spreadsheet object
            name: Worksheet name
            rows: Number of rows for new worksheet
            cols: Number of columns for new worksheet

        Returns:
            gspread Worksheet object
        """
        try:
            worksheet = spreadsheet.worksheet(name)
            print(f"  Found existing worksheet: {name}")
            # Resize if needed to ensure enough space
            if worksheet.row_count < rows or worksheet.col_count < cols:
                new_rows = max(worksheet.row_count, rows)
                new_cols = max(worksheet.col_count, cols)
                worksheet.resize(rows=new_rows, cols=new_cols)
                print(f"  Resized worksheet to {new_rows} rows x {new_cols} cols")
        except gspread.WorksheetNotFound:
            worksheet = spreadsheet.add_worksheet(title=name, rows=rows, cols=cols)
            print(f"  Created new worksheet: {name}")

        return worksheet

    def _prepare_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Prepare DataFrame for writing to sheets.

        - Drops internal processing columns
        - Converts dates to strings
        - Handles NaN values

        Args:
            df: Raw DataFrame

        Returns:
            Cleaned DataFrame ready for output
        """
        if df.empty:
            return df

        temp = df.copy()

        # Drop internal columns
        cols_to_drop = [c for c in self._internal_cols if c in temp.columns]
        temp = temp.drop(columns=cols_to_drop)

        # Convert dates to strings
        date_cols = ['date', 'projet_start', 'projet_stop', 'created_at',
                     'signature_date', 'last_updated_at', 'dates_effective_start', 'dates_effective_stop']
        for col in date_cols:
            if col in temp.columns:
                temp[col] = temp[col].apply(
                    lambda x: x.strftime('%Y-%m-%d') if pd.notna(x) else ''
                )

        # Identify amount columns (keep as numeric for proper formatting)
        amount_cols = [c for c in temp.columns if 'amount' in str(c).lower() or 'montant' in str(c).lower()]

        # Replace NaN with empty string for non-numeric columns
        for col in temp.columns:
            if col not in amount_cols:
                temp[col] = temp[col].fillna('')
                # Convert non-amount columns to string for safety
                temp[col] = temp[col].astype(str)
            else:
                # For amount columns, keep as numeric (fill NaN with 0)
                temp[col] = pd.to_numeric(temp[col], errors='coerce').fillna(0)

        return temp

    def write_dataframe(
        self,
        worksheet: gspread.Worksheet,
        df: pd.DataFrame,
        start_row: int = 1
    ) -> int:
        """
        Write a DataFrame to a worksheet.

        Args:
            worksheet: Target worksheet
            df: DataFrame to write
            start_row: Row to start writing (1-indexed)

        Returns:
            Next available row after data
        """
        if df.empty:
            print(f"    No data to write")
            return start_row

        prepared = self._prepare_dataframe(df)

        # Convert to list of lists (header + data)
        data = [prepared.columns.tolist()] + prepared.values.tolist()

        # Clear existing content and write
        end_row = start_row + len(data) - 1
        end_col = len(data[0])

        # Get cell range
        cell_range = f'A{start_row}:{gspread.utils.rowcol_to_a1(end_row, end_col).split("!")[0] if "!" in gspread.utils.rowcol_to_a1(end_row, end_col) else gspread.utils.rowcol_to_a1(end_row, end_col)}'

        worksheet.update(range_name=f'A{start_row}', values=data)
        print(f"    Wrote {len(df)} rows to worksheet")

        return end_row + 1

    def _reset_worksheet_layout_and_formatting(
        self,
        spreadsheet: gspread.Spreadsheet,
        worksheet: gspread.Worksheet
    ) -> None:
        """
        Reset worksheet *formatting* and *merges* so formatting doesn't "stick" between runs.

        Important: `worksheet.clear()` only clears values, not formatting/merges. If we don't reset,
        old formatting can land on the wrong rows when the number of data rows changes.
        """
        sheet_id = worksheet.id
        max_rows = int(getattr(worksheet, "row_count", 2000) or 2000)
        max_cols = int(getattr(worksheet, "col_count", 60) or 60)

        full_range = {
            "sheetId": sheet_id,
            "startRowIndex": 0,
            "endRowIndex": max_rows,
            "startColumnIndex": 0,
            "endColumnIndex": max_cols,
        }

        requests = [
            # Remove any merges created by previous runs (summary titles, year separators, etc.)
            {"unmergeCells": {"range": full_range}},
            # Clear formats across the whole sheet (backgrounds, borders, number formats, etc.)
            {
                "repeatCell": {
                    "range": full_range,
                    "cell": {"userEnteredFormat": {}},
                    "fields": "userEnteredFormat",
                }
            },
        ]

        try:
            spreadsheet.batch_update(body={"requests": requests})
        except Exception as e:
            # Formatting reset is best-effort; data writing should still proceed.
            print(f"  Warning: Failed to reset worksheet formatting/merges: {e}")

    def _insert_year_separators(self, headers: List[str], data_rows: List[List]) -> tuple:
        """
        Insert blank separator columns between years in headers and data.

        Args:
            headers: List of column headers
            data_rows: List of data row lists

        Returns:
            Tuple of (new_headers, new_data_rows, separator_indices)
        """
        import re

        # Find years in column names and their positions
        year_positions = {}  # year -> last column index for that year
        for idx, header in enumerate(headers):
            # Match year patterns like 2025, 2026, etc. in column names
            year_match = re.search(r'(20\d{2})', str(header))
            if year_match:
                year = year_match.group(1)
                year_positions[year] = idx

        # Sort years and find where to insert separators
        sorted_years = sorted(year_positions.keys())

        if len(sorted_years) <= 1:
            # No year separation needed
            return headers, data_rows, []

        # Find insertion points (after last column of each year except the last)
        separator_insert_points = []
        for i, year in enumerate(sorted_years[:-1]):  # All years except the last
            insert_after = year_positions[year]
            separator_insert_points.append(insert_after + 1)

        # Adjust for previous insertions (each insertion shifts subsequent indices)
        adjusted_points = []
        offset = 0
        for point in sorted(separator_insert_points):
            adjusted_points.append(point + offset)
            offset += 1

        # Insert separators into headers
        new_headers = list(headers)
        for i, point in enumerate(adjusted_points):
            new_headers.insert(point, '')  # Blank header for separator

        # Insert separators into data rows
        new_data_rows = []
        for row in data_rows:
            new_row = list(row)
            for i, point in enumerate(adjusted_points):
                new_row.insert(point, '')  # Blank cell for separator
            new_data_rows.append(new_row)

        return new_headers, new_data_rows, adjusted_points

    def write_summary(
        self,
        worksheet: gspread.Worksheet,
        summary_data: List[Dict],
        title: str,
        start_row: int
    ) -> tuple:
        """
        Write a summary table to worksheet with year separators.

        Args:
            worksheet: Target worksheet
            summary_data: List of summary dictionaries
            title: Summary section title
            start_row: Row to start writing

        Returns:
            Tuple of (next_row, separator_col_indices)
        """
        if not summary_data:
            return start_row, []

        # Add title row
        worksheet.update(range_name=f'A{start_row}', values=[[title]])
        current_row = start_row + 1

        # Convert to DataFrame
        summary_df = pd.DataFrame(summary_data)
        headers = summary_df.columns.tolist()

        # Prepare data rows
        amount_cols = [c for c in summary_df.columns if 'amount' in str(c).lower() or 'montant' in str(c).lower()]
        data_rows = []

        for _, row in summary_df.iterrows():
            values = []
            for col_name, val in zip(summary_df.columns, row.values):
                if col_name in amount_cols:
                    try:
                        values.append(float(val) if pd.notna(val) and val != '' else 0)
                    except (ValueError, TypeError):
                        values.append(0)
                else:
                    values.append(str(val) if pd.notna(val) and val != '' else '')
            data_rows.append(values)

        # Insert year separators
        new_headers, new_data_rows, separator_indices = self._insert_year_separators(headers, data_rows)

        # Write header with separators
        worksheet.update(range_name=f'A{current_row}', values=[new_headers])
        current_row += 1

        # Write data rows with separators
        for row_values in new_data_rows:
            worksheet.update(range_name=f'A{current_row}', values=[row_values])
            current_row += 1

        return current_row + 1, separator_indices  # Add blank row after summary

    def write_view(
        self,
        view: ViewResult,
        view_type: str,
        year: int
    ) -> None:
        """
        Write a complete view (data + summaries) to a worksheet in the appropriate spreadsheet.

        Args:
            view: ViewResult containing data and summaries
            view_type: Type of view ('etat', 'envoye', 'signe')
            year: Year for the view
        """
        import traceback

        print(f"\nWriting view: {view.name} (Type: {view_type}, Year: {year})")

        try:
            # Get or create the appropriate spreadsheet
            spreadsheet_id = settings.get_spreadsheet_id(view_type, year)
            print(f"  Looking for spreadsheet ID: {spreadsheet_id or 'None (will create by name)'}")

            if not spreadsheet_id:
                # Fallback: try to find/create by name
                spreadsheet_name = f"{view_type.capitalize()} {year}"
                print(f"  Creating/finding spreadsheet by name: {spreadsheet_name}")
                spreadsheet = self.get_or_create_spreadsheet(spreadsheet_name)
            else:
                print(f"  Opening spreadsheet by ID: {spreadsheet_id}")
                spreadsheet = self.get_spreadsheet(spreadsheet_id)

            print(f"  Spreadsheet opened: {spreadsheet.title} (ID: {spreadsheet.id})")

            # Get or create worksheet within the spreadsheet
            worksheet = self.get_or_create_worksheet(spreadsheet, view.name)

            # Clear existing content
            print(f"  Clearing existing content...")
            worksheet.clear()
            # Also clear formatting/merges from previous runs (critical for variable row counts)
            self._reset_worksheet_layout_and_formatting(spreadsheet, worksheet)

            # Track row positions
            current_row = 1

            # Write main data
            print(f"  Writing {len(view.data)} rows...")
            next_row = self.write_dataframe(worksheet, view.data, start_row=current_row)
            data_end_row = next_row - 1

            # Add spacing
            next_row += 2

            # Write BU summary
            bu_summary_start = 0
            bu_summary_end = 0
            bu_separator_cols = []
            if view.summary_by_bu:
                print(f"  Writing BU summary ({len(view.summary_by_bu)} entries)...")
                bu_summary_start = next_row
                next_row, bu_separator_cols = self.write_summary(
                    worksheet,
                    view.summary_by_bu,
                    "Résumé par BU",
                    next_row
                )
                bu_summary_end = next_row - 1
                next_row += 1

            # Write Typologie summary
            type_summary_start = 0
            type_summary_end = 0
            type_separator_cols = []
            if view.summary_by_type:
                print(f"  Writing Typologie summary ({len(view.summary_by_type)} entries)...")
                type_summary_start = next_row
                next_row, type_separator_cols = self.write_summary(
                    worksheet,
                    view.summary_by_type,
                    "Résumé par Typologie",
                    next_row
                )
                type_summary_end = next_row - 1
                next_row += 1

            # Apply Formatting
            try:
                self.format_view(
                    spreadsheet,
                    worksheet,
                    view,  # Pass entire view object
                    start_row=current_row,
                    data_end_row=data_end_row,
                    bu_summary_start=bu_summary_start,
                    bu_summary_end=bu_summary_end,
                    type_summary_start=type_summary_start,
                    type_summary_end=type_summary_end,
                    bu_separator_cols=bu_separator_cols,
                    type_separator_cols=type_separator_cols
                )
            except Exception as e:
                print(f"  Warning: Formatting failed but data was written: {e}")
                print(f"  Traceback: {traceback.format_exc()}")

            print(f"  ✓ Completed view: {view.name} in spreadsheet: {spreadsheet.title}")

        except gspread.exceptions.APIError as e:
            error_msg = f"Google Sheets API error: {e}"
            print(f"  ✗ {error_msg}")
            raise Exception(error_msg) from e
        except gspread.exceptions.SpreadsheetNotFound as e:
            error_msg = f"Spreadsheet not found: {e}"
            print(f"  ✗ {error_msg}")
            raise Exception(error_msg) from e
        except Exception as e:
            error_msg = f"Unexpected error writing view: {str(e)}"
            print(f"  ✗ {error_msg}")
            print(f"  Traceback: {traceback.format_exc()}")
            raise Exception(error_msg) from e

    def write_all_views(self, views: ViewsOutput) -> Dict[str, int]:
        """
        Write all views to the appropriate spreadsheets (by type and year).

        Args:
            views: ViewsOutput containing all views

        Returns:
            Dictionary of sheet names to row counts
        """
        from datetime import datetime

        current_year = datetime.now().year

        print(f"\n{'='*50}")
        print("Writing views to Google Sheets")
        print(f"Current year: {current_year}")
        print(f"{'='*50}")

        # Write snapshot (État au) - uses current year
        self.write_view(views.snapshot, 'etat', current_year)

        # Write sent month (Envoyé) - uses current year
        self.write_view(views.sent_month, 'envoye', current_year)

        # Write won month (Signé) - uses current year
        self.write_view(views.won_month, 'signe', current_year)

        print(f"\n{'='*50}")
        print("All views written successfully!")
        print(f"{'='*50}")

        return views.counts

    def read_worksheet(
        self,
        name: str,
        view_type: Optional[str] = None,
        year: Optional[int] = None
    ) -> pd.DataFrame:
        """
        Read a worksheet into a DataFrame.

        Stops reading when it encounters summary rows (e.g., "Résumé par BU").

        Args:
            name: Worksheet name
            view_type: Type of view ('etat', 'envoye', 'signe') - optional
            year: Year - optional. If both view_type and year provided, uses specific spreadsheet

        Returns:
            DataFrame with worksheet data (excluding summary rows)
        """
        # Markers that indicate the start of summary sections
        SUMMARY_MARKERS = ['Résumé par BU', 'Résumé par Typologie', 'cf_bu', 'cf_typologie']

        # Markers that indicate this is a summary header row (column names in summaries)
        SUMMARY_HEADER_MARKERS = ['Montant Total', 'Montant Pondéré', 'amount']

        try:
            # If view_type and year provided, use specific spreadsheet
            if view_type and year:
                spreadsheet_id = settings.get_spreadsheet_id(view_type, year)
                if spreadsheet_id:
                    spreadsheet = self.get_spreadsheet(spreadsheet_id)
                else:
                    # Fallback: try to find by name
                    spreadsheet_name = f"{view_type.capitalize()} {year}"
                    spreadsheet = self.client.open(spreadsheet_name)
            else:
                # Legacy: use default spreadsheet if available
                if self._default_spreadsheet_id:
                    spreadsheet = self.get_spreadsheet(self._default_spreadsheet_id)
                else:
                    # Try to find in any accessible spreadsheet
                    # This is a fallback - ideally view_type and year should be provided
                    raise ValueError("view_type and year must be provided when no default spreadsheet is set")

            worksheet = spreadsheet.worksheet(name)

            # IMPORTANT:
            # By default, Google Sheets returns "FORMATTED_VALUE" (e.g. "12 345 €"),
            # which breaks numeric parsing in the dashboard and can zero-out amounts.
            # We always want raw numbers/dates where possible.
            data = worksheet.get_all_values(value_render_option=ValueRenderOption.unformatted)

            if not data:
                return pd.DataFrame()

            # First row is header
            headers = data[0]

            # Find the id column index (should be first column)
            id_col_idx = 0
            if 'id' in headers:
                id_col_idx = headers.index('id')

            # Find where actual data ends (before summary sections)
            data_rows = []
            empty_row_count = 0

            def _normalize_id_cell(cell: Any) -> str:
                """
                Normalize an ID cell so it can be validated reliably.

                With ValueRenderOption.unformatted, numeric IDs may come back as int/float.
                We normalize:
                - 12345 -> "12345"
                - 12345.0 -> "12345"
                - "12345.0" -> "12345"
                - " 12345 " -> "12345"
                """
                if cell is None:
                    return ""
                # Handle numeric types directly
                if isinstance(cell, (int,)):
                    return str(cell)
                if isinstance(cell, (float,)):
                    # NaN guard
                    try:
                        if pd.isna(cell):
                            return ""
                    except Exception:
                        pass
                    # Accept integers represented as floats (common for Sheets)
                    if float(cell).is_integer():
                        return str(int(cell))
                    # Non-integer floats are not valid IDs
                    return str(cell).strip()

                s = str(cell).strip()
                if not s:
                    return ""
                # Handle "12345.0" from stringified floats
                if s.endswith(".0") and s[:-2].isdigit():
                    return s[:-2]
                return s

            for row in data[1:]:
                # Get the id value (first data column)
                id_value = _normalize_id_cell(row[id_col_idx]) if len(row) > id_col_idx else ''

                # Check ALL cells in the row for summary markers
                row_text = ' '.join(str(cell).strip() for cell in row[:10])  # Check first 10 columns

                # Stop if we hit a summary marker anywhere in the row
                if any(marker in row_text for marker in SUMMARY_MARKERS):
                    break

                # Stop if we hit a summary header row (like "cf_bu | amount | Montant Total 2025...")
                if any(marker in row_text for marker in SUMMARY_HEADER_MARKERS) and not id_value.isdigit():
                    break

                # Track empty rows
                if not id_value:
                    empty_row_count += 1
                    # Stop after 2 consecutive empty rows (indicates end of data section)
                    if empty_row_count >= 2 and len(data_rows) > 0:
                        break
                    continue
                else:
                    empty_row_count = 0

                # Only add rows where id looks like a valid proposal id (numeric)
                if id_value.isdigit():
                    data_rows.append(row)

            if not data_rows:
                return pd.DataFrame(columns=headers)

            df = pd.DataFrame(data_rows, columns=headers)
            return df

        except (gspread.WorksheetNotFound, gspread.SpreadsheetNotFound) as e:
            print(f"Worksheet/Spreadsheet not found: {name} (type: {view_type}, year: {year})")
            return pd.DataFrame()
        except Exception as e:
            print(f"Error reading worksheet: {e}")
            return pd.DataFrame()

    def list_worksheets(self, view_type: Optional[str] = None, year: Optional[int] = None) -> List[str]:
        """
        List all worksheet names in a spreadsheet.

        Args:
            view_type: Type of view ('etat', 'envoye', 'signe') - optional
            year: Year - optional

        Returns:
            List of worksheet names (all worksheets, including hidden)
        """
        if view_type and year:
            spreadsheet_id = settings.get_spreadsheet_id(view_type, year)
            if spreadsheet_id:
                try:
                    spreadsheet = self.get_spreadsheet(spreadsheet_id)
                    # Explicitly get all worksheets (including hidden)
                    all_worksheets = spreadsheet.worksheets()
                    worksheet_names = [ws.title for ws in all_worksheets]
                    print(f"Found {len(worksheet_names)} worksheets in spreadsheet {spreadsheet.title}: {worksheet_names}")
                    return worksheet_names
                except Exception as e:
                    print(f"Error listing worksheets for {view_type} {year}: {e}")
                    return []

        # Fallback: return empty if no specific spreadsheet
        return []

    def get_worksheets_by_pattern(self, pattern: str, view_type: Optional[str] = None, year: Optional[int] = None) -> List[str]:
        """
        Get worksheet names matching a pattern.

        Args:
            pattern: Pattern to match (e.g., "Signé")
            view_type: Type of view ('etat', 'envoye', 'signe') - optional
            year: Year - optional

        Returns:
            List of matching worksheet names
        """
        all_sheets = self.list_worksheets(view_type, year)
        return [name for name in all_sheets if pattern in name]

    def _get_bu_color(self, bu_name: str) -> Dict[str, float]:
        """Get color for a business unit."""
        if not bu_name:
            return self.COLORS['WHITE']

        bu_upper = str(bu_name).upper()
        if 'MAINTENANCE' in bu_upper:
            return self.BU_COLORS['MAINTENANCE']
        if 'TRAVAUX' in bu_upper:
            return self.BU_COLORS['TRAVAUX']
        if 'CONCEPTION' in bu_upper:
            return self.BU_COLORS['CONCEPTION']
        return self.BU_COLORS.get('AUTRE', self.COLORS['WHITE'])

    def _get_typologie_color(self, typologie_name: str) -> Dict[str, float]:
        """Get color for a typologie."""
        if not typologie_name:
            return self.COLORS['DEFAULT']

        # Try exact match first
        if typologie_name in self.TYPOLOGIE_COLORS:
            return self.TYPOLOGIE_COLORS[typologie_name]

        # Try case-insensitive match
        for key, color in self.TYPOLOGIE_COLORS.items():
            if key.lower() == str(typologie_name).lower():
                return color

        return self.COLORS['DEFAULT']

    def _build_solid_border(self) -> Dict[str, Any]:
        """Build solid border style for cell formatting."""
        border_style = {'style': 'SOLID', 'width': 1, 'color': {'red': 0, 'green': 0, 'blue': 0}}
        # Note: Only top, bottom, left, right are valid for repeatCell borders
        # innerHorizontal and innerVertical are only for updateBorders requests
        return {
            'top': border_style,
            'bottom': border_style,
            'left': border_style,
            'right': border_style
        }

    def _get_amount_column_indices(self, df: pd.DataFrame) -> List[int]:
        """
        Get column indices for all amount columns.

        Args:
            df: DataFrame with columns to check

        Returns:
            List of column indices that contain amounts
        """
        amount_indices = []
        for idx, col in enumerate(df.columns):
            col_lower = str(col).lower()
            # Check if column name contains 'amount' or 'montant'
            if 'amount' in col_lower or 'montant' in col_lower:
                amount_indices.append(idx)
        return amount_indices

    def format_view(
        self,
        spreadsheet: gspread.Spreadsheet,
        worksheet: gspread.Worksheet,
        view: ViewResult,
        start_row: int,
        data_end_row: int,
        bu_summary_start: int,
        bu_summary_end: int,
        type_summary_start: int,
        type_summary_end: int,
        bu_separator_cols: List[int] = None,
        type_separator_cols: List[int] = None
    ) -> None:
        """
        Apply formatting to the entire view.

        Handles:
        - Column resizing (150px)
        - Header formatting (Green, Bold, Borders)
        - Data rows (Colors by BU, Borders)
        - Summaries (Headers, Colors, Borders, Merged cells)
        - Currency formatting for amount columns (Devises arrondis)
        """
        print("  Applying formatting...")

        sheet_id = worksheet.id
        requests = []

        def _adjust_index_for_separators(original_idx: int, separator_indices: List[int]) -> int:
            """
            Map a column index from the *original* summary schema (no separators)
            to the *written* schema (with blank separator columns inserted).

            separator_indices are indices in the *written* header row where blank columns were inserted.
            """
            if original_idx < 0:
                return original_idx
            adj = original_idx
            for p in sorted(separator_indices or []):
                if p <= adj:
                    adj += 1
            return adj

        # 1. Resize all columns to 150px
        requests.append({
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": 0,
                    "endIndex": 50  # Format first 50 columns
                },
                "properties": {"pixelSize": 150},
                "fields": "pixelSize"
            }
        })

        # 2. Format Main Header (Green, Bold, Borders)
        prepared_df = self._prepare_dataframe(view.data)
        num_cols = len(prepared_df.columns)

        header_range = {
            "sheetId": sheet_id,
            "startRowIndex": start_row - 1,
            "endRowIndex": start_row,
            "startColumnIndex": 0,
            "endColumnIndex": num_cols
        }

        requests.append({
            "repeatCell": {
                "range": header_range,
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": self.COLORS['HEADER'],
                        "textFormat": {"bold": True},
                        "borders": self._build_solid_border()
                    }
                },
                "fields": "userEnteredFormat(backgroundColor,textFormat,borders)"
            }
        })

        # 3. Format Data Rows (Borders only - no colors in main data view)
        # Only add borders to data rows, no background colors
        if data_end_row > start_row:
            requests.append({
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": start_row,  # First data row (0-indexed)
                        "endRowIndex": data_end_row + 1,  # Exclusive end
                        "startColumnIndex": 0,
                        "endColumnIndex": num_cols
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "borders": self._build_solid_border()
                        }
                    },
                    "fields": "userEnteredFormat(borders)"
                }
            })

        # 4. Format Summaries
        def format_summary_section(start_idx, end_idx, end_col_idx):
            if start_idx >= end_idx: return

            # 4a. Header Row
            header_row = start_idx
            requests.append({
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": header_row,
                        "endRowIndex": header_row + 1,
                        "startColumnIndex": 0,
                        "endColumnIndex": end_col_idx
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": self.COLORS['HEADER'],
                            "textFormat": {"bold": True},
                            "borders": self._build_solid_border()
                        }
                    },
                    "fields": "userEnteredFormat(backgroundColor,textFormat,borders)"
                }
            })

            # 4b. Data Rows (Borders only initially)
            requests.append({
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": header_row + 1,
                        "endRowIndex": end_idx,
                        "startColumnIndex": 0,
                        "endColumnIndex": end_col_idx
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "borders": self._build_solid_border()
                        }
                    },
                    "fields": "userEnteredFormat(borders)"
                }
            })

            # 4c. Merge Title
            title_row = start_idx - 1
            requests.append({
                "mergeCells": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": title_row,
                        "endRowIndex": title_row + 1,
                        "startColumnIndex": 0,
                        "endColumnIndex": 2
                    },
                    "mergeType": "MERGE_ALL"
                }
            })

        if bu_summary_start < bu_summary_end:
            bu_summary_cols = list(view.summary_by_bu[0].keys()) if view.summary_by_bu else []
            bu_end_col = max(2, len(bu_summary_cols) + len(bu_separator_cols or []))
            format_summary_section(bu_summary_start, bu_summary_end, bu_end_col)

            # Apply BU colors to summary rows (first column only - the BU name)
            current_row = bu_summary_start + 1
            for item in view.summary_by_bu:
                if current_row >= bu_summary_end: break

                bu_name = str(item.get('cf_bu', ''))
                color = self._get_bu_color(bu_name)

                requests.append({
                    "repeatCell": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": current_row,
                            "endRowIndex": current_row + 1,
                            "startColumnIndex": 0,
                            "endColumnIndex": 1  # Only first column (BU name)
                        },
                        "cell": {
                            "userEnteredFormat": {
                                "backgroundColor": color
                            }
                        },
                        "fields": "userEnteredFormat(backgroundColor)"
                    }
                })
                current_row += 1

        if type_summary_start < type_summary_end:
            type_summary_cols = list(view.summary_by_type[0].keys()) if view.summary_by_type else []
            type_end_col = max(2, len(type_summary_cols) + len(type_separator_cols or []))
            format_summary_section(type_summary_start, type_summary_end, type_end_col)

            # Apply Typologie colors to summary rows (first column only - the Typologie name)
            current_row = type_summary_start + 1
            for item in view.summary_by_type:
                if current_row >= type_summary_end:
                    break

                type_name = str(item.get('cf_typologie_de_devis', ''))
                color = self._get_typologie_color(type_name)

                requests.append({
                    "repeatCell": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": current_row,
                            "endRowIndex": current_row + 1,
                            "startColumnIndex": 0,
                            "endColumnIndex": 1  # Only first column (Typologie name)
                        },
                        "cell": {
                            "userEnteredFormat": {
                                "backgroundColor": color
                            }
                        },
                        "fields": "userEnteredFormat(backgroundColor)"
                    }
                })
                current_row += 1

        # Format year separator columns (merge cells and add borders)
        def format_separator_columns(summary_start, summary_end, separator_cols):
            """Format separator columns with merged cells and borders."""
            if not separator_cols or summary_start >= summary_end:
                return

            # summary_start is 1-indexed (title row)
            # Header row is at summary_start + 1 (1-indexed) = summary_start (0-indexed)
            # Data starts at summary_start + 2 (1-indexed) = summary_start + 1 (0-indexed)
            header_row_0idx = summary_start  # 0-indexed header row
            data_start_0idx = summary_start + 1  # 0-indexed first data row
            data_end_0idx = summary_end  # 0-indexed (exclusive is summary_end)

            for col_idx in separator_cols:
                # Format separator column with light gray background and thick borders
                # Format from header to end of data
                requests.append({
                    "repeatCell": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": header_row_0idx,
                            "endRowIndex": data_end_0idx,
                            "startColumnIndex": col_idx,
                            "endColumnIndex": col_idx + 1
                        },
                        "cell": {
                            "userEnteredFormat": {
                                "backgroundColor": self.COLORS['YEAR_SEPARATOR'],
                                "borders": {
                                    "left": {"style": "SOLID_THICK", "width": 2, "color": {"red": 0, "green": 0, "blue": 0}},
                                    "right": {"style": "SOLID_THICK", "width": 2, "color": {"red": 0, "green": 0, "blue": 0}}
                                }
                            }
                        },
                        "fields": "userEnteredFormat(backgroundColor,borders)"
                    }
                })

                # Merge the separator cells vertically
                requests.append({
                    "mergeCells": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": header_row_0idx,
                            "endRowIndex": data_end_0idx,
                            "startColumnIndex": col_idx,
                            "endColumnIndex": col_idx + 1
                        },
                        "mergeType": "MERGE_ALL"
                    }
                })

        # Apply separator formatting for BU summary
        if bu_separator_cols:
            format_separator_columns(bu_summary_start, bu_summary_end, bu_separator_cols)

        # Apply separator formatting for Typologie summary
        if type_separator_cols:
            format_separator_columns(type_summary_start, type_summary_end, type_separator_cols)


        # 6. Format Amount Columns as Currency (Devises arrondis)
        amount_col_indices = self._get_amount_column_indices(prepared_df)
        if amount_col_indices:
            print(f"    Formatting {len(amount_col_indices)} amount columns as currency...")

            # Format amount columns in data rows
            # start_row is 1-indexed (row 1 = header), so data starts at row 2
            # In 0-indexed: header = 0, first data row = 1, last data row = data_end_row - 1
            # endRowIndex is exclusive, so use data_end_row + 1
            if data_end_row > start_row:
                for col_idx in amount_col_indices:
                    requests.append({
                        "repeatCell": {
                            "range": {
                                "sheetId": sheet_id,
                                "startRowIndex": start_row,  # start_row=1 (1-indexed header) = 1 (0-indexed, first data row)
                                "endRowIndex": data_end_row + 1,  # Exclusive: data_end_row (1-indexed) + 1
                                "startColumnIndex": col_idx,
                                "endColumnIndex": col_idx + 1
                            },
                            "cell": {
                                "userEnteredFormat": {
                                    "numberFormat": {
                                        "type": "NUMBER",
                                        "pattern": "#,##0 €"
                                    }
                                }
                            },
                            "fields": "userEnteredFormat.numberFormat"
                        }
                    })

            # Format amount columns in BU summary
            if bu_summary_start < bu_summary_end:
                # Find which BU summary columns are amount columns (in the *original* schema)
                bu_summary_cols = list(view.summary_by_bu[0].keys()) if view.summary_by_bu else []
                bu_summary_amount_indices = [
                    idx for idx, col in enumerate(bu_summary_cols)
                    if 'amount' in str(col).lower() or 'montant' in str(col).lower()
                ]
                for original_col_idx in bu_summary_amount_indices:
                    summary_col_idx = _adjust_index_for_separators(original_col_idx, bu_separator_cols or [])
                    requests.append({
                        "repeatCell": {
                            "range": {
                                "sheetId": sheet_id,
                                "startRowIndex": bu_summary_start + 1,  # Skip header
                                "endRowIndex": bu_summary_end,
                                "startColumnIndex": summary_col_idx,
                                "endColumnIndex": summary_col_idx + 1
                            },
                            "cell": {
                                "userEnteredFormat": {
                                    "numberFormat": {
                                        "type": "NUMBER",
                                        "pattern": "#,##0 €"
                                    }
                                }
                            },
                            "fields": "userEnteredFormat.numberFormat"
                        }
                    })

            # Format amount columns in Type summary
            if type_summary_start < type_summary_end:
                type_summary_cols = list(view.summary_by_type[0].keys()) if view.summary_by_type else []
                type_summary_amount_indices = [
                    idx for idx, col in enumerate(type_summary_cols)
                    if 'amount' in str(col).lower() or 'montant' in str(col).lower()
                ]
                for original_col_idx in type_summary_amount_indices:
                    summary_col_idx = _adjust_index_for_separators(original_col_idx, type_separator_cols or [])
                    requests.append({
                        "repeatCell": {
                            "range": {
                                "sheetId": sheet_id,
                                "startRowIndex": type_summary_start + 1,  # Skip header
                                "endRowIndex": type_summary_end,
                                "startColumnIndex": summary_col_idx,
                                "endColumnIndex": summary_col_idx + 1
                            },
                            "cell": {
                                "userEnteredFormat": {
                                    "numberFormat": {
                                        "type": "NUMBER",
                                        "pattern": "#,##0 €"
                                    }
                                }
                            },
                            "fields": "userEnteredFormat.numberFormat"
                        }
                    })

        # Send batch update using spreadsheet's batch_update for API formatting requests
        if requests:
            try:
                spreadsheet.batch_update(body={'requests': requests})
                print(f"    ✓ Applied {len(requests)} formatting rules")
            except Exception as e:
                print(f"    ✗ Error formatting: {e}")
