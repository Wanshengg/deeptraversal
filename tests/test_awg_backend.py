import os
import stat

import pytest

from awgbot import clientconf, keys
from awgbot.awg import AmneziaWG, BackendError
from awgbot.store import Peer


class Result(object):
    def __init__(self, returncode=0, stdout=b"", stderr=b""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeRunner(object):
    def __init__(self, returncode=0, stderr=b""):
        self.calls = []
        self.synced_texts = []
        self.returncode = returncode
        self.stderr = stderr

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        if "syncconf" in argv:
            path = argv[-1]
            with open(path, "r", encoding="utf-8") as fh:
                self.synced_texts.append(fh.read())
        return Result(self.returncode, b"", self.stderr)


def write_server_conf(path, priv):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("""[Interface]
Address = 10.8.0.1/24
ListenPort = 51820
PrivateKey = %s
MTU = 1280
Jc = 4
Jmin = 40
Jmax = 70
S1 = 25
S2 = 100
H1 = 1234567
H2 = 2345678
H3 = 3456789
H4 = 4567890
PostUp = iptables -A FORWARD -i %%i -j ACCEPT
PostDown = iptables -D FORWARD -i %%i -j ACCEPT

[Peer]
PublicKey = manualpeer
AllowedIPs = 10.8.0.9/32
""" % priv)
    os.chmod(path, 0o600)


def peer(pub="pubA", ip="10.8.0.2", tg_id=5):
    return Peer.from_row({"id": 1, "tg_id": tg_id, "device": "iphone", "public_key": pub,
                          "private_key": "privA", "preshared_key": "pskA", "ip": ip,
                          "created_at": 0, "revoked_at": None})


def test_sync_applies_stripped_config_then_writes_file(cfg):
    priv = keys.generate_private_key()
    write_server_conf(cfg.server_conf, priv)
    cfg.server_public_key = ""
    runner = FakeRunner()
    backend = AmneziaWG(cfg, runner=runner)

    assert backend.server_public_key() == keys.public_key(priv)
    backend.sync([peer()])

    assert runner.calls[0][:3] == ["awg", "syncconf", "awg0"]
    synced = runner.synced_texts[0]
    assert "PostUp" not in synced and "Address" not in synced
    assert "PublicKey = pubA" in synced and "manualpeer" in synced

    on_disk = open(cfg.server_conf, encoding="utf-8").read()
    assert clientconf.MANAGED_BEGIN in on_disk
    assert "PublicKey = pubA" in on_disk
    assert "PostUp" in on_disk                  # quick-only keys stay in the real file
    assert os.path.exists(cfg.server_conf + ".awgbot.bak")
    assert stat.S_IMODE(os.stat(cfg.server_conf).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(cfg.server_conf + ".awgbot.bak").st_mode) == 0o600


def test_failed_syncconf_leaves_config_untouched(cfg):
    write_server_conf(cfg.server_conf, keys.generate_private_key())
    before = open(cfg.server_conf, encoding="utf-8").read()
    backend = AmneziaWG(cfg, runner=FakeRunner(returncode=1, stderr=b"Operation not permitted"))
    with pytest.raises(BackendError) as exc:
        backend.sync([peer()])
    assert "Operation not permitted" in str(exc.value)
    assert open(cfg.server_conf, encoding="utf-8").read() == before


def test_dry_run_writes_file_without_calling_awg(cfg):
    write_server_conf(cfg.server_conf, keys.generate_private_key())
    runner = FakeRunner()
    backend = AmneziaWG(cfg, runner=runner, dry_run=True)
    backend.sync([peer()])
    assert runner.calls == []
    assert "PublicKey = pubA" in open(cfg.server_conf, encoding="utf-8").read()


def test_missing_server_key_raises(cfg):
    cfg.server_conf = str(cfg.db_path) + ".nokey.conf"
    with open(cfg.server_conf, "w", encoding="utf-8") as fh:
        fh.write("[Interface]\nJc = 4\n")
    cfg.server_public_key = ""
    backend = AmneziaWG(cfg, runner=FakeRunner())
    with pytest.raises(BackendError):
        backend.server_public_key()


def test_active_peers_reflect_revocation(cfg):
    write_server_conf(cfg.server_conf, keys.generate_private_key())
    runner = FakeRunner()
    backend = AmneziaWG(cfg, runner=runner)
    backend.sync([peer("pubA"), peer("pubB", ip="10.8.0.3", tg_id=6)])
    assert "pubB" in open(cfg.server_conf, encoding="utf-8").read()
    backend.sync([peer("pubA")])
    text = open(cfg.server_conf, encoding="utf-8").read()
    assert "pubB" not in text and "pubA" in text
    assert text.count(clientconf.MANAGED_BEGIN) == 1


def test_live_peers_parses_awg_show(cfg):
    write_server_conf(cfg.server_conf, keys.generate_private_key())
    backend = AmneziaWG(cfg, runner=lambda argv, **kw: Result(0, b"keyA\nkeyB\n"))
    assert backend.live_peers() == ["keyA", "keyB"]
