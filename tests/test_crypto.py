import base64

from awgbot import curve25519, keys


# RFC 7748 section 6.1 test vectors
def test_rfc7748_vector_1():
    scalar = bytes.fromhex("a546e36bf0527c9d3b16154b82465edd62144c0ac1fc5a18506a2244ba449ac4")
    u = bytes.fromhex("e6db6867583030db3594c1a424b15f7c726624ec26b3353b10a903a6d0ab1c4c")
    out = curve25519.x25519(scalar, u)
    assert out.hex() == "c3da55379de9c6908e94ea4df28d084f32eccf03491c71f754b4075577a28552"


def test_rfc7748_vector_2():
    scalar = bytes.fromhex("4b66e9d4d1b4673c5ad22691957d6af5c11b6421e0ea01d42ca4169e7918ba0d")
    u = bytes.fromhex("e5210f12786811d3f4b7959d0538ae2c31dbe7106fc03c3efc4cd549c715a493")
    out = curve25519.x25519(scalar, u)
    assert out.hex() == "95cbde9476e8907d7aade45cb4b873f88b595a68799fa152e6f8f7647aac7957"


def test_rfc7748_diffie_hellman():
    a_priv = bytes.fromhex("77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a")
    b_priv = bytes.fromhex("5dab087e624a8a4b79e17f8b83800ee66f3bb1292618b6fd1c2f8b27ff88e0eb")
    a_pub = curve25519.x25519(a_priv, curve25519.BASE_POINT)
    b_pub = curve25519.x25519(b_priv, curve25519.BASE_POINT)
    assert a_pub.hex() == "8520f0098930a754748b7ddcb43ef75a0dbf3a0d26381af4eba4a98eaa9b4e6a"
    assert b_pub.hex() == "de9edb7d7b7dc1b4d35b61c2ece435373f8343c85b78674dadfc7e146f882b4f"
    assert curve25519.x25519(a_priv, b_pub) == curve25519.x25519(b_priv, a_pub)


def test_generated_keys_are_wireguard_shaped():
    priv, pub = keys.keypair()
    assert len(base64.b64decode(priv)) == 32
    assert len(base64.b64decode(pub)) == 32
    assert keys.public_key(priv) == pub
    # clamping is applied at generation time
    raw = base64.b64decode(priv)
    assert raw[0] & 7 == 0
    assert raw[31] & 128 == 0
    assert raw[31] & 64 == 64


def test_psk_is_random_and_32_bytes():
    first, second = keys.generate_preshared_key(), keys.generate_preshared_key()
    assert first != second
    assert len(base64.b64decode(first)) == 32


def test_ub64_rejects_wrong_length():
    import pytest
    with pytest.raises(ValueError):
        keys.ub64(base64.b64encode(b"short").decode())
