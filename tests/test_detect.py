"""Auto-detection of an existing Amnezia installation (what setup.sh relies on)."""

import os

import pytest

from awgbot import detect, keys
from awgbot.detect import DetectError


SERVER_CONF = """[Interface]
PrivateKey = {priv}
Address = 10.8.1.0/24
ListenPort = 34492
Jc = 4
Jmin = 10
Jmax = 50
S1 = 15
S2 = 135
S3 = 43
S4 = 2
H1 = 1778950080-1816592595
H2 = 2072164573-2139379451
H3 = 2142534419-2145358096
H4 = 2145391100-2146000230

[Peer]
PublicKey = manual
AllowedIPs = 10.8.1.5/32
"""


class Result(object):
    def __init__(self, code=0, out=b"", err=b""):
        self.returncode = code
        self.stdout = out
        self.stderr = err


class Runner(object):
    """Answers the exact command lines the detector uses."""

    def __init__(self, containers=(), interfaces=(), conf=None, ip="203.0.113.9",
                 has_awg=True, conf_path="/opt/amnezia/awg/awg0.conf"):
        self.containers = list(containers)
        self.interfaces = list(interfaces)
        self.conf = conf
        self.ip = ip
        self.has_awg = has_awg
        self.conf_path = conf_path
        self.calls = []

    def __call__(self, argv, input=None, **kwargs):
        self.calls.append(list(argv))
        if argv[:2] == ["docker", "ps"]:
            return Result(0, ("\n".join(self.containers) + "\n").encode())
        if argv[:2] == ["docker", "exec"]:
            rest = argv[4:] if len(argv) > 3 else []
            if rest == ["awg", "show", "interfaces"]:
                return Result(0, " ".join(self.interfaces).encode())
            if rest and rest[0] == "cat":
                if self.conf is not None and rest[1] == self.conf_path:
                    return Result(0, self.conf.encode())
                return Result(1, b"", b"No such file")
            if rest and rest[0] == "sh":
                return Result(0, (self.conf_path + "\n").encode())
        if argv[:1] == ["curl"]:
            return Result(0, (self.ip + "\n").encode()) if self.ip else Result(1)
        if argv[0] in ("awg", "wg"):
            if not self.has_awg:
                return Result(127, b"", b"command not found")
            if argv[1:] == ["show", "interfaces"]:
                return Result(0, " ".join(self.interfaces).encode())
            return Result(127, b"", b"command not found")
        return Result(0)


def test_docker_installation_is_detected(tmp_path):
    conf = SERVER_CONF.format(priv=keys.generate_private_key())
    runner = Runner(containers=["amnezia-awg2"], interfaces=["awg0"], conf=conf)
    info = detect.detect(runner=runner)
    assert info["mode"] == "docker"
    assert info["container"] == "amnezia-awg2"
    assert info["conf"] == "/opt/amnezia/awg/awg0.conf"
    assert info["iface"] == "awg0"
    assert info["port"] == 34492
    assert info["subnet"] == "10.8.1.0/24"
    assert info["server_ip"] == "10.8.1.0"
    assert info["endpoint_host"] == "203.0.113.9"
    assert info["clients_table"] == "/opt/amnezia/awg/clientsTable"
    assert "S3" in info["obfuscation"] and "H4" in info["obfuscation"]
    assert info["public_key"].endswith("=")


def test_native_installation_is_detected(tmp_path):
    conf_dir = tmp_path / "amneziawg"
    conf_dir.mkdir()
    conf = SERVER_CONF.format(priv=keys.generate_private_key())
    (conf_dir / "awg0.conf").write_text(conf)
    runner = Runner(containers=[], interfaces=["awg0"], has_awg=True)
    info = detect.detect(runner=runner, globs=(str(conf_dir),))
    assert info["mode"] == "native"
    assert info["container"] == ""
    assert info["conf"] == str(conf_dir / "awg0.conf")
    assert info["port"] == 34492


def test_nothing_installed_raises():
    runner = Runner(containers=[], interfaces=[], has_awg=False)
    with pytest.raises(DetectError) as exc:
        detect.detect(runner=runner)
    assert "amnezia" in str(exc.value).lower()


def test_conf_without_address_falls_back_to_defaults(tmp_path):
    conf_dir = tmp_path / "wg"
    conf_dir.mkdir()
    (conf_dir / "awg0.conf").write_text("[Interface]\nPrivateKey = %s\nListenPort = 5555\n"
                                        % keys.generate_private_key())
    info = detect.detect(runner=Runner(interfaces=["awg0"]), globs=(str(conf_dir),))
    assert info["subnet"] == "10.8.1.0/24"          # sane fallback
    assert info["port"] == 5555
    assert any("обфускац" in w for w in info["warnings"])


def test_missing_public_ip_only_warns(tmp_path):
    conf_dir = tmp_path / "wg"
    conf_dir.mkdir()
    (conf_dir / "awg0.conf").write_text(SERVER_CONF.format(priv=keys.generate_private_key()))
    info = detect.detect(runner=Runner(interfaces=["awg0"], ip=None), globs=(str(conf_dir),))
    assert info["endpoint_host"] == ""
    assert any("ENDPOINT_HOST" in w for w in info["warnings"])


def test_explicit_host_and_port_win(tmp_path):
    conf_dir = tmp_path / "wg"
    conf_dir.mkdir()
    (conf_dir / "awg0.conf").write_text(SERVER_CONF.format(priv=keys.generate_private_key()))
    info = detect.detect(runner=Runner(interfaces=["awg0"]), host="vpn.example.com",
                         port=443, globs=(str(conf_dir),))
    assert info["endpoint_host"] == "vpn.example.com"
    assert info["port"] == 443


def test_conf_facts_parses_interface():
    facts = detect.conf_facts(SERVER_CONF.format(priv=keys.generate_private_key()))
    assert facts["port"] == 34492
    assert facts["subnet"] == "10.8.1.0/24"
    assert facts["server_ip"] == "10.8.1.0"
    assert facts["public_key"]
    with pytest.raises(DetectError):
        detect.conf_facts("")


def test_render_env_is_complete(tmp_path):
    info = {"iface": "awg0", "conf": "/opt/amnezia/awg/awg0.conf", "container": "amnezia-awg2",
            "clients_table": "/opt/amnezia/awg/clientsTable", "endpoint_host": "1.2.3.4",
            "port": 34492, "public_key": "PUB=", "subnet": "10.8.1.0/24",
            "server_ip": "10.8.1.0", "obfuscation": ["Jc"]}
    env = detect.render_env(info, "123:ABC", "42", "/opt/deeptraversal", "/opt/deeptraversal")
    for line in ["BOT_TOKEN=123:ABC", "ADMIN_IDS=42", "AWG_DOCKER=amnezia-awg2",
                 "AWG_CONF=/opt/amnezia/awg/awg0.conf", "ENDPOINT_HOST=1.2.3.4",
                 "ENDPOINT_PORT=34492", "SUBNET=10.8.1.0/24", "SERVER_IP=10.8.1.0",
                 "DB_PATH=/opt/deeptraversal/awgbot.db", "REQUIRE_APPROVAL=true",
                 "MTU=1280"]:
        assert line in env, line


def test_write_env_keeps_existing_token_and_admin(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("BOT_TOKEN=old:TOKEN\nADMIN_IDS=777\nENDPOINT_HOST=old.example\n")
    info = {"mode": "docker", "container": "amnezia-awg2",
            "conf": "/opt/amnezia/awg/awg0.conf", "iface": "awg0", "port": 34492,
            "subnet": "10.8.1.0/24", "server_ip": "10.8.1.0", "public_key": "PUB=",
            "endpoint_host": "detected.example", "clients_table": "", "notes": [],
            "warnings": [], "obfuscation": ["Jc"]}
    monkeypatch.setattr(detect, "detect", lambda **kwargs: dict(info))
    code = detect.main(["--write-env", str(env_path), "--dest", str(tmp_path)])
    assert code == 0
    text = env_path.read_text()
    assert "BOT_TOKEN=old:TOKEN" in text          # not overwritten by an empty flag
    assert "ADMIN_IDS=777" in text
    assert "ENDPOINT_HOST=old.example" in text    # keep the address the user tuned
    assert "AWG_DOCKER=amnezia-awg2" in text


def test_write_env_keeps_host_but_takes_fresh_port(tmp_path, monkeypatch):
    """After an Amnezia reinstall the ListenPort changes — .env must follow the server."""
    env_path = tmp_path / ".env"
    env_path.write_text("BOT_TOKEN=old:TOKEN\nADMIN_IDS=777\nENDPOINT_HOST=old.example\n"
                        "ENDPOINT_PORT=34492\n")
    info = {"mode": "docker", "container": "amnezia-awg2",
            "conf": "/opt/amnezia/awg/awg0.conf", "iface": "awg0", "port": 47328,
            "subnet": "10.8.1.0/24", "server_ip": "10.8.1.0", "public_key": "NEWPUB=",
            "endpoint_host": "detected.example", "clients_table": "", "notes": [],
            "warnings": [], "obfuscation": ["Jc"]}
    monkeypatch.setattr(detect, "detect", lambda **kwargs: dict(info))
    assert detect.main(["--write-env", str(env_path), "--dest", str(tmp_path)]) == 0
    text = env_path.read_text()
    assert "ENDPOINT_PORT=47328" in text            # new port wins
    assert "ENDPOINT_PORT=34492" not in text
    assert "ENDPOINT_HOST=old.example" in text      # host stays as the user set it
    assert "SERVER_PUBLIC_KEY=NEWPUB=" in text


def test_existing_values_parser(tmp_path):
    path = tmp_path / ".env"
    path.write_text("# comment\nA=1\nB = 2 \nbroken\n")
    values = detect.existing_values(str(path))
    assert values == {"A": "1", "B": "2"}
    assert detect.existing_values(str(tmp_path / "nope")) == {}
