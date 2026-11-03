"""Pure-python X25519 (RFC 7748).

Only what a WireGuard/AmneziaWG config generator needs: scalar multiplication on
Curve25519 and the base point. No third-party crypto dependency, so the bot runs on a
bare python3 install and the generated keys can be audited line by line.
"""

P = 2 ** 255 - 19
A24 = 121665
BASE_POINT = b"\x09" + b"\x00" * 31


def _clamp(k):
    k = bytearray(k)
    k[0] &= 248
    k[31] &= 127
    k[31] |= 64
    return bytes(k)


def x25519(scalar, u):
    """Return the u-coordinate of scalar*u as 32 little-endian bytes."""
    if len(scalar) != 32 or len(u) != 32:
        raise ValueError("x25519 expects 32-byte inputs")

    k = int.from_bytes(_clamp(scalar), "little")
    # RFC 7748: only the low 255 bits of the u-coordinate are used (mask, not mod p).
    x1 = int.from_bytes(u, "little") & ((1 << 255) - 1)
    x2, z2, x3, z3 = 1, 0, x1, 1
    swap = 0

    for t in reversed(range(255)):
        kt = (k >> t) & 1
        swap ^= kt
        if swap:
            x2, x3 = x3, x2
            z2, z3 = z3, z2
        swap = kt

        a = (x2 + z2) % P
        aa = a * a % P
        b = (x2 - z2) % P
        bb = b * b % P
        e = (aa - bb) % P
        c = (x3 + z3) % P
        d = (x3 - z3) % P
        da = d * a % P
        cb = c * b % P
        x3 = (da + cb) ** 2 % P
        z3 = x1 * (da - cb) ** 2 % P
        x2 = aa * bb % P
        z2 = e * (aa + A24 * e) % P

    if swap:
        x2, x3 = x3, x2
        z2, z3 = z3, z2

    return (x2 * pow(z2, P - 2, P) % P).to_bytes(32, "little")


def public_key(private_key):
    """Derive the public key (clamped scalar * base point)."""
    return x25519(private_key, BASE_POINT)
