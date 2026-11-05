"""Bot logic: framework-free update router over the thin Telegram client."""

import html
import logging
import time

from .provision import ProvisionError, safe_device_name

log = logging.getLogger("awgbot")

TEXT_START = (
    "<b>AmneziaWG · выдача ключей</b>\n\n"
    "Я выдам тебе конфиг для приложения AmneziaVPN (протокол AmneziaWG).\n"
    "Можно держать несколько устройств — телефон, ноутбук, планшет: жми «Новый ключ» "
    "столько раз, сколько нужно.\n"
    "Ключ приходит ссылкой <code>vpn://</code> и файлом <code>.conf</code>."
)
TEXT_HELP = (
    "<b>Команды</b>\n"
    "/start — получить ключ\n"
    "/keys — мои ключи: прислать заново или удалить\n"
    "/key — прислать конфиг ещё раз\n"
    "/revoke — отозвать ключ (доступ сразу закроется)\n"
    "/id — мой Telegram ID\n"
    "/help — эта справка\n\n"
    "Импорт: AmneziaVPN → «+» → «Импорт из буфера обмена» (ссылка) или «Импорт из файла»"
)
TEXT_ADMIN_HELP = (
    "<b>Админ</b>\n"
    "/status — статистика\n"
    "/pending — заявки в очереди\n"
    "/approve <id> — выдать доступ\n"
    "/reject <id> — отказать\n"
    "/ban <tg_id> · /unban <tg_id>\n"
    "/revoke <tg_id> — отозвать ключ пользователя\n"
    "/prune — подчистить ключи, удалённые мимо бота (например в приложении)"
)


TEXT_LINK = (
    "Импорт: приложение AmneziaVPN → «+» → «Импорт из буфера обмена».\n"
    "Скопируй строку целиком (тап по ней копирует):\n\n"
    "<code>%s</code>"
)
TEXT_LINK_TOO_LONG = (
    "Ссылка получилась длиннее лимита сообщения Telegram, поэтому отправляю файлом: "
    "AmneziaVPN → «+» → «Импорт из файла»."
)


def esc(value):
    return html.escape(str(value), quote=False)


def kb(*rows):
    return {"inline_keyboard": [list(row) for row in rows]}


def btn(text, data):
    return {"text": text, "callback_data": data}


class Bot(object):
    def __init__(self, cfg, tg, store, prov, backend=None, clock=time.time):
        self.cfg = cfg
        self.tg = tg
        self.store = store
        self.prov = prov
        self.backend = backend
        self.clock = clock
        self.sessions = {}

    # --- entry point -----------------------------------------------------------
    def handle(self, update):
        try:
            if "message" in update:
                self._on_message(update["message"])
            elif "callback_query" in update:
                self._on_callback(update["callback_query"])
        except Exception:  # keep the polling loop alive, log the traceback
            log.exception("update failed: %s", update.get("update_id"))

    # --- helpers ---------------------------------------------------------------
    def is_admin(self, tg_id):
        return int(tg_id) in self.cfg.admin_ids

    def _user_status(self, tg_id):
        row = self.store.user(tg_id)
        return row["status"] if row else "new"

    def _reply(self, chat_id, text, markup=None):
        return self.tg.send_message(chat_id, text, reply_markup=markup)

    def _send_conf(self, chat_id, filename, content):
        self.tg.send_document(chat_id, filename, content.encode("utf-8"),
                              caption="Твой конфиг AmneziaWG. Импортируй в AmneziaVPN.")

    def _send_delivery(self, chat_id, delivery):
        """vpn:// link first (one tap to import), .conf file as the fallback."""
        if delivery.link:
            if len(delivery.link) + len(TEXT_LINK) < 4000:
                self._reply(chat_id, TEXT_LINK % esc(delivery.link))
            else:
                self._reply(chat_id, TEXT_LINK_TOO_LONG)
        if self.cfg.send_conf_file:
            caption = "Файл конфига — AmneziaVPN → «+» → «Импорт из файла»."
            self.tg.send_document(chat_id, delivery.filename,
                                  delivery.conf.encode("utf-8"), caption=caption)

    # --- messages --------------------------------------------------------------
    def _on_message(self, msg):
        chat_id = msg["chat"]["id"]
        from_user = msg.get("from") or {}
        tg_id = from_user.get("id", chat_id)
        text = (msg.get("text") or "").strip()
        self.store.touch_user(tg_id, from_user.get("username"))

        if text.startswith("/"):
            command, _, args = text.partition(" ")
            command = command.split("@")[0].lower()
            args = args.strip()
            handler = getattr(self, "_cmd_" + command.lstrip("/").replace("-", "_"), None)
            if handler:
                handler(chat_id, tg_id, args)
            else:
                self._reply(chat_id, "Неизвестная команда. /help")
            return

        session = self.sessions.get(tg_id)
        if session and session.get("await") == "device":
            self.sessions.pop(tg_id, None)
            self._create_request(chat_id, tg_id, text)
            return

        self._reply(chat_id, "Нажми /start, чтобы получить ключ.")

    # --- commands --------------------------------------------------------------
    def _cmd_start(self, chat_id, tg_id, args):
        status = self._user_status(tg_id)
        if status == "banned":
            self._reply(chat_id, "Доступ к боту закрыт.")
            return
        peers = self.store.active_peers(tg_id)
        rows = [[btn("Новый ключ", "new")]]
        if peers:
            rows.append([btn("Мои ключи (%s)" % len(peers), "list")])
        self._reply(chat_id, TEXT_START, kb(*rows))

    def _cmd_key(self, chat_id, tg_id, args):
        peers = self.store.active_peers(tg_id)
        if len(peers) == 1:
            self._send_delivery(chat_id, self.prov.resend_peer(peers[0].id, tg_id))
        elif peers:
            self._show_keys(chat_id, tg_id)
        else:
            self._reply(chat_id, "Активного ключа нет. /start")

    def _cmd_keys(self, chat_id, tg_id, args):
        self._show_keys(chat_id, tg_id)

    def _cmd_help(self, chat_id, tg_id, args):
        text = TEXT_HELP
        if self.is_admin(tg_id):
            text += "\n\n" + TEXT_ADMIN_HELP
        self._reply(chat_id, text)

    def _cmd_id(self, chat_id, tg_id, args):
        self._reply(chat_id, "Твой Telegram ID: <code>%s</code>" % tg_id)

    def _cmd_revoke(self, chat_id, tg_id, args):
        if self.is_admin(tg_id) and args.isdigit():
            count = self.prov.revoke_all(int(args), actor=tg_id)
            self._reply(chat_id, "Отозвано ключей: %s" % count)
            return
        peers = self.store.active_peers(tg_id)
        if not peers:
            self._reply(chat_id, "Активных ключей нет.")
            return
        if len(peers) > 1:
            # с нескольких устройств удаляем выборочно
            self._show_keys(chat_id, tg_id, delete_only=True)
            return
        peer = self.prov.revoke_peer(peers[0].id, tg_id)
        self._reply(chat_id, "Ключ %s (%s) отозван. Новый — /start."
                    % (esc(peer.device), esc(peer.ip)))

    def _show_keys(self, chat_id, tg_id, delete_only=False):
        peers = self.store.active_peers(tg_id)
        limit = self.prov.peer_limit
        header = "<b>Мои ключи: %s%s</b>" % (len(peers), (" из %s" % limit if limit else ""))
        if not peers:
            self._reply(chat_id, "Активных ключей нет. /start")
            return
        lines = [header, ""]
        rows = []
        for peer in peers:
            lines.append("· <code>%s</code> — <code>%s</code>"
                         % (esc(peer.device), esc(peer.ip)))
            if delete_only:
                rows.append([btn("Удалить %s" % peer.device, "delok:%s" % peer.id)])
            else:
                rows.append([btn("Прислать %s" % peer.device, "send:%s" % peer.id),
                             btn("Удалить %s" % peer.device, "del:%s" % peer.id)])
        lines.append("")
        lines.append("«Прислать» — заново отправит ссылку и файл, «Удалить» — сразу закроет доступ.")
        self._reply(chat_id, "\n".join(lines), kb(*rows))

    def _cmd_prune(self, chat_id, tg_id, args):
        if not self.is_admin(tg_id):
            self._reply(chat_id, "Команда только для админа.")
            return
        force = "force" in args.split()
        try:
            result = self.prov.prune(actor=tg_id, force=force)
        except ProvisionError as exc:
            self._reply(chat_id, "Prune не выполнен: %s" % esc(exc))
            return
        self._reply(chat_id, "<b>Prune</b>\n%s" % esc(result.summary()))

    def _cmd_status(self, chat_id, tg_id, args):
        if not self.is_admin(tg_id):
            self._reply(chat_id, "Команда только для админа.")
            return
        stats = self.store.stats()
        lines = [
            "<b>Статус</b>",
            "пользователей: %s" % stats["users"],
            "активных ключей: %s" % stats["peers"],
            "заявок в очереди: %s" % stats["pending"],
            "подсеть: <code>%s</code> (занято %s)" % (
                self.cfg.subnet, len(self.store.used_ips())),
            "endpoint: <code>%s:%s</code>" % (self.cfg.endpoint_host, self.cfg.endpoint_port),
        ]
        self._reply(chat_id, "\n".join(lines))

    def _cmd_pending(self, chat_id, tg_id, args):
        if not self.is_admin(tg_id):
            self._reply(chat_id, "Команда только для админа.")
            return
        rows = self.store.pending_requests()
        if not rows:
            self._reply(chat_id, "Очередь пуста.")
            return
        for row in rows:
            self.tg.send_message(chat_id, self._request_text(row),
                                 reply_markup=self._request_keyboard(row["id"]))

    def _cmd_approve(self, chat_id, tg_id, args):
        if not self.is_admin(tg_id):
            self._reply(chat_id, "Команда только для админа.")
            return
        if not args.isdigit():
            self._reply(chat_id, "Формат: /approve &lt;id заявки&gt;")
            return
        self._decide(chat_id, tg_id, int(args), approve=True)

    def _cmd_reject(self, chat_id, tg_id, args):
        if not self.is_admin(tg_id):
            self._reply(chat_id, "Команда только для админа.")
            return
        if not args.isdigit():
            self._reply(chat_id, "Формат: /reject &lt;id заявки&gt;")
            return
        self._decide(chat_id, tg_id, int(args), approve=False)

    def _cmd_ban(self, chat_id, tg_id, args):
        if not self.is_admin(tg_id):
            self._reply(chat_id, "Команда только для админа.")
            return
        if not args.isdigit():
            self._reply(chat_id, "Формат: /ban &lt;tg_id&gt;")
            return
        self.store.set_user_status(int(args), "banned", note="banned by %s" % tg_id)
        self.prov.revoke_all(int(args), actor=tg_id)
        self.store.audit(tg_id, "ban", args)
        self._reply(chat_id, "Пользователь %s забанен, ключ отозван." % args)

    def _cmd_unban(self, chat_id, tg_id, args):
        if not self.is_admin(tg_id):
            self._reply(chat_id, "Команда только для админа.")
            return
        if not args.isdigit():
            self._reply(chat_id, "Формат: /unban &lt;tg_id&gt;")
            return
        self.store.set_user_status(int(args), "approved", note="unbanned by %s" % tg_id)
        self.store.audit(tg_id, "unban", args)
        self._reply(chat_id, "Пользователь %s разбанен." % args)

    # --- key flow --------------------------------------------------------------
    def _request_text(self, row):
        username = ""
        user = self.store.user(row["tg_id"])
        if user and user["username"]:
            username = " (@%s)" % user["username"]
        return ("<b>Заявка #%s</b>\n"
                "пользователь: <code>%s</code>%s\n"
                "устройство: %s\n"
                "время: %s" % (row["id"], row["tg_id"], esc(username),
                               esc(row["device"]),
                               time.strftime("%Y-%m-%d %H:%M", time.localtime(row["created_at"]))))

    def _request_keyboard(self, req_id):
        return kb([btn("Выдать", "req:%s:ok" % req_id), btn("Отказать", "req:%s:no" % req_id)])

    def _start_request(self, chat_id, tg_id):
        status = self._user_status(tg_id)
        if status == "banned":
            self._reply(chat_id, "Доступ к боту закрыт.")
            return
        last = self.store.last_request_at(tg_id)
        if last and self.clock() - last < self.cfg.cooldown_s:
            wait = int(self.cfg.cooldown_s - (self.clock() - last))
            self._reply(chat_id, "Слишком часто. Попробуй через %s с." % wait)
            return
        limit = self.prov.peer_limit
        peers = self.store.active_peers(tg_id)
        if limit and len(peers) >= limit:
            self._reply(
                chat_id,
                "У вас уже %s ключ(ей) — это лимит (%s). Удалите ненужный и повторите."
                % (len(peers), limit),
                kb([btn("Мои ключи", "list")]))
            return
        self.sessions[tg_id] = {"await": "device"}
        hint = ""
        if peers:
            hint = " (у вас уже %s, это будет ещё один — например <code>%s</code>)" % (
                len(peers), "iphone-2")
        self._reply(
            chat_id,
            "Как назовём устройство? Пришли имя (например <code>iphone</code>, "
            "<code>laptop</code>). Имя попадёт в название файла конфига." + hint,
            kb([btn("Отмена", "cancel")]),
        )

    def _create_request(self, chat_id, tg_id, device):
        device = safe_device_name(device)
        if self.cfg.require_approval and not self.is_admin(tg_id):
            req_id = self.store.create_request(tg_id, device)
            self.store.audit(tg_id, "request", "id=%s device=%s" % (req_id, device))
            self._reply(chat_id, "Заявка #%s отправлена админу. Жди ответа." % req_id)
            self._notify_admins(req_id)
            return
        self._issue_now(chat_id, tg_id, device)

    def _notify_admins(self, req_id):
        row = self.store.request(req_id)
        if not row:
            return
        for admin_id in self.cfg.admin_ids:
            try:
                self.tg.send_message(admin_id, self._request_text(row),
                                     reply_markup=self._request_keyboard(req_id))
            except Exception:
                log.exception("cannot notify admin %s", admin_id)

    def _issue_now(self, chat_id, tg_id, device):
        try:
            delivery = self.prov.issue(tg_id, device, actor=chat_id)
        except ProvisionError as exc:
            self._reply(chat_id, "Не получилось: %s" % esc(exc))
            return
        except Exception:
            log.exception("issue failed for %s", tg_id)
            self._reply(chat_id, "Ошибка на сервере, попробуй позже или напиши админу.")
            return
        self.store.set_user_status(tg_id, "approved")
        self._send_delivery(chat_id, delivery)
        peers = self.store.active_peers(tg_id)
        limit = self.prov.peer_limit
        total = "%s%s" % (len(peers), (" из %s" % limit if limit else ""))
        self._reply(chat_id,
                    "Готово. Устройство <code>%s</code>, IP <code>%s</code>. "
                    "Всего ключей: %s.\n"
                    "Нужен ещё один — «Новый ключ» в /start; список и удаление — /keys."
                    % (esc(delivery.peer.device), esc(delivery.peer.ip), total))

    def _resend(self, chat_id, tg_id):
        peers = self.store.active_peers(tg_id)
        if not peers:
            self._reply(chat_id, "Активного ключа нет. /start")
            return
        if len(peers) == 1:
            self._send_delivery(chat_id, self.prov.resend_peer(peers[0].id, tg_id))
        else:
            self._show_keys(chat_id, tg_id)

    def _decide(self, chat_id, admin_id, req_id, approve):
        row = self.store.request(req_id)
        if not row:
            self._reply(chat_id, "Заявки #%s нет." % req_id)
            return
        if row["status"] != "pending":
            self._reply(chat_id, "Заявка #%s уже обработана (%s)." % (req_id, row["status"]))
            return
        if approve:
            self.store.decide_request(req_id, "approved", admin_id)
            self.store.set_user_status(row["tg_id"], "approved")
            self._issue_now(row["tg_id"], row["tg_id"], row["device"])
            self._reply(chat_id, "Заявка #%s выдана." % req_id)
        else:
            self.store.decide_request(req_id, "rejected", admin_id)
            self.store.audit(admin_id, "reject", "id=%s" % req_id)
            try:
                self.tg.send_message(row["tg_id"], "Заявка #%s отклонена админом." % req_id)
            except Exception:
                log.exception("cannot notify user %s", row["tg_id"])
            self._reply(chat_id, "Заявка #%s отклонена." % req_id)

    # --- callbacks -------------------------------------------------------------
    def _on_callback(self, cb):
        data = cb.get("data", "")
        message = cb.get("message") or {}
        chat_id = (message.get("chat") or {}).get("id")
        from_user = cb.get("from") or {}
        tg_id = from_user.get("id")
        self.tg.answer_callback_query(cb.get("id"))
        if chat_id is None or tg_id is None:
            return

        if data == "new":
            self._start_request(chat_id, tg_id)
        elif data in ("resend", "list"):
            self._show_keys(chat_id, tg_id)
        elif data == "cancel":
            self.sessions.pop(tg_id, None)
            self._reply(chat_id, "Отменено.")
        elif data.startswith("send:"):
            peer_id = data.split(":", 1)[1]
            delivery = (self.prov.resend_peer(int(peer_id), tg_id)
                        if peer_id.isdigit() else None)
            if delivery:
                self._send_delivery(chat_id, delivery)
            else:
                self._reply(chat_id, "Ключ не найден — возможно, уже удалён.")
        elif data.startswith("del:"):
            peer_id = data.split(":", 1)[1]
            peer = self.store.peer(int(peer_id)) if peer_id.isdigit() else None
            if not peer or peer.tg_id != tg_id:
                self._reply(chat_id, "Ключ не найден.")
                return
            self._reply(chat_id,
                        "Удалить ключ <code>%s</code> (%s)? Доступ закроется сразу."
                        % (esc(peer.device), esc(peer.ip)),
                        kb([btn("Удалить", "delok:%s" % peer.id), btn("Отмена", "cancel")]))
        elif data.startswith("delok:"):
            peer_id = data.split(":", 1)[1]
            peer = (self.prov.revoke_peer(int(peer_id), tg_id)
                    if peer_id.isdigit() else None)
            if peer:
                self._reply(chat_id, "Ключ <code>%s</code> (%s) удалён."
                            % (esc(peer.device), esc(peer.ip)))
            else:
                self._reply(chat_id, "Ключ не найден.")
        elif data.startswith("req:"):
            if not self.is_admin(tg_id):
                self._reply(chat_id, "Только админ может это сделать.")
                return
            _, req_id, action = data.split(":")
            if not req_id.isdigit():
                return
            self._decide(chat_id, tg_id, int(req_id), approve=(action == "ok"))
        else:
            log.warning("unknown callback: %s", data)
