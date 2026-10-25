import hashlib
import secrets

from cryptography.fernet import Fernet

from disha.config import get_settings


# TODO(phase-2): move to KMS-backed per-tenant DEKs.
def _fernet() -> Fernet:
    key = get_settings().disha_fernet_key.get_secret_value()
    if not key:
        raise RuntimeError(
            "DISHA_FERNET_KEY is not set. "
            'Generate one with: python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"'
        )
    return Fernet(key.encode())


def encrypt(plaintext: bytes | str) -> bytes:
    if isinstance(plaintext, str):
        plaintext = plaintext.encode("utf-8")
    return _fernet().encrypt(plaintext)


def decrypt(ciphertext: bytes) -> bytes:
    return _fernet().decrypt(ciphertext)


def new_tenant_salt() -> bytes:
    return secrets.token_bytes(16)


def hash_email(email: str, salt: bytes) -> bytes:
    normalized = email.strip().lower().encode("utf-8")
    return hashlib.sha256(salt + normalized).digest()
