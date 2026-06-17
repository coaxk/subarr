"""#259: managed API keys — pure key-generation + hashing helpers (no DB).

Keys are high-entropy random tokens, so a single SHA-256 is the right hash:
unlike passwords (low-entropy → slow pbkdf2), there is nothing to brute-force,
and an unsalted deterministic hash lets the store index `token_hash` and verify
a presented key in O(1). This is the GitHub/Stripe personal-access-token model.
"""

from __future__ import annotations

import hashlib
import secrets

# Visible, greppable prefix — makes a subarr key recognizable in logs/configs.
KEY_PREFIX = "sbar_"


def hash_key(token: str) -> str:
    """SHA-256 hex of the full token. The verify path hashes the presented key
    and looks the row up by this value."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_key() -> tuple[str, str, str]:
    """Mint a new key. Returns (token, token_hash, last4):
    - token: the full plaintext, shown to the user exactly once.
    - token_hash: what we persist (the plaintext is never stored).
    - last4: last 4 chars, persisted for masked display (sbar_…WXYZ).
    """
    token = KEY_PREFIX + secrets.token_urlsafe(32)
    return token, hash_key(token), token[-4:]
