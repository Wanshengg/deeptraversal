import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from awgbot.config import Config  # noqa: E402
from awgbot.store import Store  # noqa: E402


class FakeTelegram(object):
    """Records everything the bot tries to send."""

    def __init__(self):
        self.messages = []
        self.documents = []
        self.edits = []
        self.answers = []
        self._msg_id = 100

    def send_message(self, chat_id, text, reply_markup=None, parse_mode="HTML"):
        self._msg_id += 1
        self.messages.append({"chat_id": chat_id, "text": text,
                              "reply_markup": reply_markup})
        return {"message_id": self._msg_id}

    def edit_message_text(self, chat_id, message_id, text, reply_markup=None,
                          parse_mode="HTML"):
        self.edits.append({"chat_id": chat_id, "message_id": message_id, "text": text})
        return {"message_id": message_id}

    def answer_callback_query(self, callback_query_id, text=None, show_alert=False):
        self.answers.append({"id": callback_query_id, "text": text})

    def send_document(self, chat_id, filename, content, caption=None):
        self.documents.append({"chat_id": chat_id, "filename": filename,
                               "content": content, "caption": caption})
        return {"message_id": 1}

    def get_updates(self, offset=None, timeout=50):
        return []

    def set_my_commands(self):
        return True

    # test helpers
    def last_text_for(self, chat_id):
        for msg in reversed(self.messages):
            if msg["chat_id"] == chat_id:
                return msg["text"]
        return None

    def texts_for(self, chat_id):
        return [m["text"] for m in self.messages if m["chat_id"] == chat_id]


@pytest.fixture
def cfg(tmp_path):
    cfg = Config.__new__(Config)
    cfg.bot_token = "123:TEST"
    cfg.api_base = "https://api.telegram.org"
    cfg.admin_ids = (42,)
    cfg.backend = "mock"
    cfg.iface = "awg0"
    cfg.server_conf = str(tmp_path / "awg0.conf")
    cfg.awg_bin = "awg"
    cfg.awg_docker = ""
    cfg.vpn_link_container = ""
    cfg.server_public_key = "c2VydmVyLXB1YmxpYy1rZXktMzJieXRlcy1wYWRkZWQ="
    cfg.endpoint_host = "vpn.example.com"
    cfg.endpoint_port = 51820
    cfg.subnet = "10.8.0.0/29"     # small pool on purpose: 6 usable addresses
    cfg.server_ip = "10.8.0.1"
    cfg.reserved_ips = ()
    cfg.dns = "1.1.1.1"
    cfg.allowed_ips = "0.0.0.0/0, ::/0"
    cfg.keepalive = 25
    cfg.mtu = None
    cfg.db_path = str(tmp_path / "awgbot.db")
    cfg.max_peers_per_user = 5
    cfg.cooldown_s = 0
    cfg.require_approval = True
    cfg.send_vpn_link = True
    cfg.send_conf_file = True
    cfg.mock_state = str(tmp_path / "mock_state.json")
    cfg.log_level = "DEBUG"
    return cfg


@pytest.fixture
def store(cfg):
    s = Store(cfg.db_path)
    yield s
    s.close()


@pytest.fixture
def backend(cfg):
    from awgbot.awg import Mock
    return Mock(cfg)


@pytest.fixture
def prov(cfg, store, backend):
    from awgbot.provision import Provisioner
    return Provisioner(cfg, store, backend)


@pytest.fixture
def tg():
    return FakeTelegram()


@pytest.fixture
def bot(cfg, tg, store, prov, backend):
    from awgbot.bot import Bot
    return Bot(cfg, tg, store, prov, backend=backend)


SRV_PUB = "c2VydmVyLXB1YmxpYy1rZXktMzJieXRlcy1wYWRkZWQ="


def message(chat_id, text, username="ivan"):
    return {"update_id": 1, "message": {
        "message_id": 7, "text": text, "chat": {"id": chat_id},
        "from": {"id": chat_id, "username": username},
    }}


def callback(chat_id, data, admin=True):
    return {"update_id": 2, "callback_query": {
        "id": "cb1", "data": data, "from": {"id": chat_id},
        "message": {"message_id": 9, "chat": {"id": chat_id}},
    }}
