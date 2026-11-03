"""AmneziaWG backends.

The real backend edits the `# >>> awgbot managed peers` region of the server interface
config and reconciles the live interface with `awg syncconf`. It works through a
*transport*, because Amnezia ships its server side in two very different shapes:

  * native install  — `awg` on the host, config at /etc/amnezia/amneziawg/awg0.conf
                      (LocalTransport);
  * Docker container — `awg` and the config live inside the container, the host has
                      neither (DockerTransport, `docker exec`).

The mock backend keeps state in a JSON file so the bot can be run (and demoed) anywhere.
"""

import json
import os
import shutil
import subprocess
import tempfile
import uuid

from . import clientconf


class BackendError(Exception):
    pass


def _stderr(result):
    return (getattr(result, "stderr", b"") or b"").decode("utf-8", "replace").strip()


class LocalTransport(object):
    """Config on this host, `awg` run as a local process."""

    def __init__(self, cfg, runner=None):
        self.cfg = cfg
        self.run = runner or subprocess.run

    def read_conf(self):
        try:
            with open(self.cfg.server_conf, "r", encoding="utf-8") as fh:
                return fh.read()
        except IOError as exc:
            raise BackendError("cannot read %s: %s" % (self.cfg.server_conf, exc))

    def write_conf(self, text):
        path = self.cfg.server_conf
        backup = path + ".awgbot.bak"
        shutil.copy2(path, backup)
        os.chmod(backup, 0o600)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)),
                                   prefix=".awgbot-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def put_temp(self, text, suffix=".conf"):
        fd, path = tempfile.mkstemp(prefix="awgbot-sync-", suffix=suffix)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def remove_temp(self, path):
        try:
            os.unlink(path)
        except OSError:
            pass

    def awg(self, argv, input=None):
        return self.run([self.cfg.awg_bin] + list(argv), input=input,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)


class DockerTransport(object):
    """Config and `awg` inside the Amnezia container (`amnezia-awg` / `amnezia-awg2`).

    `AWG_CONF` is a path *inside* the container; Amnezia's own layout puts it at
    /opt/amnezia/awg/awg0.conf and runs `amneziawg-go awg0` (userspace WireGuard), so
    `awg syncconf` talks to that daemon instead of a kernel interface.
    """

    def __init__(self, cfg, runner=None):
        self.cfg = cfg
        self.run = runner or subprocess.run
        self.container = cfg.awg_docker

    def _exec(self, argv, input=None):
        return self.run(["docker", "exec", "-i", self.container] + list(argv), input=input,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def read_conf(self):
        result = self._exec(["cat", self.cfg.server_conf])
        if getattr(result, "returncode", 1) != 0:
            raise BackendError("docker exec %s cat %s failed: %s"
                               % (self.container, self.cfg.server_conf, _stderr(result)))
        return result.stdout.decode("utf-8")

    def write_conf(self, text):
        path = self.cfg.server_conf
        # one shell round-trip: backup, write-ahead to .new, atomic rename inside the
        # container's filesystem (the conf is not mounted on the host)
        script = ("set -e; [ -f '{p}' ] && cp -f '{p}' '{p}.awgbot.bak' && "
                  "chmod 600 '{p}.awgbot.bak'; cat > '{p}.awgbot.new'; "
                  "chmod 600 '{p}.awgbot.new'; mv -f '{p}.awgbot.new' '{p}'").format(p=path)
        result = self._exec(["sh", "-c", script], input=text.encode("utf-8"))
        if getattr(result, "returncode", 1) != 0:
            raise BackendError("writing %s in %s failed: %s"
                               % (path, self.container, _stderr(result)))

    def put_temp(self, text, suffix=".conf"):
        path = "/tmp/awgbot-%s%s" % (uuid.uuid4().hex, suffix)
        result = self._exec(["sh", "-c", "cat > '%s'" % path], input=text.encode("utf-8"))
        if getattr(result, "returncode", 1) != 0:
            raise BackendError("cannot stage %s in %s: %s"
                               % (path, self.container, _stderr(result)))
        return path

    def remove_temp(self, path):
        try:
            self._exec(["rm", "-f", path])
        except Exception:
            pass

    def awg(self, argv, input=None):
        return self._exec([self.cfg.awg_bin] + list(argv), input=input)


class AmneziaWG(object):
    """Owns the bot-managed peer region and applies it to the live interface."""

    def __init__(self, cfg, runner=None, dry_run=False, transport=None):
        self.cfg = cfg
        self.dry_run = dry_run
        if transport is not None:
            self.transport = transport
        elif getattr(cfg, "awg_docker", ""):
            self.transport = DockerTransport(cfg, runner)
        else:
            self.transport = LocalTransport(cfg, runner)

    @property
    def run(self):  # backwards-compatible access for callers that passed a runner
        return self.transport.run

    # --- config file -----------------------------------------------------------
    def server_text(self):
        return self.transport.read_conf()

    def obfuscation(self):
        return clientconf.server_obfuscation(self.server_text())

    def server_public_key(self):
        key = clientconf.resolve_server_pubkey(self.cfg, self.server_text())
        if not key:
            raise BackendError(
                "cannot determine server public key: set SERVER_PUBLIC_KEY or PrivateKey in "
                + self.cfg.server_conf
            )
        return key

    def server_mtu(self):
        return clientconf.server_mtu(self.server_text()) or self.cfg.mtu

    def existing_ips(self):
        """Addresses of hand-added peers — never hand them out again."""
        return clientconf.existing_peer_ips(self.server_text())

    def sync(self, peers):
        """Rewrite the managed region from `peers` and push it to the live interface.

        The live interface is updated first: if `awg syncconf` fails, the config file still
        describes what is actually running, so a restart cannot resurrect a ghost peer.
        """
        text = clientconf.apply_managed_peers(self.server_text(), peers)
        if self.dry_run:
            self.transport.write_conf(text)
            return
        self._apply(text)
        self.transport.write_conf(text)

    def _apply(self, text):
        stripped = clientconf.strip_for_syncconf(text)
        path = self.transport.put_temp(stripped)
        try:
            result = self.transport.awg(["syncconf", self.cfg.iface, path])
        finally:
            self.transport.remove_temp(path)
        if getattr(result, "returncode", 1) != 0:
            raise BackendError("awg syncconf failed: %s" % _stderr(result))

    def live_peers(self):
        result = self.transport.awg(["show", self.cfg.iface, "peers"])
        if getattr(result, "returncode", 1) != 0:
            raise BackendError("awg show failed: %s" % _stderr(result))
        return result.stdout.decode().split()


DEFAULT_SERVER_STUB = """[Interface]
# Synthetic server config used by the mock backend so client configs look realistic.
Address = 10.8.0.1/24
MTU = 1280
Jc = 4
Jmin = 40
Jmax = 70
S1 = 0
S2 = 0
H1 = 1234567
H2 = 2345678
H3 = 3456789
H4 = 4567890
"""


class Mock(object):
    """In-memory/JSON-file backend. Same interface, no root, no network changes."""

    def __init__(self, cfg, path=None):
        self.cfg = cfg
        self.path = path or cfg.mock_state
        self._state = self._load()

    def _load(self):
        if os.path.exists(self.path):
            with open(self.path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        return {"peers": []}

    def _save(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(self._state, fh, indent=2)

    def server_text(self):
        return self._state.get("server_conf") or DEFAULT_SERVER_STUB

    def obfuscation(self):
        stored = self._state.get("obfuscation")
        if stored:
            return [tuple(x) for x in stored]
        return clientconf.server_obfuscation(self.server_text())

    def server_public_key(self):
        key = (self.cfg.server_public_key or self._state.get("server_public_key")
               or clientconf.resolve_server_pubkey(self.cfg, self.server_text()))
        if not key:
            raise BackendError("mock backend needs SERVER_PUBLIC_KEY")
        return key

    def server_mtu(self):
        return clientconf.server_mtu(self.server_text()) or self.cfg.mtu

    def existing_ips(self):
        return set(self._state.get("existing_ips", []))

    def sync(self, peers):
        self._state["peers"] = [
            {"public_key": p.public_key, "preshared_key": p.preshared_key, "ip": p.ip,
             "tg_id": p.tg_id}
            for p in peers
        ]
        self._save()

    def live_peers(self):
        return [p["public_key"] for p in self._state["peers"]]

    def seed(self, obfuscation=None, server_public_key=None, existing_ips=None):
        if obfuscation is not None:
            self._state["obfuscation"] = [list(x) for x in obfuscation]
        if server_public_key:
            self._state["server_public_key"] = server_public_key
        if existing_ips is not None:
            self._state["existing_ips"] = sorted(existing_ips)
        self._save()


def make_backend(cfg):
    if cfg.backend == "mock":
        return Mock(cfg)
    return AmneziaWG(cfg)
