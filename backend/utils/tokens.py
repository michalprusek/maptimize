"""Personal access token (PAT) helpers.

PATs let the remote MCP connector authenticate as a specific user. The format is
a recognizable prefix + a 256-bit secret; only the sha256 hex of the full
plaintext is stored, so a leaked database never yields a usable token. The
prefix makes tokens greppable/scannable and lets the auth layer cheaply tell a
PAT apart from a JWT.
"""
from __future__ import annotations

import hashlib
import secrets

PAT_PREFIX = "mtk_pat_"


def hash_pat(plaintext: str) -> str:
    """sha256 hex of the full plaintext token (fast is correct — token is high-entropy)."""
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def generate_pat() -> tuple[str, str, str]:
    """Return ``(plaintext, token_hash, token_prefix)``.

    The plaintext is shown to the user exactly once; only the hash and a short
    display prefix are persisted.
    """
    plaintext = f"{PAT_PREFIX}{secrets.token_urlsafe(32)}"
    return plaintext, hash_pat(plaintext), plaintext[:12]


def looks_like_pat(token: str) -> bool:
    return token.startswith(PAT_PREFIX)
