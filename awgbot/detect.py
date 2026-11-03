"""Find an existing Amnezia/AmneziaWG installation and turn it into a working .env.

Two shapes are supported (both exist in the wild):

  * native  — `awg` on the host, config under /etc/amnezia/amneziawg/ or /etc/wireguard/;
  * docker  — Amnezia's container (`amnezia-awg`, `amnezia-awg2`), with `awg` and the config
              inside the container and nothing on the host.

Only the stdlib is used here: the installer runs this before a virtualenv exists.
"""

import argparse
import ipaddress
import json
import os
import subprocess
import sys

from . import clientconf, keys

DOCKER_DEFAULT_CONF = "/opt/amnezia/awg/awg0.conf"
NATIVE_CONF_GLOBS = ("/etc/amnezia/amneziawg", "/etc/wireguard", "/opt/amnezia/awg")
CONTAINER_PREFIXES = ("amnezia-awg2", "amnezia-awg")


class DetectError(Exception):
    pass


class _Failed(object):
    """Stand-in for a command that could not even be started (binary missing)."""

    returncode = 127
    stdout = b""
    stderr = b"command not found"


def _run(argv, runner=None, input=None):
    runner = runner or subprocess.run
    try:
        return runner(argv, input=input, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError:
        return _Failed()


def _out(result):
    return (getattr(result, "stdout", b"") or b"").decode("utf-8", "replace")


def _ok(result):
    return getattr(result, "returncode", 1) == 0


# --- docker -----------------------------------------------------------------
def find_container(runner=None):
    result = _run(["docker", "ps", "--format", "{{.Names}}"], runner)
    if not _ok(result):
        return None
    names = _out(result).split()
    for prefix in CONTAINER_PREFIXES:
        for name in names:
            if name == prefix or name.startswith(prefix + "-") or name.startswith(prefix):
                return name
    return None


def container_exec(container, argv, runner=None):
    return _run(["docker", "exec", "-i", container] + list(argv), runner)


def docker_interfaces(container, runner=None):
    result = container_exec(container, ["awg", "show", "interfaces"], runner)
    return _out(result).split() if _ok(result) else []


def docker_conf_candidates(container, iface, runner=None):
    out = [DOCKER_DEFAULT_CONF, "/opt/amnezia/awg/%s.conf" % iface]
    result = container_exec(
        container, ["sh", "-c", "ls /opt/amnezia/*/%s.conf /opt/amnezia/*/*.conf 2>/dev/null"
                                 % iface], runner)
    out.extend([line.strip() for line in _out(result).splitlines() if line.strip()])
    seen, ordered = set(), []
    for path in out:
        if path and path not in seen:
            seen.add(path)
            ordered.append(path)
    return ordered


def docker_read(container, path, runner=None):
    result = container_exec(container, ["cat", path], runner)
    return _out(result) if _ok(result) else None


# --- native -----------------------------------------------------------------
def native_conf_candidates(iface, globs=NATIVE_CONF_GLOBS):
    out = []
    for base in globs:
        out.append(os.path.join(base, "%s.conf" % iface))
        out.append(os.path.join(base, "wg0.conf"))
    return [p for p in out if p]


def native_interfaces(runner=None):
    for binary in ("awg", "wg"):
        result = _run([binary, "show", "interfaces"], runner)
        if _ok(result):
            return binary, _out(result).split()
    return None, []


# --- conf parsing ------------------------------------------------------------
def conf_facts(text):
    """Everything the .env needs, straight out of the server [Interface]."""
    if not text:
        raise DetectError("empty config")
    address = None
    for name, items in clientconf._sections(text):
        if name != "interface":
            continue
        for key, value in items:
            if key.lower() == "address":
                address = value.split(",")[0].strip()
    port = None
    for name, items in clientconf._sections(text):
        if name != "interface":
            continue
        for key, value in items:
            if key.lower() == "listenport":
                port = int(value)
    facts = {"port": port, "subnet": None, "server_ip": None, "public_key": None,
             "obfuscation": [k for k, _ in clientconf.server_obfuscation(text)]}
    if address:
        iface = ipaddress.ip_interface(address)
        facts["subnet"] = str(iface.network)
        facts["server_ip"] = str(iface.ip)
    private = clientconf.server_private_key(text)
    if private:
        facts["public_key"] = keys.public_key(private)
    return facts


def public_ip(runner=None):
    for url in ("https://api.ipify.org", "https://ifconfig.me/ip"):
        result = _run(["curl", "-fsS", "--max-time", "5", url], runner)
        if _ok(result):
            value = _out(result).strip()
            if value:
                return value
    return None


# --- main entry --------------------------------------------------------------
def detect(runner=None, host=None, port=None, iface="awg0", globs=None):
    """Return a dict describing the installation (or raise DetectError)."""
    globs = globs or NATIVE_CONF_GLOBS
    result = {"mode": None, "container": "", "conf": "", "iface": iface, "port": port,
              "subnet": "", "server_ip": "", "public_key": "", "endpoint_host": host or "",
              "clients_table": "", "notes": [], "warnings": []}

    container = find_container(runner)
    if container:
        result["mode"] = "docker"
        result["container"] = container
        interfaces = docker_interfaces(container, runner) or [iface]
        chosen = None
        for name in interfaces:
            for path in docker_conf_candidates(container, name, runner):
                text = docker_read(container, path, runner)
                if not text:
                    continue
                try:
                    facts = conf_facts(text)
                except DetectError:
                    continue
                chosen = (name, path, facts, text)
                break
            if chosen:
                break
        if not chosen:
            raise DetectError("в контейнере %s не найден конфиг amneziawg" % container)
        name, path, facts, text = chosen
        result.update({"iface": name, "conf": path, "clients_table":
                       os.path.join(os.path.dirname(path), "clientsTable")})
        result["notes"].append("Amnezia в Docker: %s:%s" % (container, path))
    else:
        binary, interfaces = native_interfaces(runner)
        if not binary:
            raise DetectError(
                "не нашёл ни контейнер amnezia-awg2/amnezia-awg, ни awg/wg на хосте — "
                "похоже, Amnezia тут не установлена")
        result["mode"] = "native"
        candidates = []
        for name in interfaces or [iface]:
            candidates.extend(native_conf_candidates(name, globs))
        facts = text = None
        for path in candidates:
            if not path or not os.path.exists(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    text = fh.read()
            except IOError:
                continue
            try:
                facts = conf_facts(text)
            except DetectError:
                text = None
                continue
            result["conf"] = path
            if not interfaces:
                result["iface"] = os.path.basename(path)[:-5]
            else:
                result["iface"] = interfaces[0]
            break
        if not facts:
            raise DetectError("awg есть, но конфиг не найден в %s"
                              % ", ".join(NATIVE_CONF_GLOBS))
        result["clients_table"] = os.path.join(os.path.dirname(result["conf"]), "clientsTable")
        result["notes"].append("нативная установка: %s (%s)" % (result["conf"], binary))

    if facts["port"]:
        result["port"] = facts["port"]
    if port:
        result["port"] = port
    result["subnet"] = facts["subnet"] or "10.8.1.0/24"
    result["server_ip"] = facts["server_ip"] or "10.8.1.0"
    result["public_key"] = facts["public_key"] or ""
    result["obfuscation"] = facts["obfuscation"]
    if not result["endpoint_host"]:
        detected = public_ip(runner)
        if detected:
            result["endpoint_host"] = detected
            result["notes"].append("публичный адрес определён как %s" % detected)
        else:
            result["warnings"].append(
                "не удалось определить публичный IP — укажите ENDPOINT_HOST вручную")
    if not result["obfuscation"]:
        result["warnings"].append("в конфиге нет параметров обфускации — это точно AmneziaWG?")
    return result


def render_env(info, token, admins, db_path, dest, mtu=1280, dns="1.1.1.1"):
    lines = [
        "# generated by setup.sh — deeptraversal",
        "BOT_TOKEN=%s" % token,
        "ADMIN_IDS=%s" % admins,
        "BACKEND=awg",
        "AWG_IFACE=%s" % info["iface"],
        "AWG_CONF=%s" % info["conf"],
        "AWG_BIN=awg",
        "AWG_DOCKER=%s" % info["container"],
        "AWG_CLIENTS_TABLE=%s" % info["clients_table"],
        "ENDPOINT_HOST=%s" % info["endpoint_host"],
        "ENDPOINT_PORT=%s" % info["port"],
        "SERVER_PUBLIC_KEY=%s" % info["public_key"],
        "SUBNET=%s" % info["subnet"],
        "SERVER_IP=%s" % info["server_ip"],
        "DNS=%s" % dns,
        "ALLOWED_IPS=0.0.0.0/0, ::/0",
        "PERSISTENT_KEEPALIVE=25",
        "MTU=%s" % mtu,
        "DB_PATH=%s/awgbot.db" % dest,
        "MOCK_STATE=%s/mock_awg_state.json" % dest,
        "MAX_PEERS_PER_USER=1",
        "REQUEST_COOLDOWN_S=60",
        "REQUIRE_APPROVAL=true",
        "SEND_VPN_LINK=true",
        "SEND_CONF_FILE=true",
        "LOG_LEVEL=INFO",
    ]
    return "\n".join(lines) + "\n"


def existing_values(path):
    values = {}
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    values[key.strip()] = value.strip()
    return values


def summary(info):
    rows = [("режим", "Docker — %s" % info["container"] if info["mode"] == "docker"
             else "нативная установка"),
            ("интерфейс", info["iface"]),
            ("конфиг", info["conf"]),
            ("endpoint", "%s:%s" % (info["endpoint_host"] or "?", info["port"] or "?")),
            ("подсеть", "%s (сервер %s)" % (info["subnet"], info["server_ip"])),
            ("public key", info["public_key"] or "не определён"),
            ("обфускация", ", ".join(info["obfuscation"]) or "нет")]
    text = "\n".join("  %-12s %s" % (k, v) for k, v in rows)
    for note in info["notes"]:
        text += "\n  · %s" % note
    for warning in info["warnings"]:
        text += "\n  ! %s" % warning
    return text


def main(argv=None):
    parser = argparse.ArgumentParser(prog="awgbot.detect", description=__doc__)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--host", default=None, help="public host/ip clients connect to")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--write-env", metavar="PATH")
    parser.add_argument("--token", default="")
    parser.add_argument("--admin", default="")
    parser.add_argument("--dest", default="/opt/deeptraversal")
    parser.add_argument("--mtu", type=int, default=1280)
    parser.add_argument("--dns", default="1.1.1.1")
    args = parser.parse_args(argv)

    try:
        info = detect(host=args.host, port=args.port)
    except DetectError as exc:
        sys.stderr.write("detect failed: %s\n" % exc)
        return 2

    if args.write_env:
        old = existing_values(args.write_env)
        token = args.token or old.get("BOT_TOKEN", "")
        admins = args.admin or old.get("ADMIN_IDS", "")
        if not args.host:
            info["endpoint_host"] = old.get("ENDPOINT_HOST") or info["endpoint_host"]
        if not args.port and old.get("ENDPOINT_PORT"):
            info["port"] = old["ENDPOINT_PORT"]
        content = render_env(info, token, admins, args.dest, args.dest, args.mtu, args.dns)
        fd = os.open(args.write_env, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.chmod(args.write_env, 0o600)
        print("wrote %s" % args.write_env)
        return 0

    if args.json:
        print(json.dumps(info, indent=2, ensure_ascii=False))
    else:
        print(summary(info))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
