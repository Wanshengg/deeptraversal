"""AmneziaWG backends: the real one edits the server config and syncs the live interface,
the mock one keeps state in a JSON file so the bot can be run (and demoed) on any laptop.
"""

import json
import os
import shutil
import subprocess
import tempfile

from . import clientconf


class BackendError(Exception):
    pass


class AmneziaWG(object):
    """Owns the `# >>> awgbot managed peers` region of the server interface config."""

    def __init__(self, cfg, runner=None, dry_run=False):
        self.cfg = cfg
        self.run = runner or subprocess.run
        self.dry_run = dry_run

    # --- config file -----------------------------------------------------------
    def server_text(self):
        with open(self.cfg.server_conf, "r", encoding="utf-8") as fh:
            return fh.read()

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

    def sync(self, peers):
        """Rewrite the managed region from `peers` and push it to the live interface.

        The live interface is updated first: if `awg syncconf` fails, the config file on
        disk still describes what is actually running and a restart cannot lose state.
        """
        text = clientconf.apply_managed_peers(self.server_text(), peers)
        if self.dry_run:
            self._write_conf(text)
            return
        self._apply(text)
        self._write_conf(text)

    def _write_conf(self, text):
        path = self.cfg.server_conf
        backup = path + ".awgbot.bak"
        shutil.copy2(path, backup)
        os.chmod(backup, 0o600)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)), prefix=".awgbot-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def _apply(self, text):
        stripped = clientconf.strip_for_syncconf(text)
        fd, tmp = tempfile.mkstemp(prefix="awgbot-sync-", suffix=".conf")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(stripped)
            result = self.run(
                [self.cfg.awg_bin, "syncconf", self.cfg.iface, tmp],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
        finally:
            os.unlink(tmp)
        if getattr(result, "returncode", 1) != 0:
            raise BackendError(
                "awg syncconf failed: %s" % (getattr(result, "stderr", b"") or b"").decode(
                    "utf-8", "replace").strip()
            )

    def live_peers(self):
        result = self.run(
            [self.cfg.awg_bin, "show", self.cfg.iface, "peers"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        if getattr(result, "returncode", 1) != 0:
            raise BackendError("awg show failed")
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

    def sync(self, peers):
        self._state["peers"] = [
            {"public_key": p.public_key, "preshared_key": p.preshared_key, "ip": p.ip,
             "tg_id": p.tg_id}
            for p in peers
        ]
        self._save()

    def live_peers(self):
        return [p["public_key"] for p in self._state["peers"]]

    def seed(self, obfuscation=None, server_public_key=None):
        if obfuscation is not None:
            self._state["obfuscation"] = [list(x) for x in obfuscation]
        if server_public_key:
            self._state["server_public_key"] = server_public_key
        self._save()


def make_backend(cfg):
    if cfg.backend == "mock":
        return Mock(cfg)
    return AmneziaWG(cfg)
