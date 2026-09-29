"""Settings of the EPI form, read from the environment (the .env shared with Myrium)."""

import os
from dataclasses import dataclass
from typing import List, Tuple

try:  # local runs; in Docker the variables come from env_file
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

# Notion data sources created on 2026-09-24 / 2026-09-29 (ids are not secrets).
DEFAULT_ARTICLES_DS = "78d8becc-ede9-45dc-abc0-1bd2055be98f"
DEFAULT_REGISTRE_DS = "29a47659-efdf-4feb-b5d6-9113ece0b12d"
DEFAULT_EQUIPE_DS = "d8d704e0-4a8e-4481-82cd-0a56b6a6aace"
DEFAULT_DEMANDES_DS = "7629f962-0368-42ef-b41e-1cffbd6b8c67"
MIN_SECRET_LENGTH = 16


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _emails(raw: str) -> Tuple[str, ...]:
    return tuple(e.strip().lower() for e in raw.split(",") if e.strip())


@dataclass(frozen=True)
class Config:
    notion_token: str
    articles_ds: str
    registre_ds: str
    equipe_ds: str
    demandes_ds: str
    form_key: str             # secret part of the form link: /f/<form_key>
    signing_secret: str       # HMAC key of the validation links in the e-mails
    public_url: str           # e.g. https://epi.srv1082911.hstgr.cloud
    notify_emails: Tuple[str, ...]
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    mail_dry_run_dir: str     # when set, e-mails are written there instead of sent

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            notion_token=_env("NOTION_API_KEY"),
            articles_ds=_env("EPI_ARTICLES_DS", DEFAULT_ARTICLES_DS),
            registre_ds=_env("EPI_REGISTRE_DS", DEFAULT_REGISTRE_DS),
            equipe_ds=_env("EPI_EQUIPE_DS", DEFAULT_EQUIPE_DS),
            demandes_ds=_env("EPI_DEMANDES_DS", DEFAULT_DEMANDES_DS),
            form_key=_env("EPI_FORM_KEY"),
            signing_secret=_env("EPI_SIGNING_SECRET"),
            public_url=_env("EPI_PUBLIC_URL", "http://localhost:8000").rstrip("/"),
            notify_emails=_emails(_env("EPI_NOTIFY_EMAILS")),
            smtp_host=_env("SMTP_HOST", "smtp.gmail.com"),
            smtp_port=int(_env("SMTP_PORT", "587") or 587),
            smtp_user=_env("SMTP_USER"),
            smtp_password=_env("SMTP_PASSWORD"),
            mail_dry_run_dir=_env("EPI_MAIL_DRY_RUN_DIR"),
        )

    def problems(self) -> List[str]:
        """What prevents the service from working, in plain words (empty list when ready)."""
        issues = []
        if not self.notion_token:
            issues.append("NOTION_API_KEY manquant")
        if len(self.form_key) < MIN_SECRET_LENGTH:
            issues.append(f"EPI_FORM_KEY manquant ou trop court (min {MIN_SECRET_LENGTH} caractères)")
        if len(self.signing_secret) < MIN_SECRET_LENGTH:
            issues.append(f"EPI_SIGNING_SECRET manquant ou trop court (min {MIN_SECRET_LENGTH} caractères)")
        if not self.notify_emails:
            issues.append("EPI_NOTIFY_EMAILS vide : personne ne recevra les demandes")
        if not self.mail_dry_run_dir and not (self.smtp_user and self.smtp_password):
            issues.append("SMTP_USER / SMTP_PASSWORD manquants : les e-mails ne partiront pas")
        return issues
