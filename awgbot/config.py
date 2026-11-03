"""Configuration: environment variables, optionally seeded from a .env file."""

import os
import sys

# Interface-level AmneziaWG obfuscation parameters that must be copied verbatim from the
# server config into every client config, otherwise the handshake is dropped by the server.
OBFUSCATION_KEYS = (
    "Jc", "Jmin", "Jmax", "S1", "S2", "H1", "H2", "H3", "H4",
    "I1", "I2", "I3", "I4", "I5", "J1", "J2", "J3", "Itime",
)


def load_env_file(path):
    """Minimal .env loader. Real environment wins over the file."""
    if not path or not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


class ConfigError(Exception):
    pass


def _env(name, default=None):
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value


def _env_int(name, default):
    raw = _env(name)
    return default if raw is None else int(raw)


def _env_bool(name, default):
    raw = _env(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int_tuple(name):
    raw = _env(name, "")
    ids = []
    for chunk in raw.replace(";", ",").split(","):
        chunk = chunk.strip()
        if chunk:
            ids.append(int(chunk))
    return tuple(ids)


class Config(object):
    def __init__(self):
        self.bot_token = _env("BOT_TOKEN", "")
        self.api_base = _env("TELEGRAM_API_BASE", "https://api.telegram.org")
        self.admin_ids = _env_int_tuple("ADMIN_IDS")
        self.backend = _env("BACKEND", "awg")          # "awg" (real) or "mock" (dry run)
        self.iface = _env("AWG_IFACE", "awg0")
        self.server_conf = _env("AWG_CONF", "/etc/amnezia/amneziawg/awg0.conf")
        self.awg_bin = _env("AWG_BIN", "awg")
        self.server_public_key = _env("SERVER_PUBLIC_KEY", "")
        self.endpoint_host = _env("ENDPOINT_HOST", "")
        self.endpoint_port = _env_int("ENDPOINT_PORT", 51820)
        self.subnet = _env("SUBNET", "10.8.0.0/24")
        self.server_ip = _env("SERVER_IP", "10.8.0.1")
        self.reserved_ips = tuple(
            ip.strip() for ip in _env("RESERVED_IPS", "").split(",") if ip.strip()
        )
        self.dns = _env("DNS", "1.1.1.1")
        self.allowed_ips = _env("ALLOWED_IPS", "0.0.0.0/0, ::/0")
        self.keepalive = _env_int("PERSISTENT_KEEPALIVE", 25)
        self.mtu = _env_int("MTU", 0) or None
        self.db_path = _env("DB_PATH", "awgbot.db")
        self.max_peers_per_user = _env_int("MAX_PEERS_PER_USER", 1)
        self.cooldown_s = _env_int("REQUEST_COOLDOWN_S", 60)
        self.require_approval = _env_bool("REQUIRE_APPROVAL", True)
        self.mock_state = _env("MOCK_STATE", "mock_awg_state.json")
        self.log_level = _env("LOG_LEVEL", "INFO")

    def validate(self, need_token=True):
        problems = []
        if need_token and not self.bot_token:
            problems.append("BOT_TOKEN is empty")
        if not self.endpoint_host:
            problems.append("ENDPOINT_HOST is empty (public host clients connect to)")
        if self.backend not in ("awg", "mock"):
            problems.append("BACKEND must be 'awg' or 'mock'")
        if self.backend == "awg":
            if not os.path.exists(self.server_conf):
                problems.append("AWG_CONF does not exist: %s" % self.server_conf)
            if not self.server_public_key:
                problems.append("SERVER_PUBLIC_KEY is empty")
        if problems:
            raise ConfigError("; ".join(problems))
        return self

    def describe(self):
        rows = [
            ("backend", self.backend),
            ("interface", self.iface),
            ("server conf", self.server_conf),
            ("endpoint", "%s:%s" % (self.endpoint_host, self.endpoint_port)),
            ("subnet", "%s (server %s)" % (self.subnet, self.server_ip)),
            ("dns", self.dns),
            ("allowed ips", self.allowed_ips),
            ("db", self.db_path),
            ("max peers/user", self.max_peers_per_user),
            ("approval required", self.require_approval),
            ("admins", self.admin_ids),
        ]
        return "\n".join("  %-18s %s" % (k, v) for k, v in rows)


def load(dotenv=".env"):
    load_env_file(dotenv)
    return Config()


if __name__ == "__main__":  # pragma: no cover
    cfg = load()
    sys.stdout.write(cfg.describe() + "\n")
