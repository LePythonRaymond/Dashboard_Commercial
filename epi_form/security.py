"""
Signed links for the e-mails, and constant-time key checks.

A validation link carries the request id, the action and the recipient, plus an
HMAC of the three computed with EPI_SIGNING_SECRET. Changing any part (another
request, "refuser" instead of "valider", another person) breaks the signature,
and nobody can forge a link without the secret.

Example: /v/<request id>/valider/<recipient>/<signature>
"""

import base64
import hashlib
import hmac

SIGNATURE_LENGTH = 40


def sign(secret: str, *parts: str) -> str:
    message = "|".join(parts).encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()[:SIGNATURE_LENGTH]


def check(secret: str, signature: str, *parts: str) -> bool:
    return bool(secret) and hmac.compare_digest(sign(secret, *parts), signature or "")


def same_key(expected: str, given: str) -> bool:
    """True when the key in the URL is the configured one (never true for an empty key)."""
    return bool(expected) and hmac.compare_digest(expected.encode(), (given or "").encode())


def encode_recipient(email: str) -> str:
    return base64.urlsafe_b64encode(email.lower().encode()).decode().rstrip("=")


def decode_recipient(token: str) -> str:
    padded = token + "=" * (-len(token) % 4)
    return base64.urlsafe_b64decode(padded.encode()).decode()
