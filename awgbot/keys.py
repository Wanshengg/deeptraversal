"""WireGuard/AmneziaWG key material: generation and base64 wire format."""

import base64
import os

from .curve25519 import public_key as _public_key

KEY_LEN = 32


def b64(raw):
    return base64.b64encode(raw).decode("ascii")


def ub64(text):
    raw = base64.b64decode(text.strip(), validate=True)
    if len(raw) != KEY_LEN:
        raise ValueError("not a 32-byte key")
    return raw


def generate_private_key():
    """Random clamped private key, base64 encoded (same bytes `awg genkey` prints)."""
    raw = bytearray(os.urandom(KEY_LEN))
    raw[0] &= 248
    raw[31] &= 127
    raw[31] |= 64
    return b64(bytes(raw))


def public_key(private_key_b64):
    return b64(_public_key(ub64(private_key_b64)))


def generate_preshared_key():
    return b64(os.urandom(KEY_LEN))


def keypair():
    priv = generate_private_key()
    return priv, public_key(priv)
