"""Key issuance / revocation — the part worth unit-testing without Telegram in the way."""

import os

from . import clientconf, ipam, keys, vpnlink
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


class Delivery(object):
    """Everything a user gets for one peer: .conf text plus a vpn:// import link."""

    __slots__ = ("peer", "conf", "filename", "link")

    def __init__(self, peer, conf, filename, link=None):
        self.peer = peer
        self.conf = conf
        self.filename = filename
        self.link = link

    def __iter__(self):  # keeps `peer, conf, filename = delivery` working
        return iter((self.peer, self.conf, self.filename))

    def __repr__(self):
        return "<Delivery %s %s link=%s chars>" % (
            self.peer.device, self.peer.ip, len(self.link or ""))


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

    def delivery(self, peer):
        conf = self.config_text(peer)
        link = None
        if self.cfg.send_vpn_link:
            try:
                link = vpnlink.build_vpn_link(
                    conf, description=safe_device_name(peer.device),
                    container=getattr(self.cfg, "vpn_link_container", "") or None)
            except vpnlink.VpnLinkError:
                link = None  # a broken link must never block issuing the .conf
        return Delivery(peer, conf, conf_filename(peer), link)

    # --- operations ------------------------------------------------------------
    def issue(self, tg_id, device, actor=None):
        """Create (or replace) the user's peer. Returns a Delivery."""
        active = self.store.active_peers(tg_id)
        if len(active) >= self.cfg.max_peers_per_user:
            for peer in active[self.cfg.max_peers_per_user - 1:]:
                self._revoke(peer, actor or tg_id, reason="reissue")

        used = self.store.used_ips() | set(self.backend.existing_ips())
        ip = ipam.allocate(self.cfg.subnet, self.cfg.server_ip, used, self.cfg.reserved_ips)
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
        result = self.delivery(peer)
        self._write_export(peer, result.filename)
        self.store.audit(actor or tg_id, "issue", "tg=%s ip=%s pub=%s" % (tg_id, ip, pub))
        return result

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
        return self.delivery(peers[0])
