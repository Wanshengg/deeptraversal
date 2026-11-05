"""Thin Telegram Bot API client (long polling, no framework)."""

import json

import requests


class TelegramError(Exception):
    def __init__(self, method, description, status=None):
        Exception.__init__(self, "%s: %s" % (method, description))
        self.method = method
        self.description = description
        self.status = status


class Telegram(object):
    def __init__(self, token, api_base="https://api.telegram.org", timeout=30):
        self.token = token
        self.api_base = api_base.rstrip("/")
        self.http = requests.Session()
        self.timeout = timeout

    def url(self, method):
        return "%s/bot%s/%s" % (self.api_base, self.token, method)

    def call(self, method, http_timeout=None, **params):
        payload = {k: v for k, v in params.items() if v is not None}
        for key, value in list(payload.items()):
            # Telegram wants JSON for structured fields; requests would otherwise send
            # a python repr and the API answers "can't parse ... JSON object".
            if isinstance(value, (dict, list)) and not isinstance(value, str):
                payload[key] = json.dumps(value)
        response = self.http.post(self.url(method), data=payload,
                                  timeout=http_timeout or self.timeout)
        try:
            body = response.json()
        except ValueError:
            raise TelegramError(method, "non-JSON response: %s" % response.text[:200],
                                response.status_code)
        if not body.get("ok"):
            raise TelegramError(method, body.get("description", "unknown error"),
                                response.status_code)
        return body.get("result")

    # --- endpoints used by the bot --------------------------------------------
    def get_me(self):
        return self.call("getMe")

    def get_updates(self, offset=None, timeout=50):
        return self.call("getUpdates", http_timeout=timeout + 15, offset=offset,
                         timeout=timeout,
                         allowed_updates=["message", "callback_query"]) or []

    def send_message(self, chat_id, text, reply_markup=None, parse_mode="HTML"):
        return self.call("sendMessage", chat_id=chat_id, text=text, parse_mode=parse_mode,
                         reply_markup=reply_markup, disable_web_page_preview=True)

    def edit_message_text(self, chat_id, message_id, text, reply_markup=None,
                          parse_mode="HTML"):
        return self.call("editMessageText", chat_id=chat_id, message_id=message_id, text=text,
                         parse_mode=parse_mode, reply_markup=reply_markup)

    def answer_callback_query(self, callback_query_id, text=None, show_alert=False):
        return self.call("answerCallbackQuery", callback_query_id=callback_query_id, text=text,
                         show_alert=show_alert)

    def send_document(self, chat_id, filename, content, caption=None):
        response = self.http.post(
            self.url("sendDocument"),
            data={"chat_id": chat_id, "caption": caption or ""},
            files={"document": (filename, content, "application/octet-stream")},
            timeout=self.timeout,
        )
        try:
            body = response.json()
        except ValueError:
            raise TelegramError("sendDocument", "non-JSON response", response.status_code)
        if not body.get("ok"):
            raise TelegramError("sendDocument", body.get("description", "unknown"),
                                response.status_code)
        return body.get("result")

    def set_my_commands(self):
        return self.call("setMyCommands", commands=[
            {"command": "start", "description": "Получить ключ"},
            {"command": "keys", "description": "Мои ключи"},
            {"command": "key", "description": "Прислать конфиг ещё раз"},
            {"command": "revoke", "description": "Отозвать ключ"},
            {"command": "id", "description": "Мой Telegram ID"},
            {"command": "help", "description": "Помощь"},
        ])
