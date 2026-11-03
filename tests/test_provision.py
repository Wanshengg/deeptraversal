import base64
import os
import stat

import pytest

from awgbot import clientconf
from awgbot.provision import ProvisionError, Provisioner, safe_device_name


def test_issue_creates_peer_conf_and_export(prov, store, backend, cfg):
    peer, conf, filename = prov.issue(100, "iphone")
    assert peer.ip == "10.8.0.2"
    assert filename == "amnezia-iphone-10.8.0.2.conf"
    assert "Address = 10.8.0.2/32" in conf
    assert "Endpoint = vpn.example.com:51820" in conf
    # obfuscation params from the (mock) server config are copied into the client file
    assert "Jc = 4" in conf and "H1 = 1234567" in conf
    assert "PrivateKey = %s" % peer.private_key in conf
    # keys are consistent: public key must be derivable from the stored private key
    from awgbot import keys
    assert keys.public_key(peer.private_key) == peer.public_key
    assert len(base64.b64decode(peer.preshared_key)) == 32
    # backend knows the peer
    assert backend.live_peers() == [peer.public_key]
    # export file is written 0600
    export = os.path.join(prov.export_dir(), filename)
    assert os.path.exists(export)
    assert stat.S_IMODE(os.stat(export).st_mode) == 0o600


def test_second_issue_replaces_the_first_peer(prov, store):
    first, _, _ = prov.issue(100, "iphone")
    second, _, _ = prov.issue(100, "laptop")
    active = store.active_peers(100)
    assert [p.id for p in active] == [second.id]
    assert active[0].ip == first.ip            # address is recycled, not leaked
    assert store.used_ips() == {first.ip}


def test_max_peers_per_user_is_enforced(cfg, store, backend):
    cfg.max_peers_per_user = 2
    prov = Provisioner(cfg, store, backend)
    prov.issue(100, "a")
    prov.issue(100, "b")
    third, _, _ = prov.issue(100, "c")
    assert len(store.active_peers(100)) == 2
    assert third.device == "c"
    assert store.active_peers(100)[-1].device == "c"


def test_pool_exhaustion_raises(prov, store):
    for i in range(5):                       # /29 has 6 hosts, one is the server
        prov.issue(1000 + i, "dev%s" % i)
    with pytest.raises(ProvisionError) as exc:
        prov.issue(2000, "too-many")
    assert "exhausted" in str(exc.value)
    assert not store.active_peers(2000)


def test_revoke_frees_address_and_updates_backend(prov, store, backend):
    peer, _, _ = prov.issue(100, "iphone")
    assert prov.revoke_all(100) == 1
    assert store.active_peers(100) == []
    assert backend.live_peers() == []
    assert store.used_ips() == set()
    again, _, _ = prov.issue(100, "iphone")
    assert again.ip == peer.ip


def test_resend_returns_existing_config(prov):
    peer, conf, _ = prov.issue(100, "iphone")
    result = prov.resend(100)
    assert result is not None
    _, again, filename = result
    assert again == conf
    assert filename.endswith(".conf")


def test_issue_rolls_back_when_backend_fails(cfg, store, backend, prov):
    from awgbot.awg import BackendError

    def boom(peers):
        raise BackendError("syncconf failed")

    backend.sync = boom
    with pytest.raises(BackendError):
        prov.issue(100, "iphone")
    assert store.active_peers(100) == []


def test_audit_log_is_written(prov, store):
    prov.issue(100, "iphone")
    rows = store.conn.execute("SELECT action FROM audit").fetchall()
    assert [r[0] for r in rows] == ["issue"]


@pytest.mark.parametrize("raw,expected", [
    ("iphone", "iphone"),
    ("../../etc/passwd", "etcpasswd"),
    ("my laptop!", "mylaptop"),
    ("", "device"),
    ("....", "device"),
    ("a" * 80, "a" * 32),
])
def test_safe_device_name_strips_paths(raw, expected):
    assert safe_device_name(raw) == expected


def test_server_pubkey_used_verbatim_in_conf(prov, cfg):
    _, conf, _ = prov.issue(100, "iphone")
    assert "PublicKey = %s" % cfg.server_public_key in conf
    assert clientconf.MANAGED_BEGIN not in conf
