"""Provider-credential encryption (D13) — hidden inside Settings Store.

The master key lives in the OS keyring (`libsecret` via `keyring`), never in
the vault, the DB, or the repo. Provider keys are AES-256-GCM ciphertext;
plaintext exists only for the duration of a call.
"""

import base64
import os

import keyring
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_KEYRING_SERVICE = "research-companion-os"
_KEYRING_USERNAME = "provider-credentials-master-key"


def _master_key() -> bytes:
    stored = keyring.get_password(_KEYRING_SERVICE, _KEYRING_USERNAME)
    if stored is not None:
        return base64.b64decode(stored)
    key = AESGCM.generate_key(bit_length=256)
    keyring.set_password(_KEYRING_SERVICE, _KEYRING_USERNAME, base64.b64encode(key).decode())
    return key


def encrypt(plaintext: str) -> tuple[str, str]:
    """Returns `(ciphertext_b64, nonce_b64)`."""
    nonce = os.urandom(12)
    ciphertext = AESGCM(_master_key()).encrypt(nonce, plaintext.encode(), None)
    return base64.b64encode(ciphertext).decode(), base64.b64encode(nonce).decode()


def decrypt(ciphertext_b64: str, nonce_b64: str) -> str:
    ciphertext = base64.b64decode(ciphertext_b64)
    nonce = base64.b64decode(nonce_b64)
    return AESGCM(_master_key()).decrypt(nonce, ciphertext, None).decode()
