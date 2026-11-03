"""Key issuance / revocation — the part worth unit-testing without Telegram in the way."""

import os

from . import clientconf, ipam, keys
from .awg import BackendError


class ProvisionError(Exception):
    pass


def safe_device_name(raw):
    """Keep config file names boring: no path separators, no spaces."""
    cleaned = "".join(ch for ch in raw if ch.isalnum() or ch in "-_.").strip("._-")
    return (cleaned or "device")[:32]


def conf_filename(peer):
    """One .conf name for both the Telegram upload and the on-disk export."""
    return "amnezia-%s-%s.conf" % (safe_device_name(peer.device), peer.ip)


class Provisioner(object):
    def __init__(self, cfg, store, backend):
        self.cfg = cfg
        self.store = store
        self.backend = backend

    # --- helpers ---------------------------------------------------------------
    def _server_context(self):
        server_pub = self.backend.server_public_key()
        return server_pub, self.backend.obfuscation(), self.backend.server_mtu()

    def config_text(self, peer):
        server_pub, obf, mtu = self._server_context()
        return clientconf.render_client_config(
            self.cfg, peer.private_key, peer.preshared_key, peer.ip, server_pub,
            obfuscation=obf, mtu=mtu,
        )

    def export_dir(self):
        path = os.path.join(os.path.dirname(os.path.abspath(self.cfg.db_path)), "export")
        if not os.path.isdir(path):
            os.makedirs(path, mode=0o700)
        return path

    def _write_export(self, peer, filename):
        path = os.path.join(self.export_dir(), filename)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(self.config_text(peer))
        os.chmod(path, 0o600)
        return path

    # --- operations ------------------------------------------------------------
    def issue(self, tg_id, device, actor=None):
        """Create (or replace) the user's peer. Returns (peer, conf_text, filename)."""
        active = self.store.active_peers(tg_id)
        if len(active) >= self.cfg.max_peers_per_user:
            for peer in active[self.cfg.max_peers_per_user - 1:]:
                self._revoke(peer, actor or tg_id, reason="reissue")

        ip = ipam.allocate(self.cfg.subnet, self.cfg.server_ip,
                           self.store.used_ips(), self.cfg.reserved_ips)
        if ip is None:
            raise ProvisionError("IP pool %s is exhausted" % self.cfg.subnet)

        priv = keys.generate_private_key()
        pub = keys.public_key(priv)
        psk = keys.generate_preshared_key()
        device = safe_device_name(device)

        peer_id = self.store.add_peer(tg_id, device, pub, priv, psk, ip)
        peers = self.store.active_peers()
        try:
            self.backend.sync(peers)
        except BackendError:
            self.store.revoke(peer_id)
            raise

        peer = [p for p in self.store.active_peers(tg_id) if p.id == peer_id][0]
        conf = self.config_text(peer)
        filename = conf_filename(peer)
        self._write_export(peer, filename)
        self.store.audit(actor or tg_id, "issue", "tg=%s ip=%s pub=%s" % (tg_id, ip, pub))
        return peer, conf, filename

    def _revoke(self, peer, actor, reason=""):
        self.store.revoke(peer.id)
        self.store.audit(actor, "revoke", "tg=%s ip=%s %s" % (peer.tg_id, peer.ip, reason))
        self.backend.sync(self.store.active_peers())

    def revoke_all(self, tg_id, actor=None):
        peers = self.store.active_peers(tg_id)
        for peer in peers:
            self._revoke(peer, actor or tg_id, reason="user request")
        return len(peers)

    def resend(self, tg_id):
        peers = self.store.active_peers(tg_id)
        if not peers:
            return None
        peer = peers[0]
        return peer, self.config_text(peer), conf_filename(peer)
