"""vpn:// link format — must match what the real AmneziaVPN client imports."""

import base64
import json
import struct
import zlib

import pytest

from awgbot import vpnlink

CONF = """[Interface]
PrivateKey = oN+fPRIVEXAMPLEKEYVALUE0000000000000000000000=
Address = 10.8.0.2/32
DNS = 1.1.1.1
MTU = 1280
Jc = 4
Jmin = 40
Jmax = 70
S1 = 25
S2 = 100
S3 = 8
S4 = 12
H1 = 1234567
H2 = 2345678-2345799
H3 = 3456789
H4 = 4567890
I1 = <b 0x0100><r 2>
I4 = <t>

[Peer]
PublicKey = SERVERPUBEXAMPLE=
PresharedKey = PSKEXAMPLE=
AllowedIPs = 0.0.0.0/0, ::/0
Endpoint = vpn.example.com:51820
PersistentKeepalive = 25
"""


# --- Qt qCompress / qUncompress layout -------------------------------------
def test_qcompress_is_qt_compatible():
    raw = b'{"a":1}'
    blob = vpnlink.qcompress(raw)
    assert struct.unpack(">I", blob[:4])[0] == len(raw)      # big-endian length prefix
    assert blob[4] == 0x78                                   # zlib header follows
    assert zlib.decompress(blob[4:]) == raw
    assert vpnlink.quncompress(blob) == raw


def test_quncompress_rejects_garbage():
    with pytest.raises(vpnlink.VpnLinkError):
        vpnlink.quncompress(b"\x00\x01")
    with pytest.raises(vpnlink.VpnLinkError):
        vpnlink.quncompress(b"\x00\x00\x00\x01not-zlib")


# --- link shape -------------------------------------------------------------
def test_link_prefix_base64url_and_padding():
    link = vpnlink.build_vpn_link(CONF)
    assert link.startswith("vpn://")
    body = link[len("vpn://"):]
    assert "=" not in body                                  # OmitTrailingEquals
    assert set(body) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
    # decoding requires re-padding, exactly like the Qt side
    assert base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))


def test_envelope_matches_amnezia_import_contract():
    envelope = vpnlink.decode_vpn_link(vpnlink.build_vpn_link(CONF, description="iphone"))
    assert envelope["defaultContainer"] == "amnezia-awg2"   # CONF carries S3/S4
    assert envelope["hostName"] == "vpn.example.com"
    assert envelope["description"] == "iphone"
    assert envelope["dns1"] == "1.1.1.1"
    containers = envelope["containers"]
    assert len(containers) == 1
    assert containers[0]["container"] == "amnezia-awg2"
    awg = containers[0]["awg"]
    assert awg["isThirdPartyConfig"] is True
    assert awg["transport_proto"] == "udp"
    assert awg["port"] == "51820"                           # string on the awg object
    assert isinstance(awg["last_config"], str)              # double-serialised


def test_last_config_structured_fields():
    envelope = vpnlink.decode_vpn_link(vpnlink.build_vpn_link(CONF))
    last = vpnlink.decode_last_config(envelope)

    # fields the connect path reads with getString()/getInt()
    assert last["client_priv_key"].startswith("oN+fPRIV")
    assert last["client_ip"] == "10.8.0.2/32"
    assert last["server_pub_key"] == "SERVERPUBEXAMPLE="
    assert last["psk_key"] == "PSKEXAMPLE="
    assert last["hostName"] == "vpn.example.com"
    assert last["port"] == 51820 and isinstance(last["port"], int)   # number here
    assert last["mtu"] == "1280"
    assert last["persistent_keep_alive"] == "25"
    assert last["isObfuscationEnabled"] is True
    assert last["allowed_ips"] == ["0.0.0.0/0", "::/0"]     # array, not a string
    assert last["config"].startswith("[Interface]")         # verbatim INI for display

    # every obfuscation param, verbatim (ranges and CPS tags untouched)
    for key, value in [("Jc", "4"), ("Jmin", "40"), ("Jmax", "70"),
                       ("S1", "25"), ("S2", "100"), ("S3", "8"), ("S4", "12"),
                       ("H1", "1234567"), ("H2", "2345678-2345799"),
                       ("H3", "3456789"), ("H4", "4567890"),
                       ("I1", "<b 0x0100><r 2>"), ("I4", "<t>")]:
        assert last[key] == value, key


def test_no_obfuscation_means_flag_false_and_no_junk_keys():
    plain = "\n".join(line for line in CONF.splitlines()
                      if not line.split(" ")[0] in
                      {"Jc", "Jmin", "Jmax", "S1", "S2", "S3", "S4",
                       "H1", "H2", "H3", "H4", "I1", "I4"})
    last = vpnlink.decode_last_config(vpnlink.decode_vpn_link(vpnlink.build_vpn_link(plain)))
    assert last["isObfuscationEnabled"] is False
    assert "Jc" not in last and "H1" not in last


def test_rejects_config_without_keys():
    with pytest.raises(vpnlink.VpnLinkError):
        vpnlink.build_vpn_link("[Interface]\nAddress = 10.8.0.2/32\n")


def test_decode_rejects_foreign_strings():
    with pytest.raises(vpnlink.VpnLinkError):
        vpnlink.decode_vpn_link("https://example.com")
    with pytest.raises(vpnlink.VpnLinkError):
        vpnlink.decode_vpn_link("vpn://AAAA")


def test_parse_ini_and_endpoint_helpers():
    kv = vpnlink.parse_ini(CONF)
    assert kv["Endpoint"] == "vpn.example.com:51820"
    assert "[Interface]" not in kv
    assert vpnlink.split_endpoint("vpn.example.com:51820") == ("vpn.example.com", 51820)
    assert vpnlink.split_endpoint("vpn.example.com") == ("vpn.example.com", 51820)
    assert vpnlink.split_endpoint("[2001:db8::1]:4443") == ("2001:db8::1", 4443)


def test_link_size_is_chat_friendly():
    # must stay far below Telegram's 4096-char message limit for a real config
    link = vpnlink.build_vpn_link(CONF)
    assert len(link) < 1500


def test_awg2_server_gets_awg2_container():
    # S3/S4 = AWG 2.0 junk params → Amnezia ships that as amnezia-awg2
    env = vpnlink.decode_vpn_link(vpnlink.build_vpn_link(CONF))
    assert env["containers"][0]["container"] == "amnezia-awg2"
    assert env["defaultContainer"] == "amnezia-awg2"

    legacy = "\n".join(line for line in CONF.splitlines()
                       if not line.startswith(("S3", "S4")))
    env2 = vpnlink.decode_vpn_link(vpnlink.build_vpn_link(legacy))
    assert env2["containers"][0]["container"] == "amnezia-awg"


def test_container_name_can_be_overridden():
    env = vpnlink.decode_vpn_link(vpnlink.build_vpn_link(CONF, container="amnezia-awg"))
    assert env["containers"][0]["container"] == "amnezia-awg"


AWG31_INI = CONF.replace("H4 = 4567890", """H4 = 4567890
HeaderProtectionKey = J0GHEQVvL63s2KqUZfqE4KNfx8Gcx8bWEcm6HKaeB7Q=
RekeyAfterTime = 100-120
RekeyTimeout = 3-7
RejectAfterTime = 150-180
KeepaliveTimeout = 5-15
MaxHandshakeAttempts = 15-20
RandomTrailers = on
DisableCookies = on""")


def test_awg31_params_land_in_last_config():
    last = vpnlink.decode_last_config(
        vpnlink.decode_vpn_link(vpnlink.build_vpn_link(AWG31_INI)))
    assert last["HeaderProtectionKey"] == "J0GHEQVvL63s2KqUZfqE4KNfx8Gcx8bWEcm6HKaeB7Q="
    assert last["RekeyAfterTime"] == "100-120"
    assert last["RekeyTimeout"] == "3-7"
    assert last["RejectAfterTime"] == "150-180"
    assert last["KeepaliveTimeout"] == "5-15"
    assert last["MaxHandshakeAttempts"] == "15-20"
    assert last["RandomTrailers"] == "on"
    assert last["DisableCookies"] == "on"


def test_protocol_version_is_explicit():
    env31 = vpnlink.decode_vpn_link(vpnlink.build_vpn_link(AWG31_INI))
    assert env31["containers"][0]["awg"]["protocol_version"] == "3.1"   # awgV3 constant

    env2 = vpnlink.decode_vpn_link(vpnlink.build_vpn_link(CONF))        # S3/S4, no 3.x markers
    assert env2["containers"][0]["awg"]["protocol_version"] == "2"

    legacy = "\n".join(line for line in CONF.splitlines()
                       if not line.startswith(("S3", "S4")))
    # H2 is a range in this fixture, and the client treats ranged headers as v2 as well
    assert vpnlink.decode_vpn_link(
        vpnlink.build_vpn_link(legacy))["containers"][0]["awg"]["protocol_version"] == "2"

    no_ranges = legacy.replace("H2 = 2345678-2345799", "H2 = 2345678")
    assert vpnlink.decode_vpn_link(
        vpnlink.build_vpn_link(no_ranges))["containers"][0]["awg"]["protocol_version"] == "1.5"

    plain = "\n".join(line for line in no_ranges.splitlines()
                      if not line.startswith(("I1", "I4")))
    assert "protocol_version" not in vpnlink.decode_vpn_link(
        vpnlink.build_vpn_link(plain))["containers"][0]["awg"]


def test_protocol_version_follows_range_headers():
    ranged = CONF.replace("H1 = 1234567", "H1 = 1778950080-1816592595")
    ranged = "\n".join(line for line in ranged.splitlines()
                       if not line.startswith(("S3", "S4")))
    assert vpnlink.protocol_version_for(vpnlink.parse_ini(ranged)) == "2"
