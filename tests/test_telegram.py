import json

import pytest

from awgbot.telegram import Telegram, TelegramError


class FakeResponse(object):
    def __init__(self, payload, status=200, text=None):
        self._payload = payload
        self.status_code = status
        self.text = text if text is not None else json.dumps(payload)

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession(object):
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, data=None, files=None, timeout=None):
        self.calls.append({"url": url, "data": data, "files": files, "timeout": timeout})
        return self.responses.pop(0)


def client(*responses):
    tg = Telegram("123:ABC")
    tg.http = FakeSession(*responses)
    return tg


def test_url_contains_token():
    tg = Telegram("123:ABC")
    assert tg.url("getMe") == "https://api.telegram.org/bot123:ABC/getMe"


def test_send_message_serialises_markup_and_drops_none():
    tg = client(FakeResponse({"ok": True, "result": {"message_id": 1}}))
    tg.send_message(5, "hi", reply_markup={"inline_keyboard": []}, parse_mode=None)
    data = tg.http.calls[0]["data"]
    assert data["chat_id"] == 5
    assert json.loads(data["reply_markup"]) == {"inline_keyboard": []}
    assert "parse_mode" not in data          # None must not be sent


def test_api_error_becomes_exception():
    tg = client(FakeResponse({"ok": False, "description": "chat not found"}, status=400))
    with pytest.raises(TelegramError) as exc:
        tg.send_message(5, "hi")
    assert "chat not found" in str(exc.value)


def test_non_json_response_becomes_exception():
    tg = client(FakeResponse(None, status=502, text="<html>bad gateway</html>"))
    with pytest.raises(TelegramError):
        tg.call("getMe")


def test_send_document_uses_multipart():
    tg = client(FakeResponse({"ok": True, "result": {"message_id": 2}}))
    tg.send_document(5, "x.conf", b"[Interface]\n", caption="cap")
    call = tg.http.calls[0]
    assert call["files"]["document"][0] == "x.conf"
    assert call["files"]["document"][1] == b"[Interface]\n"
    assert call["data"]["chat_id"] == 5


def test_get_updates_passes_long_poll_timeout():
    tg = client(FakeResponse({"ok": True, "result": []}))
    assert tg.get_updates(offset=7, timeout=50) == []
    call = tg.http.calls[0]
    assert call["data"]["timeout"] == 50
    assert call["data"]["offset"] == 7
    assert call["timeout"] == 65
    assert call["data"]["allowed_updates"] == ["message", "callback_query"]
