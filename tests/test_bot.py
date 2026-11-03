from awgbot.bot import Bot


def approve_flow(bot, tg, user_id=100, device="iphone", req_id=1):
    bot.handle({"update_id": 1, "message": {
        "message_id": 1, "text": "/start", "chat": {"id": user_id},
        "from": {"id": user_id, "username": "ivan"}}})
    bot.handle({"update_id": 2, "callback_query": {
        "id": "cb1", "data": "new", "from": {"id": user_id},
        "message": {"message_id": 2, "chat": {"id": user_id}}}})
    bot.handle({"update_id": 3, "message": {
        "message_id": 3, "text": device, "chat": {"id": user_id},
        "from": {"id": user_id, "username": "ivan"}}})
    bot.handle({"update_id": 4, "callback_query": {
        "id": "cb2", "data": "req:%s:ok" % req_id, "from": {"id": 42},
        "message": {"message_id": 4, "chat": {"id": 42}}}})


def test_start_shows_key_button(bot, tg):
    from tests.conftest import message
    bot.handle(message(100, "/start"))
    assert tg.messages[-1]["chat_id"] == 100
    assert "AmneziaWG" in tg.messages[-1]["text"]
    labels = [b["text"] for row in tg.messages[-1]["reply_markup"]["inline_keyboard"]
              for b in row]
    assert "Получить ключ" in labels


def test_full_approval_flow_issues_config(bot, tg, store):
    from tests.conftest import message, callback
    bot.handle(message(100, "/start"))
    bot.handle(callback(100, "new"))
    assert "устройство" in tg.last_text_for(100).lower()
    bot.handle(message(100, "iPhone 15"))

    # admin got a review card for request #1
    assert 42 in [m["chat_id"] for m in tg.messages]
    pending = store.pending_requests()
    assert len(pending) == 1
    assert pending[0]["device"] == "iPhone15"
    assert tg.documents == []                # nothing issued before approval

    bot.handle(callback(42, "req:1:ok"))
    assert len(tg.documents) == 1
    doc = tg.documents[0]
    assert doc["chat_id"] == 100
    assert doc["filename"] == "amnezia-iPhone15-10.8.0.2.conf"
    assert b"[Interface]" in doc["content"] and b"[Peer]" in doc["content"]
    assert store.active_peers(100)[0].ip == "10.8.0.2"
    assert store.user(100)["status"] == "approved"
    assert store.request(1)["status"] == "approved"


def test_rejection_notifies_user_and_issues_nothing(bot, tg, store):
    from tests.conftest import message, callback
    bot.handle(message(100, "/start"))
    bot.handle(callback(100, "new"))
    bot.handle(message(100, "ipad"))
    bot.handle(callback(42, "req:1:no"))
    assert tg.documents == []
    assert store.active_peers(100) == []
    assert store.request(1)["status"] == "rejected"
    assert any("отклонена" in t for t in tg.texts_for(100))


def test_non_admin_cannot_approve(bot, tg, store):
    from tests.conftest import message, callback
    bot.handle(message(100, "/start"))
    bot.handle(callback(100, "new"))
    bot.handle(message(100, "ipad"))
    bot.handle(callback(100, "req:1:ok"))     # same user tries to self-approve
    assert tg.documents == []
    assert store.active_peers(100) == []


def test_key_command_resends_without_new_peer(bot, tg, store):
    from tests.conftest import message
    approve_flow(bot, tg)
    assert len(store.active_peers(100)) == 1
    tg.documents = []
    bot.handle(message(100, "/key"))
    assert len(tg.documents) == 1
    assert len(store.active_peers(100)) == 1


def test_revoke_command(bot, tg, store, backend):
    from tests.conftest import message
    approve_flow(bot, tg)
    bot.handle(message(100, "/revoke"))
    assert store.active_peers(100) == []
    assert backend.live_peers() == []
    assert "отозван" in tg.last_text_for(100).lower()


def test_cooldown_blocks_repeated_requests(cfg, bot, tg, store):
    from tests.conftest import message, callback
    cfg.cooldown_s = 120
    bot.clock = lambda: 1000.0
    bot.handle(message(100, "/start"))
    bot.handle(callback(100, "new"))
    bot.handle(message(100, "iphone"))       # creates request at t=1000
    bot.handle(callback(100, "new"))         # too early
    assert "Слишком часто" in tg.last_text_for(100)
    assert len(store.pending_requests()) == 1


def test_banned_user_gets_nothing(bot, tg, store):
    from tests.conftest import message
    store.set_user_status(100, "banned")
    bot.handle(message(100, "/start"))
    assert "закрыт" in tg.last_text_for(100)
    assert tg.messages[-1]["reply_markup"] is None


def test_admin_skips_approval_when_required(cfg, bot, tg, store):
    from tests.conftest import message, callback
    bot.handle(message(42, "/start"))        # 42 is the admin
    bot.handle(callback(42, "new"))
    bot.handle(message(42, "server-laptop"))
    assert len(tg.documents) == 1
    assert store.pending_requests() == []


def test_no_approval_mode_issues_directly(cfg, bot, tg, store):
    from tests.conftest import message, callback
    cfg.require_approval = False
    bot.handle(message(100, "/start"))
    bot.handle(callback(100, "new"))
    bot.handle(message(100, "iphone"))
    assert len(tg.documents) == 1
    assert store.pending_requests() == []


def test_device_name_is_sanitised(bot, tg, store):
    from tests.conftest import message, callback
    bot.handle(message(100, "/start"))
    bot.handle(callback(100, "new"))
    bot.handle(message(100, "../../etc/passwd"))
    assert store.pending_requests()[0]["device"] == "etcpasswd"
    assert "/" not in tg.documents[0]["filename"] if tg.documents else True


def test_id_and_help_and_status(bot, tg):
    from tests.conftest import message
    bot.handle(message(100, "/id"))
    assert "100" in tg.last_text_for(100)
    bot.handle(message(100, "/help"))
    assert "/revoke" in tg.last_text_for(100)
    bot.handle(message(42, "/status"))
    assert "активных ключей" in tg.last_text_for(42)
    bot.handle(message(100, "/status"))
    assert "админа" in tg.last_text_for(100)


def test_unknown_command_and_callback_do_not_crash(bot, tg):
    from tests.conftest import message, callback
    bot.handle(message(100, "/nope"))
    bot.handle(callback(42, "req:abc:ok"))
    bot.handle(callback(42, "weird"))
    assert tg.answers                          # callbacks always answered


def _extract_link(texts):
    import re
    found = [re.search(r"<code>(vpn://[^<]+)</code>", t) for t in texts]
    return [m.group(1) for m in found if m]


def test_vpn_link_arrives_in_chat_and_imports(bot, tg, store):
    from awgbot import vpnlink
    approve_flow(bot, tg)
    links = _extract_link(tg.texts_for(100))
    assert len(links) == 1
    envelope = vpnlink.decode_vpn_link(links[0])
    last = vpnlink.decode_last_config(envelope)
    assert envelope["containers"][0]["container"] == "amnezia-awg"
    assert last["client_ip"] == "10.8.0.2/32"
    assert last["server_pub_key"] == "c2VydmVyLXB1YmxpYy1rZXktMzJieXRlcy1wYWRkZWQ="
    assert last["allowed_ips"] == ["0.0.0.0/0", "::/0"]
    assert last["Jc"] == "4" and last["H1"] == "1234567"      # obfuscation survives
    assert last["isObfuscationEnabled"] is True
    assert last["port"] == 51820
    # the .conf is still delivered next to it
    assert len(tg.documents) == 1


def test_link_is_resent_by_key_command(bot, tg, store):
    from tests.conftest import message
    approve_flow(bot, tg)
    tg.messages = []
    tg.documents = []
    bot.handle(message(100, "/key"))
    assert len(_extract_link(tg.texts_for(100))) == 1
    assert len(tg.documents) == 1
    assert len(store.active_peers(100)) == 1


def test_link_can_be_switched_off(cfg, bot, tg, store):
    from tests.conftest import message
    cfg.send_vpn_link = False
    approve_flow(bot, tg)
    assert _extract_link(tg.texts_for(100)) == []
    assert len(tg.documents) == 1


def test_file_can_be_switched_off(cfg, bot, tg):
    cfg.send_conf_file = False
    approve_flow(bot, tg)
    assert len(_extract_link(tg.texts_for(100))) == 1
    assert tg.documents == []
