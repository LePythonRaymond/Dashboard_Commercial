"""External service integrations."""
from .email_sender import EmailSender

__all__ = ["GoogleSheetsClient", "EmailSender"]


def __getattr__(name: str):
    if name == "GoogleSheetsClient":
        from .google_sheets import GoogleSheetsClient

        return GoogleSheetsClient
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
