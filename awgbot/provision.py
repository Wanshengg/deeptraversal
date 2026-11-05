"""Key issuance / revocation — the part worth unit-testing without Telegram in the way."""

import logging
import os

from . import clientconf, ipam, keys, vpnlink
from .awg import BackendError

log = logging.getLogger("awgbot")


class ProvisionError(Exception):
    pass


class PruneResult(object):
    __slots__ = ("removed", "unknown_live", "checked")

    def __init__(self, removed, unknown_live, checked):
        self.removed = removed          # peers that were in the db but gone from the interface
        self.unknown_live = unknown_live  # live peers the bot does not know (hand-made)
        self.checked = checked

    def summary(self):
        lines = ["проверено пиров в базе: %s" % self.checked,
                 "удалено (их уже нет на интерфейсе): %s" % len(self.removed)]
        for peer in self.removed:
            lines.append("  · %s %s (%s…)" % (peer.device, peer.ip, peer.public_key[:12]))
        if self.unknown_live:
            lines.append("живых пиров вне базы (ручные, не трогаю): %s"
                         % len(self.unknown_live))
        return "\n".join(lines)


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
        self._register(peer)
        self.store.audit(actor or tg_id, "issue", "tg=%s ip=%s pub=%s" % (tg_id, ip, pub))
        return result

    def _register(self, peer):
        """Mirror the peer into Amnezia's clientsTable (app's Users screen)."""
        try:
            if not self.backend.register_client(peer):
                log.info("no clientsTable next to the server config, app list untouched")
        except Exception:
            log.warning("cannot update clientsTable for %s", peer.public_key[:8], exc_info=True)

    def _unregister(self, peer):
        try:
            self.backend.unregister_client(peer)
        except Exception:
            log.warning("cannot drop %s from clientsTable", peer.public_key[:8], exc_info=True)

    def _revoke(self, peer, actor, reason=""):
        self.store.revoke(peer.id)
        self.store.audit(actor, "revoke", "tg=%s ip=%s %s" % (peer.tg_id, peer.ip, reason))
        self.backend.sync(self.store.active_peers())
        self._unregister(peer)

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

    def sync_clients_table(self):
        """Re-add active peers to Amnezia's clientsTable.

        The AmneziaVPN app rewrites that file from its own state whenever the server is
        edited there, silently dropping entries for peers it did not create. So the bot
        reconciles: at startup, after prune, and via `--sync-clients-table`.
        """
        peers = self.store.active_peers()
        if not peers:
            return 0
        try:
            present = set(self.backend.clients_in_app())
        except Exception:
            present = set()
        added = 0
        for peer in peers:
            if peer.public_key in present:
                continue
            if self.backend.register_client(peer):
                added += 1
        if added:
            log.info("clientsTable reconcile: %s peer(s) re-listed", added)
        return added

    def prune(self, actor=None, force=False):
        """Drop peers that were removed outside the bot (e.g. deleted in the app).

        Reads the *live* interface, never the config: if `awg` answers with no peers while
        the db has some, that is almost always a broken interface rather than mass deletion,
        so it refuses unless `force` is set.
        """
        live = set(self.backend.live_peers())
        active = self.store.active_peers()
        if not live and active and not force:
            raise ProvisionError(
                "интерфейс не отдал ни одного пира, а в базе их %s — похоже, awg недоступен; "
                "если так и должно быть, повтори с --force" % len(active))

        removed = []
        for peer in active:
            if peer.public_key in live:
                continue
            self.store.revoke(peer.id)
            self.store.audit(actor or "cli", "prune",
                             "tg=%s ip=%s pub=%s" % (peer.tg_id, peer.ip, peer.public_key))
            log.info("pruned peer %s (%s) of user %s", peer.ip, peer.public_key[:12],
                     peer.tg_id)
            self._unregister(peer)
            removed.append(peer)

        if removed:
            self.backend.sync(self.store.active_peers())
        self.sync_clients_table()          # the app may have dropped our rows meanwhile
        known = {p.public_key for p in self.store.active_peers()}
        return PruneResult(removed, sorted(live - known), len(active))
