"""Entry point: `python -m awgbot` runs the bot, flags give the ops commands."""

import argparse
import json
import logging
import os
import sys
import time

from . import config as config_mod
from .awg import BackendError, Mock, make_backend
from .bot import Bot
from .config import ConfigError
from .provision import Provisioner
from .store import Store
from .telegram import Telegram, TelegramError

log = logging.getLogger("awgbot")


def build(cfg):
    store = Store(cfg.db_path)
    backend = make_backend(cfg)
    prov = Provisioner(cfg, store, backend)
    return store, backend, prov


def offset_path(cfg):
    return os.path.join(os.path.dirname(os.path.abspath(cfg.db_path)), ".bot.offset")


def load_offset(cfg):
    try:
        with open(offset_path(cfg), "r", encoding="utf-8") as fh:
            return json.load(fh).get("offset")
    except (IOError, ValueError):
        return None


def save_offset(cfg, offset):
    path = offset_path(cfg)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"offset": offset}, fh)


def cmd_check(cfg):
    print("settings:\n%s" % cfg.describe())
    cfg.validate(need_token=False)   # the token is only needed to actually poll Telegram
    store, backend, _ = build(cfg)
    print("server public key: %s" % backend.server_public_key())
    print("obfuscation params: %s" % (", ".join(k for k, _ in backend.obfuscation()) or "none"))
    print("peers in db: %s" % len(store.active_peers()))
    if isinstance(backend, Mock):
        print("mock backend: no system changes")
    else:
        print("live peers on %s: %s" % (cfg.iface, len(backend.live_peers())))
    print("OK")
    return 0


def cmd_list(cfg):
    store, _, _ = build(cfg)
    peers = store.active_peers()
    if not peers:
        print("no active peers")
        return 0
    print("%-4s %-10s %-16s %-20s %s" % ("id", "tg_id", "ip", "device", "public_key"))
    for peer in peers:
        print("%-4s %-10s %-16s %-20s %s" % (peer.id, peer.tg_id, peer.ip, peer.device,
                                            peer.public_key))
    return 0


def cmd_revoke(cfg, tg_id):
    store, _, prov = build(cfg)
    count = prov.revoke_all(tg_id, actor="cli")
    print("revoked %s peer(s) of %s" % (count, tg_id))
    return 0


def cmd_export(cfg, tg_id, link_only=False):
    store, _, prov = build(cfg)
    delivery = prov.resend(tg_id)
    if not delivery:
        print("no active peer for %s" % tg_id)
        return 1
    if link_only:
        if not delivery.link:
            print("no vpn:// link for %s (SEND_VPN_LINK is off or the config is incomplete)"
                  % tg_id)
            return 1
        print(delivery.link)
        return 0
    path = os.path.join(prov.export_dir(), delivery.filename)
    print(delivery.conf)
    if delivery.link:
        print("vpn:// link (%s chars):" % len(delivery.link))
        print(delivery.link)
    print("saved to %s" % path if os.path.exists(path) else "not saved")
    return 0


def run_bot(cfg):
    store, backend, prov = build(cfg)
    tg = Telegram(cfg.bot_token, api_base=cfg.api_base)
    me = tg.get_me()
    log.info("connected as @%s", me.get("username"))
    bot = Bot(cfg, tg, store, prov, backend=backend)
    try:
        tg.set_my_commands()
    except Exception:
        log.warning("setMyCommands failed", exc_info=True)
    offset = load_offset(cfg)
    while True:
        try:
            updates = tg.get_updates(offset=offset, timeout=50)
        except Exception:
            log.warning("getUpdates failed, retrying in 5s", exc_info=True)
            time.sleep(5)
            continue
        for update in updates:
            offset = update["update_id"] + 1
            save_offset(cfg, offset)
            bot.handle(update)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="awgbot", description="AmneziaWG key bot")
    parser.add_argument("--env", default=".env", help="path to .env (default: ./.env)")
    parser.add_argument("--mock", action="store_true",
                        help="force the mock backend (no system changes)")
    parser.add_argument("--check", action="store_true", help="validate config and exit")
    parser.add_argument("--list", action="store_true", help="list active peers and exit")
    parser.add_argument("--revoke", metavar="TG_ID", type=int,
                        help="revoke all peers of a Telegram user")
    parser.add_argument("--export", metavar="TG_ID", type=int,
                        help="print and re-export a user's config")
    parser.add_argument("--link", action="store_true",
                        help="with --export: print only the vpn:// link")
    args = parser.parse_args(argv)

    cfg = config_mod.load(args.env)
    if args.mock:
        cfg.backend = "mock"
    logging.basicConfig(
        level=getattr(logging, cfg.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        if args.check:
            return cmd_check(cfg)
        if args.list:
            return cmd_list(cfg)
        if args.revoke:
            return cmd_revoke(cfg, args.revoke)
        if args.export:
            return cmd_export(cfg, args.export, link_only=args.link)
        cfg.validate()
        run_bot(cfg)
        return 0
    except (ConfigError, BackendError, TelegramError) as exc:  # pragma: no cover - ops path
        log.error("%s", exc)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
