import re

from awgbot import clientconf
from awgbot.store import Peer

SERVER_CONF = """[Interface]
Address = 10.8.0.1/24
ListenPort = 51820
PrivateKey = {priv}
MTU = 1280
Jc = 4
Jmin = 40
Jmax = 70
S1 = 25
S2 = 100
H1 = 1234567
H2 = 2345678-2345799
H3 = 3456789
H4 = 4567890
PostUp = iptables -A FORWARD -i %i -j ACCEPT; iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
PostDown = iptables -D FORWARD -i %i -j ACCEPT
SaveConfig = false

[Peer]
# manual peer, added by hand before the bot existed
PublicKey = manualpeerkey
AllowedIPs = 10.8.0.9/32
"""


def test_obfuscation_params_are_read_in_canonical_order():
    params = dict(clientconf.server_obfuscation(SERVER_CONF.format(priv="x")))
    assert params["Jc"] == "4"
    assert params["H2"] == "2345678-2345799"      # ranges are copied verbatim
    assert "Address" not in params
    assert list(dict(clientconf.server_obfuscation(SERVER_CONF.format(priv="x")))) == [
        "Jc", "Jmin", "Jmax", "S1", "S2", "H1", "H2", "H3", "H4"]


def test_strip_for_syncconf_drops_quick_only_keys():
    stripped = clientconf.strip_for_syncconf(SERVER_CONF.format(priv="abc"))
    assert "Address" not in stripped
    assert "PostUp" not in stripped
    assert "SaveConfig" not in stripped
    assert "ListenPort = 51820" in stripped
    assert "PrivateKey = abc" in stripped
    assert "manualpeerkey" in stripped        # hand-made peers survive
    assert stripped.count("[Interface]") == 1
    assert stripped.count("[Peer]") == 1


def test_apply_managed_peers_is_idempotent_and_preserves_manual_peers():
    peers = [Peer.from_row({"id": 1, "tg_id": 5, "device": "iphone",
                            "public_key": "pubA", "private_key": "privA",
                            "preshared_key": "pskA", "ip": "10.8.0.2",
                            "created_at": 0, "revoked_at": None})]
    once = clientconf.apply_managed_peers(SERVER_CONF.format(priv="abc"), peers)
    twice = clientconf.apply_managed_peers(once, peers)
    assert once == twice
    assert "manualpeerkey" in once
    assert once.count(clientconf.MANAGED_BEGIN) == 1
    assert "PublicKey = pubA" in once
    assert "AllowedIPs = 10.8.0.2/32" in once


def test_apply_managed_peers_removes_block_when_empty():
    peers = [Peer.from_row({"id": 1, "tg_id": 5, "device": "d", "public_key": "pubA",
                            "private_key": "p", "preshared_key": "k", "ip": "10.8.0.2",
                            "created_at": 0, "revoked_at": None})]
    with_peers = clientconf.apply_managed_peers(SERVER_CONF.format(priv="abc"), peers)
    cleared = clientconf.apply_managed_peers(with_peers, [])
    assert clientconf.MANAGED_BEGIN not in cleared
    assert "pubA" not in cleared
    assert "manualpeerkey" in cleared


def test_render_client_config_contains_everything_the_app_needs(cfg):
    conf = clientconf.render_client_config(
        cfg, "PRIVKEY", "PSK", "10.8.0.2", "SRVPUB",
        obfuscation=[("Jc", "4"), ("H1", "1234567")], mtu=1280,
    )
    assert "[Interface]" in conf and "[Peer]" in conf
    for line in ["PrivateKey = PRIVKEY", "Address = 10.8.0.2/32", "DNS = 1.1.1.1",
                 "MTU = 1280", "Jc = 4", "H1 = 1234567",
                 "PublicKey = SRVPUB", "PresharedKey = PSK",
                 "AllowedIPs = 0.0.0.0/0, ::/0",
                 "Endpoint = vpn.example.com:51820", "PersistentKeepalive = 25"]:
        assert line in conf, line
    # the interface section must come before the peer section
    assert conf.index("[Interface]") < conf.index("[Peer]")
    # no server-only leftovers
    assert "PostUp" not in conf and "ListenPort" not in conf


def test_server_public_key_is_derived_from_private_key(cfg):
    from awgbot import keys
    priv = keys.generate_private_key()
    cfg.server_public_key = ""
    text = SERVER_CONF.format(priv=priv)
    assert clientconf.resolve_server_pubkey(cfg, text) == keys.public_key(priv)


def test_resolve_returns_none_without_key(cfg):
    cfg.server_public_key = ""
    assert clientconf.resolve_server_pubkey(cfg, "[Interface]\nJc = 4\n") is None


def test_existing_peer_ips_ignores_managed_region_and_multi_allowedips():
    text = """[Interface]
PrivateKey = k

[Peer]
PublicKey = manual1
AllowedIPs = 10.8.0.9/32, 192.168.50.0/24

[Peer]
PublicKey = manual2
AllowedIPs = 10.8.1.0/32

# >>> awgbot managed peers (do not edit by hand)
[Peer]
# awgbot
PublicKey = botpeer
AllowedIPs = 10.8.0.2/32
# <<< awgbot managed peers
"""
    assert clientconf.existing_peer_ips(text) == {"10.8.0.9", "192.168.50.0", "10.8.1.0"}


def test_existing_peer_ips_on_empty_config():
    assert clientconf.existing_peer_ips("") == set()
    assert clientconf.existing_peer_ips("[Interface]\nPrivateKey = k\n") == set()


def test_s3_s4_are_copied_to_the_client_config():
    text = SERVER_CONF.format(priv="k").replace(
        "SaveConfig = false", "SaveConfig = false\nS3 = 43\nS4 = 2")
    params = dict(clientconf.server_obfuscation(text))
    assert params["S3"] == "43" and params["S4"] == "2"
