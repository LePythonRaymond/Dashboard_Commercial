"""
Furious API Authentication Module

Handles JWT token acquisition and caching for the Furious CRM API.
"""

import time
import requests
from typing import Optional
from dataclasses import dataclass

from config.settings import settings


@dataclass
class TokenInfo:
    """Stores JWT token and expiration info."""
    token: str
    expires_at: float  # Unix timestamp


class FuriousAuth:
    """
    Handles authentication with the Furious CRM API.

    Manages JWT token lifecycle including acquisition and auto-refresh.
    """

    def __init__(
        self,
        username: Optional[str] = None,
        password: Optional[str] = None,
        api_url: Optional[str] = None
    ):
        """
        Initialize the auth client.

        Args:
            username: Furious API username (defaults to settings)
            password: Furious API password (defaults to settings)
            api_url: Base API URL (defaults to settings)
        """
        self.username = username or settings.furious_username
        self.password = password or settings.furious_password
        self.api_url = api_url or settings.furious_api_url
        self.auth_endpoint = f"{self.api_url}/auth/"
        self._token_info: Optional[TokenInfo] = None
        self._token_buffer_seconds = 60  # Refresh 60s before expiry

    def _is_token_valid(self) -> bool:
        """Check if current token is still valid."""
        if self._token_info is None:
            return False
        return time.time() < (self._token_info.expires_at - self._token_buffer_seconds)

    def _fetch_token(self) -> TokenInfo:
        """
        Fetch a new JWT token from the API.

        Returns:
            TokenInfo with token and expiration time

        Raises:
            AuthenticationError: If authentication fails
        """
        payload = {
            "action": "auth",
            "data": {
                "username": self.username,
                "password": self.password
            }
        }

        headers = {
            "Content-Type": "application/json"
        }

        try:
            response = requests.post(
                self.auth_endpoint,
                json=payload,
                headers=headers,
                timeout=settings.api_timeout
            )
            response.raise_for_status()
            data = response.json()

            if not data.get("success"):
                error_msg = data.get("message", "Authentication failed")
                raise AuthenticationError(f"API authentication failed: {error_msg}")

            token = data["token"]
            expires_in = data.get("expires_in", 3600)  # Default 1 hour
            expires_at = time.time() + expires_in

            return TokenInfo(token=token, expires_at=expires_at)

        except requests.RequestException as e:
            raise AuthenticationError(f"Failed to connect to auth endpoint: {e}")

    def get_token(self) -> str:
        """
        Get a valid JWT token, refreshing if necessary.

        Returns:
            Valid JWT token string
        """
        if not self._is_token_valid():
            self._token_info = self._fetch_token()
        return self._token_info.token

    def get_headers(self) -> dict:
        """
        Get headers with authentication token for API requests.

        Returns:
            Dict with Content-Type and F-Auth-Token headers
        """
        return {
            "Content-Type": "application/json",
            "F-Auth-Token": self.get_token()
        }

    def invalidate_token(self) -> None:
        """Force token refresh on next request."""
        self._token_info = None


class AuthenticationError(Exception):
    """Raised when authentication with Furious API fails."""
    pass
