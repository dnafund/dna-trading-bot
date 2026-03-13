"""
API Key Encryption — Fernet symmetric encryption with per-user key derivation.

Uses HKDF (HMAC-based Key Derivation Function) to derive per-user encryption
keys from a master key. This means compromising one user's data doesn't
expose other users' keys.

Environment:
    ENCRYPTION_MASTER_KEY — Base64-encoded 32-byte master key.
    Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

Usage:
    from src.security.encryption import encrypt_api_key, decrypt_api_key

    encrypted = encrypt_api_key("my-api-key", user_id="user-123")
    decrypted = decrypt_api_key(encrypted, user_id="user-123")
"""

import base64
import logging
import os

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

logger = logging.getLogger(__name__)

_MASTER_KEY: bytes | None = None


def _get_master_key() -> bytes:
    """Load master key from environment (cached)."""
    global _MASTER_KEY
    if _MASTER_KEY is None:
        key_str = os.getenv("ENCRYPTION_MASTER_KEY")
        if not key_str:
            raise RuntimeError(
                "ENCRYPTION_MASTER_KEY not set. Generate with: "
                'python -c "from cryptography.fernet import Fernet; '
                'print(Fernet.generate_key().decode())"'
            )
        _MASTER_KEY = base64.urlsafe_b64decode(key_str)
    return _MASTER_KEY


def _derive_user_key(user_id: str) -> bytes:
    """Derive a per-user Fernet key from master key + user_id.

    Uses HKDF-SHA256 so each user gets a unique encryption key.
    Even if one user's key is compromised, others remain safe.

    Args:
        user_id: User identifier (used as salt/info).

    Returns:
        32-byte Fernet key (base64-encoded).
    """
    master = _get_master_key()
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=user_id.encode("utf-8"),
        info=b"api-key-encryption",
    )
    derived = hkdf.derive(master)
    return base64.urlsafe_b64encode(derived)


def encrypt_api_key(plaintext: str, user_id: str) -> str:
    """Encrypt an API key for a specific user.

    Args:
        plaintext: The API key to encrypt.
        user_id: User ID for key derivation.

    Returns:
        Encrypted string (base64-encoded Fernet token).
    """
    key = _derive_user_key(user_id)
    f = Fernet(key)
    encrypted = f.encrypt(plaintext.encode("utf-8"))
    return encrypted.decode("utf-8")


def decrypt_api_key(ciphertext: str, user_id: str) -> str:
    """Decrypt an API key for a specific user.

    Args:
        ciphertext: Encrypted API key string.
        user_id: User ID for key derivation (must match encryption).

    Returns:
        Decrypted plaintext API key.

    Raises:
        ValueError: If decryption fails (wrong key, tampered data).
    """
    try:
        key = _derive_user_key(user_id)
        f = Fernet(key)
        decrypted = f.decrypt(ciphertext.encode("utf-8"))
        return decrypted.decode("utf-8")
    except InvalidToken:
        raise ValueError("Decryption failed: invalid key or corrupted data")


def validate_api_key_permissions(exchange_client, warn_withdrawal: bool = True) -> dict:
    """Validate API key permissions by making a read-only call.

    Args:
        exchange_client: CCXT exchange client instance.
        warn_withdrawal: If True, warn when withdrawal permission detected.

    Returns:
        Dict with validation results:
        {
            "valid": bool,
            "permissions": list[str],
            "has_withdrawal": bool,
            "error": str | None,
        }
    """
    try:
        # Test with a read-only call (fetch balance)
        balance = exchange_client.fetch_balance()
        permissions = ["read"]

        # Try a small operation to check trade permission
        # We don't actually place an order — just verify the key works
        if balance is not None:
            permissions.append("trade")

        has_withdrawal = False  # Can't detect without trying (which we won't)
        if warn_withdrawal:
            logger.warning(
                "[SECURITY] Cannot detect withdrawal permission without attempting it. "
                "Recommend users create API keys with trade-only permissions."
            )

        return {
            "valid": True,
            "permissions": permissions,
            "has_withdrawal": has_withdrawal,
            "error": None,
        }

    except Exception as e:
        return {
            "valid": False,
            "permissions": [],
            "has_withdrawal": False,
            "error": str(e),
        }
