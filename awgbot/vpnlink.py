"""AmneziaVPN `vpn://` import links.

Format (verified against the client sources):

  vpn://<base64url(no padding) of qCompress(UTF-8 JSON envelope)>>

  qCompress  = 4-byte big-endian uncompressed length + zlib stream
               (Qt's QByteArray::qCompress / qUncompress)

  envelope   = {"containers": [{"container": "amnezia-awg",
                                "awg": {"last_config": <JSON *string*>,
                                        "isThirdPartyConfig": true,
                                        "port": "<port>",
                                        "transport_proto": "udp"}}],
                "defaultContainer": "amnezia-awg",
                "hostName": ..., "description": ..., "dns1": ...}

  last_config = the client INI in "config" (for display) *plus* the structured
                fields the connect path reads: client_priv_key, client_ip,
                server_pub_key, psk_key, allowed_ips (array!), mtu,
                persistent_keep_alive, isObfuscationEnabled and every AWG
                obfuscation key verbatim (`Jc`, `Jmin`, `Jmax`, `S1`..`S4`,
                `H1`..`H4`, `I1`..`I5`, ...). Missing structured fields make the
                app throw on connect (error 1000), even if "config" is valid.

Reference points in amnezia-client: importController.cpp (extractConfigFromData
→ qUncompress → processAmneziaConfig), protocols_defs.h (config_key names,
where junkPacketCount == "Jc" etc.), exportController.cpp (link generation).
"""

import base64
import json
import struct
import zlib

AWG_CONTAINER = "amnezia-awg"
AWG2_CONTAINER = "amnezia-awg2"
TRANSPORT_PROTO = "udp"
# AWG params that must survive into last_config verbatim, in the order the server writes
# them. JSON keys equal INI keys (see protocols_defs.h: junkPacketCount == "Jc", ...).
OBF_KEYS = ("Jc", "Jmin", "Jmax", "S1", "S2", "S3", "S4",
            "H1", "H2", "H3", "H4",
            # AWG 3.x transport protection
            "HeaderProtectionKey", "ContentPaddingAddition",
            "RekeyAfterTime", "RekeyTimeout", "RejectAfterTime", "KeepaliveTimeout",
            "MaxHandshakeAttempts", "RandomTrailers", "DisableCookies",
            "I1", "I2", "I3", "I4", "I5", "J1", "J2", "J3", "Itime")


class VpnLinkError(Exception):
    pass


# --- Qt qCompress / qUncompress ---------------------------------------------
def qcompress(data, level=6):
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("qcompress expects bytes")
    return struct.pack(">I", len(data)) + zlib.compress(bytes(data), level)


def quncompress(data):
    if len(data) <= 4:
        raise VpnLinkError("qCompress payload too short")
    try:
        return zlib.decompress(data[4:])
    except zlib.error as exc:
        raise VpnLinkError("zlib: %s" % exc)


# --- helpers ----------------------------------------------------------------
def parse_ini(text):
    """Key -> value for the whole client config (sections are ignored).

    Mirrors the client's own parser: split on '=', trim both sides, skip section
    headers and comments.
    """
    out = {}
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            continue
        key, sep, value = line.partition("=")
        if sep:
            out[key.strip()] = value.strip()
    return out


def split_endpoint(endpoint, default_port=51820):
    host, sep, port = endpoint.rpartition(":")
    if not sep:
        return endpoint, int(default_port)
    if host.startswith("[") and host.endswith("]"):     # [2001:db8::1]:51820
        host = host[1:-1]
    try:
        return host, int(port)
    except ValueError:
        return endpoint, int(default_port)


def _obf_pairs(kv):
    return [(key, kv[key]) for key in OBF_KEYS if kv.get(key, "") != ""]


# --- build ------------------------------------------------------------------
def build_last_config(conf_text, description=""):
    kv = parse_ini(conf_text)
    for required in ("PrivateKey", "Address", "PublicKey", "Endpoint"):
        if not kv.get(required):
            raise VpnLinkError("client config has no %s" % required)

    host, port = split_endpoint(kv["Endpoint"])
    allowed_ips = [part.strip() for part in kv.get("AllowedIPs", "").split(",")
                   if part.strip()]

    last = {
        "config": conf_text,
        "hostName": host,
        "port": int(port),
        "client_priv_key": kv["PrivateKey"],
        "client_ip": kv["Address"],
        "server_pub_key": kv["PublicKey"],
        "allowed_ips": allowed_ips,
        "isObfuscationEnabled": bool(kv.get("Jc")),
    }
    if kv.get("PresharedKey"):
        last["psk_key"] = kv["PresharedKey"]
    if kv.get("MTU"):
        last["mtu"] = kv["MTU"]
    if kv.get("PersistentKeepalive"):
        last["persistent_keep_alive"] = kv["PersistentKeepalive"]
    for key, value in _obf_pairs(kv):
        last[key] = value
    return last, kv, host, port


def container_for(kv):
    """AWG 2.0 servers (S3/S4 cookie+transport junk) live in `amnezia-awg2`."""
    if kv.get("S3") or kv.get("S4"):
        return AWG2_CONTAINER
    return AWG_CONTAINER


def build_envelope(conf_text, description="", container=None):
    last, kv, host, port = build_last_config(conf_text)
    container = container or container_for(kv)
    envelope = {
        "containers": [{
            "container": container,
            "awg": {
                "last_config": json.dumps(last, separators=(",", ":"), ensure_ascii=True),
                "isThirdPartyConfig": True,
                "port": str(port),
                "transport_proto": TRANSPORT_PROTO,
            },
        }],
        "defaultContainer": container,
        "hostName": host,
    }
    if description:
        envelope["description"] = description
    dns = [part.strip() for part in kv.get("DNS", "").split(",") if part.strip()]
    if dns:
        envelope["dns1"] = dns[0]
    if len(dns) > 1:
        envelope["dns2"] = dns[1]
    return envelope


def build_vpn_link(conf_text, description="", container=None):
    raw = json.dumps(build_envelope(conf_text, description, container),
                     separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    encoded = base64.urlsafe_b64encode(qcompress(raw)).decode("ascii").rstrip("=")
    return "vpn://" + encoded


# --- decode (used by tests and by `--export --link` debugging) ---------------
def decode_vpn_link(link):
    if not link.startswith("vpn://"):
        raise VpnLinkError("not a vpn:// link")
    body = link[len("vpn://"):].strip()
    padded = body + "=" * (-len(body) % 4)
    try:
        compressed = base64.urlsafe_b64decode(padded.encode("ascii"))
    except Exception as exc:
        raise VpnLinkError("base64: %s" % exc)
    envelope = json.loads(quncompress(compressed).decode("utf-8"))
    return envelope


def decode_last_config(envelope):
    containers = envelope.get("containers") or []
    if not containers:
        raise VpnLinkError("envelope has no containers")
    awg = containers[0].get("awg") or {}
    raw = awg.get("last_config")
    if not raw:
        raise VpnLinkError("container has no awg.last_config")
    return json.loads(raw)
