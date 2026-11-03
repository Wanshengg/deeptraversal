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


# --- Docker mode (Amnezia's containerised server: no awg, no conf on the host) ----------
class DockerFakeRunner(object):
    """Records `docker exec` calls and captures the payloads written via stdin."""

    def __init__(self, returncode=0, read_conf="[Interface]\nPrivateKey = x\nJc = 4\n"):
        self.calls = []
        self.returncode = returncode
        self.read_conf = read_conf
        self.written = {}

    def __call__(self, argv, input=None, **kwargs):
        self.calls.append({"argv": list(argv), "input": input})
        if "cat" in argv and input is None:
            return Result(0, self.read_conf.encode())
        if input is not None:
            target = argv[-1]
            if target.startswith("cat > "):
                target = target[len("cat > "):].strip("'")
                self.written[target] = input.decode()
            elif target.startswith("set -e"):
                self.written["conf"] = input.decode()
        return Result(self.returncode, b"", b"")


def test_docker_mode_uses_docker_exec(cfg):
    cfg.awg_docker = "amnezia-awg2"
    cfg.server_conf = "/opt/amnezia/awg/awg0.conf"
    cfg.server_public_key = ""
    runner = DockerFakeRunner(read_conf="[Interface]\nPrivateKey = %s\nJc = 4\n"
                               % keys.generate_private_key())
    backend = AmneziaWG(cfg, runner=runner)
    assert backend.transport.__class__.__name__ == "DockerTransport"

    backend.sync([peer()])

    # everything goes through `docker exec -i <container>`
    for call in runner.calls:
        assert call["argv"][:4] == ["docker", "exec", "-i", "amnezia-awg2"], call["argv"]
    syncconf = [c for c in runner.calls if "syncconf" in c["argv"]][0]
    assert syncconf["argv"][4:] == ["awg", "syncconf", "awg0", syncconf["argv"][-1]]
    assert syncconf["argv"][-1].startswith("/tmp/awgbot-")       # path inside the container
    staged = runner.written[syncconf["argv"][-1]]
    assert "PublicKey = pubA" in staged and "PostUp" not in staged
    # config file written back inside the container, with a .bak take
    assert "PublicKey = pubA" in runner.written["conf"]
    assert ".awgbot.bak" in str([c["argv"] for c in runner.calls if c["input"] and
                                 c["argv"][-1].startswith("set -e")])


def test_docker_mode_reads_conf_from_container(cfg):
    cfg.awg_docker = "amnezia-awg"
    cfg.server_conf = "/opt/amnezia/awg/awg0.conf"
    runner = DockerFakeRunner(read_conf="[Interface]\nPrivateKey = x\nS1 = 15\nS3 = 43\n")
    backend = AmneziaWG(cfg, runner=runner)
    assert backend.server_text().startswith("[Interface]")
    assert [k for k, _ in backend.obfuscation()] == ["S1", "S3"]


def test_docker_mode_reports_container_errors(cfg):
    cfg.awg_docker = "amnezia-awg2"
    cfg.server_conf = "/opt/amnezia/awg/awg0.conf"
    runner = DockerFakeRunner(returncode=1)
    backend = AmneziaWG(cfg, runner=runner)
    with pytest.raises(BackendError):
        backend.sync([peer()])


def test_existing_ips_come_from_unmanaged_peers(cfg):
    priv = keys.generate_private_key()
    write_server_conf(cfg.server_conf, priv)
    cfg.server_public_key = keys.public_key(priv)
    backend = AmneziaWG(cfg, runner=FakeRunner())
    assert backend.existing_ips() == {"10.8.0.9"}      # the hand-made peer in the fixture
    backend.sync([peer("pubA", ip="10.8.0.2")])
    assert backend.existing_ips() == {"10.8.0.9"}      # managed region is not counted


# --- Amnezia clientsTable ("Users" screen in the app) -----------------------------------
TABLE = """[
    {
        "clientId": "ADMINKEY=",
        "userData": {
            "allowedIps": "10.8.1.1/32",
            "clientName": "Admin [macOS]",
            "creationDate": "Tue Nov 3 12:46:52 2026"
        }
    }
]
"""


class TableRunner(object):
    def __init__(self, table=TABLE, returncode=0):
        self.table = table
        self.returncode = returncode
        self.writes = []

    def __call__(self, argv, input=None, **kwargs):
        if input is not None:
            self.writes.append(input.decode())
            return Result(self.returncode, b"", b"")
        if argv[-1].endswith("clientsTable"):
            if self.table is None:
                return Result(1, b"", b"No such file or directory")
            return Result(0, self.table.encode())
        return Result(0, b"")


def test_register_client_writes_into_container_table(cfg):
    cfg.awg_docker = "amnezia-awg2"
    cfg.server_conf = "/opt/amnezia/awg/awg0.conf"
    runner = TableRunner()
    backend = AmneziaWG(cfg, runner=runner)
    ok = backend.register_client(peer(pub="NEWKEY=", ip="10.8.1.8"))
    assert ok is True
    assert len(runner.writes) == 1
    written = runner.writes[0]
    assert "NEWKEY=" in written and "Admin [macOS]" in written   # others preserved
    assert '"allowedIps": "10.8.1.8/32"' in written


def test_unregister_client_removes_from_table(cfg):
    cfg.awg_docker = "amnezia-awg2"
    cfg.server_conf = "/opt/amnezia/awg/awg0.conf"
    runner = TableRunner(table=TABLE.replace('"clientId": "ADMINKEY="', '"clientId": "KEY="'))
    backend = AmneziaWG(cfg, runner=runner)
    assert backend.unregister_client(peer(pub="KEY=")) is True
    assert "KEY=" not in runner.writes[0]


def test_register_client_is_optional_when_table_missing(cfg, tmp_path):
    cfg.server_conf = str(tmp_path / "awg0.conf")
    write_server_conf(cfg.server_conf, keys.generate_private_key())
    backend = AmneziaWG(cfg, runner=FakeRunner())     # no clientsTable on disk
    assert backend.clients_in_app() == set()
    assert backend.register_client(peer()) is False    # must not raise
