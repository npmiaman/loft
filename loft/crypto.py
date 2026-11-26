"""End-to-end encryption for team data.

Every team has a 256-bit key that only clients hold. Fingerprints, proxies,
template settings and browser-data archives are AES-256-GCM encrypted with it
before upload, so the server only ever stores ciphertext.

Key distribution:
  * the team creator generates the key;
  * an invite is `<server code>.<base64 key>`, and only the server code is sent to the server;
  * each member uploads the key wrapped with a key derived from their password
    (scrypt), so a new device can recover it at login.
"""
from __future__ import annotations

import base64
import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"LFT1"


def new_key() -> bytes:
    return AESGCM.generate_key(bit_length=256)


def b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def encrypt(key: bytes, data: bytes, aad: str = "") -> bytes:
    nonce = os.urandom(12)
    return MAGIC + nonce + AESGCM(key).encrypt(nonce, data, aad.encode())


def decrypt(key: bytes, blob: bytes, aad: str = "") -> bytes:
    if blob[:4] != MAGIC:
        raise ValueError("not a Loft ciphertext")
    return AESGCM(key).decrypt(blob[4:16], blob[16:], aad.encode())


def enc_text(key: bytes, text: str, aad: str = "") -> str:
    return b64e(encrypt(key, text.encode(), aad))


def dec_text(key: bytes, s: str, aad: str = "") -> str:
    return decrypt(key, b64d(s), aad).decode()


def vault_key(password: str, salt_b64: str) -> bytes:
    """Per-user key that wraps team keys. Never leaves the client."""
    return hashlib.scrypt(password.encode(), salt=b64d(salt_b64), n=2**15, r=8, p=1, maxmem=64 * 1024 * 1024, dklen=32)


def passphrase_key(passphrase: str, salt: bytes) -> bytes:
    return hashlib.scrypt(passphrase.encode(), salt=salt, n=2**15, r=8, p=1, maxmem=64 * 1024 * 1024, dklen=32)
