"""Render a client .conf and parse the server config's obfuscation parameters."""

import re

from .config import OBFUSCATION_KEYS
from .keys import public_key as derive_public_key

MANAGED_BEGIN = "# >>> awgbot managed peers (do not edit by hand)"
MANAGED_END = "# <<< awgbot managed peers"

# Keys that only make sense on the server side; stripped before `awg syncconf`.
STRIP_DROP = {
    "address", "dns", "mtu", "table", "preup", "postup", "predown", "postdown",
    "saveconfig", "fwmark",
}


def _sections(text):
    """Yield (section_name, [(key, value), ...]) preserving order."""
    current, items = None, []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            if current is not None:
                yield current, items
            current, items = line[1:-1].strip().lower(), []
            continue
        if "=" in line and current is not None:
            key, _, value = line.partition("=")
            items.append((key.strip(), value.strip()))
    if current is not None:
        yield current, items


def server_obfuscation(text):
    """Interface-level AmneziaWG params present in the server config, in canonical order."""
    found = {}
    for name, items in _sections(text):
        if name != "interface":
            continue
        for key, value in items:
            found[key.lower()] = value
    out = []
    for key in OBFUSCATION_KEYS:
        if key.lower() in found:
            out.append((key, found[key.lower()]))
    return out


def server_private_key(text):
    for name, items in _sections(text):
        if name != "interface":
            continue
        for key, value in items:
            if key.lower() == "privatekey":
                return value
    return None


def server_mtu(text):
    for name, items in _sections(text):
        if name != "interface":
            continue
        for key, value in items:
            if key.lower() == "mtu":
                return value
    return None


def strip_for_syncconf(text):
    """Mimic `awg-quick strip`: drop interface keys that are not understood by `awg`."""
    out = []
    for name, items in _sections(text):
        kept = [(k, v) for k, v in items if k.lower() not in STRIP_DROP]
        if not kept:
            continue
        out.append("[%s]" % ("Interface" if name == "interface" else "Peer"))
        for key, value in kept:
            out.append("%s = %s" % (key, value))
        out.append("")
    return "\n".join(out) + "\n"


def render_client_config(cfg, private_key, preshared_key, address, server_pub,
                         obfuscation=(), mtu=None):
    """A .conf the AmneziaWG client app can import directly."""
    lines = ["[Interface]"]
    lines.append("PrivateKey = %s" % private_key)
    lines.append("Address = %s/32" % address)
    if cfg.dns:
        lines.append("DNS = %s" % cfg.dns)
    if mtu:
        lines.append("MTU = %s" % mtu)
    for key, value in obfuscation:
        lines.append("%s = %s" % (key, value))
    lines.append("")
    lines.append("[Peer]")
    lines.append("PublicKey = %s" % server_pub)
    if preshared_key:
        lines.append("PresharedKey = %s" % preshared_key)
    lines.append("AllowedIPs = %s" % cfg.allowed_ips)
    lines.append("Endpoint = %s:%s" % (cfg.endpoint_host, cfg.endpoint_port))
    if cfg.keepalive:
        lines.append("PersistentKeepalive = %s" % cfg.keepalive)
    return "\n".join(lines) + "\n"


def peer_block(public_key, preshared_key, ip):
    lines = ["[Peer]", "# awgbot"]
    lines.append("PublicKey = %s" % public_key)
    if preshared_key:
        lines.append("PresharedKey = %s" % preshared_key)
    lines.append("AllowedIPs = %s/32" % ip)
    return "\n".join(lines)


def apply_managed_peers(server_text, peers):
    """Return server config text with the bot-managed peer block replaced."""
    pattern = re.compile(
        r"\n*%s.*?%s\n?" % (re.escape(MANAGED_BEGIN), re.escape(MANAGED_END)), re.S
    )
    body = server_text
    if pattern.search(body):
        body = pattern.sub("\n", body)
    body = body.rstrip("\n") + "\n\n"
    if peers:
        body += MANAGED_BEGIN + "\n"
        body += "\n".join(peer_block(p.public_key, p.preshared_key, p.ip) + "\n"
                          for p in peers)
        body += MANAGED_END + "\n"
    return body


def resolve_server_pubkey(cfg, server_text):
    if cfg.server_public_key:
        return cfg.server_public_key
    priv = server_private_key(server_text or "")
    if priv:
        return derive_public_key(priv)
    return None
