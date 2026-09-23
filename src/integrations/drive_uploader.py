"""
Google Drive uploader: convert an xlsx blob into a native Google Sheet
inside a configured Drive folder, and return the new spreadsheet URL.

Reuses the same Google credentials as ``GoogleSheetsClient`` (Streamlit
secrets / env / OAuth file). Requires ``google-api-python-client`` and the
existing ``drive`` scope already declared on the service account.
"""

from __future__ import annotations

import io
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional


SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive',
]

XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
SHEET_MIME = 'application/vnd.google-apps.spreadsheet'


# =============================================================================
# Credentials resolution (mirrors GoogleSheetsClient._authenticate)
# =============================================================================

def _load_service_account_info_from_streamlit() -> Optional[dict]:
    try:
        import streamlit as st  # type: ignore
    except Exception:
        return None
    if not hasattr(st, "secrets"):
        return None
    try:
        if "google_service_account" in st.secrets:
            raw = st.secrets["google_service_account"]
            return json.loads(raw) if isinstance(raw, str) else dict(raw)
        if "GOOGLE_SERVICE_ACCOUNT_JSON" in st.secrets:
            return json.loads(st.secrets["GOOGLE_SERVICE_ACCOUNT_JSON"])
    except Exception:
        return None
    return None


def _load_service_account_info_from_env() -> Optional[dict]:
    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def _load_oauth_user_info_from_streamlit() -> Optional[Dict[str, str]]:
    """Return dict {credentials_json, token_json} for OAuth user creds, or None."""
    try:
        import streamlit as st  # type: ignore
    except Exception:
        return None
    if not hasattr(st, "secrets"):
        return None
    try:
        oauth_credentials: Optional[str] = None
        oauth_token: Optional[str] = None
        if "google_oauth" in st.secrets:
            block = st.secrets["google_oauth"]
            if isinstance(block, str):
                block = json.loads(block)
            oauth_credentials = block.get("credentials_json") or block.get("credentials")
            oauth_token = block.get("token_json") or block.get("token")
        oauth_credentials = oauth_credentials or st.secrets.get("GOOGLE_OAUTH_CREDENTIALS_JSON")
        oauth_token = oauth_token or st.secrets.get("GOOGLE_OAUTH_TOKEN_JSON")
        if oauth_credentials and oauth_token:
            if not isinstance(oauth_credentials, str):
                oauth_credentials = json.dumps(oauth_credentials)
            if not isinstance(oauth_token, str):
                oauth_token = json.dumps(oauth_token)
            return {"credentials_json": oauth_credentials, "token_json": oauth_token}
    except Exception:
        return None
    return None


def _build_credentials():
    """
    Resolve Google credentials usable with google-api-python-client.

    Order matches GoogleSheetsClient._authenticate:
    1. Streamlit secrets / env GOOGLE_SERVICE_ACCOUNT_JSON (service account)
    2. Streamlit secrets google_oauth (OAuth user credentials)
    3. Local OAuth token file (whatever GoogleSheetsClient was using)
    """
    sa_info = (
        _load_service_account_info_from_streamlit()
        or _load_service_account_info_from_env()
    )
    if sa_info:
        from google.oauth2.service_account import Credentials as SACredentials
        return SACredentials.from_service_account_info(sa_info, scopes=SCOPES)

    oauth = _load_oauth_user_info_from_streamlit()
    if oauth:
        from google.oauth2.credentials import Credentials as UserCredentials
        token_info = json.loads(oauth["token_json"])
        return UserCredentials.from_authorized_user_info(token_info, scopes=SCOPES)

    # Fallback: local OAuth token file used by gspread.oauth
    try:
        from config.settings import settings
        token_path = Path(settings.google_oauth_token_path)
    except Exception:
        token_path = None

    if token_path and token_path.exists():
        from google.oauth2.credentials import Credentials as UserCredentials
        token_info = json.loads(token_path.read_text(encoding="utf-8"))
        return UserCredentials.from_authorized_user_info(token_info, scopes=SCOPES)

    raise RuntimeError(
        "No Google credentials available. Configure GOOGLE_SERVICE_ACCOUNT_JSON "
        "in env/Streamlit secrets, or a local OAuth token file."
    )


# =============================================================================
# Public uploader
# =============================================================================

def upload_xlsx_as_google_sheet(
    xlsx_bytes: bytes,
    name: str,
    folder_id: Optional[str] = None,
) -> Dict[str, str]:
    """
    Upload an xlsx blob to Drive, converting it to a native Google Sheet.

    Args:
        xlsx_bytes: Workbook bytes (e.g. from ``build_budget_workbook``).
        name: Display name for the new Google Sheet (e.g. "Budget 2026 — 2026-05-15").
        folder_id: Drive folder ID where the file lands. If None, uploads to
            the service account's "My Drive" root (rarely useful — set it).

    Returns:
        ``{"id": <fileId>, "url": <https://docs.google.com/spreadsheets/d/.../edit>, "webViewLink": <link>}``

    Raises:
        ImportError if google-api-python-client is missing.
        RuntimeError on credential resolution failure.
        googleapiclient.errors.HttpError on Drive API errors.
    """
    try:
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaIoBaseUpload
    except ImportError as e:
        raise ImportError(
            "google-api-python-client is required for Drive uploads. "
            "Install with `pip install google-api-python-client`."
        ) from e

    credentials = _build_credentials()
    drive = build('drive', 'v3', credentials=credentials, cache_discovery=False)

    metadata: Dict[str, Any] = {
        "name": name,
        "mimeType": SHEET_MIME,  # ← triggers conversion to native Google Sheet
    }
    if folder_id:
        metadata["parents"] = [folder_id]

    media = MediaIoBaseUpload(
        io.BytesIO(xlsx_bytes),
        mimetype=XLSX_MIME,
        resumable=False,
    )

    created = drive.files().create(
        body=metadata,
        media_body=media,
        fields="id, webViewLink",
        supportsAllDrives=True,
    ).execute()

    file_id = created.get("id")
    web_view_link = created.get("webViewLink") or (
        f"https://docs.google.com/spreadsheets/d/{file_id}/edit"
    )

    return {
        "id": file_id,
        "url": web_view_link,
        "webViewLink": web_view_link,
    }
